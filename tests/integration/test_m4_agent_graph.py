"""Integration tests for the first bounded LangGraph workflow."""

from collections.abc import Iterable
from pathlib import Path

from ai_data_enrichment.capabilities.definitions import get_builtin_capabilities
from ai_data_enrichment.capabilities.registry import CapabilityRegistry
from ai_data_enrichment.domain.dataset_profile import (
    ColumnProfile,
    DatasetProfile,
    PrimitiveType,
)
from ai_data_enrichment.domain.enrichment_plan import (
    ColumnReference,
    EnrichmentPlan,
    OutputMapping,
    PlanStep,
    StepOutputReference,
)
from ai_data_enrichment.execution.job_models import (
    InputRecord,
    JobExecutionMetrics,
    JobExecutionResult,
    RecordExecution,
)
from ai_data_enrichment.execution.models import (
    OperationResult,
    Quantity,
    RowExecutionResult,
    StepExecutionResult,
)
from ai_data_enrichment.orchestration.agent.execution_journal import ExecutionJournal
from ai_data_enrichment.orchestration.agent.graph import build_agent_graph
from ai_data_enrichment.orchestration.agent.state import AgentState
from ai_data_enrichment.planning.llm_planner import PlanGenerationResult
from ai_data_enrichment.planning.models import PlanningResponse
from ai_data_enrichment.validation.models import ValidationResult


def make_registry() -> CapabilityRegistry:
    """Register the production capability contracts."""
    registry = CapabilityRegistry()
    for capability in get_builtin_capabilities():
        registry.register(capability)
    return registry


def make_profile() -> DatasetProfile:
    """Create a small dataset profile for capital distances."""
    return DatasetProfile(
        dataset_id="capitals",
        name="Capital distances",
        row_count=1,
        columns=[
            ColumnProfile(
                name=name,
                inferred_type=PrimitiveType.TEXT,
                null_count=0,
            )
            for name in ("City A", "Country A", "City B", "Country B")
        ],
    )


def make_plan() -> EnrichmentPlan:
    """Build a valid typed plan without an LLM."""
    return EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="origin",
                capability="geocode",
                inputs={
                    "city": ColumnReference(kind="column", name="City A"),
                    "country": ColumnReference(kind="column", name="Country A"),
                },
                parameters={},
            ),
            PlanStep(
                id="destination",
                capability="geocode",
                inputs={
                    "city": ColumnReference(kind="column", name="City B"),
                    "country": ColumnReference(kind="column", name="Country B"),
                },
                parameters={},
            ),
            PlanStep(
                id="distance",
                capability="geodesic_distance",
                inputs={
                    "point_a": StepOutputReference(
                        kind="step_output",
                        step_id="origin",
                    ),
                    "point_b": StepOutputReference(
                        kind="step_output",
                        step_id="destination",
                    ),
                },
                parameters={"unit": "km"},
            ),
        ],
        outputs=[
            OutputMapping(
                step_id="distance",
                value_field="value",
                target_column="distance_km",
                expected_unit="km",
            )
        ],
        assumptions=[],
    )


def make_records() -> tuple[InputRecord, ...]:
    """Provide normalized input without opening Excel."""
    return (
        InputRecord(
            record_id="Capitals!2",
            values={
                "City A": "Berlin",
                "Country A": "Germany",
                "City B": "Kyiv",
                "Country B": "Ukraine",
            },
        ),
    )


class FakeExecutor:
    """Record graph execution calls without running providers."""

    def __init__(self) -> None:
        self.calls = 0

    def execute(
        self,
        plan: EnrichmentPlan,
        records: Iterable[InputRecord],
        *,
        job_id: str | None = None,
        previous_job_id: str | None = None,
    ) -> JobExecutionResult:
        self.calls += 1

        assert len(plan.steps) == 3
        assert job_id is None
        assert previous_job_id is None

        source_records = tuple(records)
        assert len(source_records) == 1

        # A valid, minimal execution result satisfying the existing
        # JobExecutionResult and row/metric consistency contracts.
        return JobExecutionResult(
            records=(
                RecordExecution(
                    record_id=source_records[0].record_id,
                    row_result=RowExecutionResult(
                        steps=(
                            StepExecutionResult(
                                step_id="distance",
                                result=OperationResult(
                                    status="success",
                                    value=Quantity(
                                        value=1203.99,
                                        unit="km",
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
            metrics=JobExecutionMetrics(
                total_rows=1,
                successful_rows=1,
                logical_operations=1,
                executed_handler_calls=1,
            ),
        )


class FakePlanner:
    """Return a predefined planning decision."""

    def __init__(self, response: PlanningResponse) -> None:
        self.response = response
        self.calls = 0

    def generate(
        self,
        *,
        instruction: str,
        dataset_profile: DatasetProfile,
    ) -> PlanGenerationResult:
        self.calls += 1
        assert instruction
        assert dataset_profile.dataset_id == "capitals"

        return PlanGenerationResult(
            response=self.response,
            validation=ValidationResult(),
        )


class FakeRepairer:
    """Return successive repair proposals and record validation feedback."""

    def __init__(self, proposals: list[EnrichmentPlan]) -> None:
        self.proposals = iter(proposals)
        self.calls = 0
        self.diagnostics: list[list[str]] = []

    def repair(
        self,
        *,
        plan: EnrichmentPlan,
        validation: ValidationResult,
        dataset_profile: DatasetProfile,
        instruction: str | None,
    ) -> EnrichmentPlan:
        self.calls += 1

        assert not validation.valid
        assert dataset_profile.dataset_id == "capitals"

        self.diagnostics.append([error.code for error in validation.errors])

        return next(self.proposals)


def make_invalid_plan() -> EnrichmentPlan:
    """Replace one registered capability with an unsupported operation."""
    plan = make_plan()

    invalid_step = plan.steps[0].model_copy(update={"capability": "unknown_capability"})

    return plan.model_copy(update={"steps": [invalid_step, *plan.steps[1:]]})


def test_valid_plan_reaches_executor_once() -> None:
    """The graph validates a supplied plan before execution."""
    executor = FakeExecutor()
    graph = build_agent_graph(
        registry=make_registry(),
        executor=executor,
    )

    result = graph.invoke(
        {
            "plan": make_plan(),
            "dataset_profile": make_profile(),
            "records": make_records(),
        }
    )

    assert result["status"] == "completed"
    assert result["validation"].valid
    assert result["job_result"].metrics.successful_rows == 1
    assert executor.calls == 1


def test_invalid_plan_never_reaches_executor() -> None:
    """An unregistered capability must be rejected before execution."""
    executor = FakeExecutor()

    invalid_plan = EnrichmentPlan(
        version="1.0",
        steps=[
            PlanStep(
                id="unknown",
                capability="unregistered_operation",
                inputs={},
                parameters={},
            )
        ],
        outputs=[
            OutputMapping(
                step_id="unknown",
                value_field="value",
                target_column="result",
            )
        ],
        assumptions=[],
    )

    graph = build_agent_graph(
        registry=make_registry(),
        executor=executor,
    )

    result = graph.invoke(
        {
            "plan": invalid_plan,
            "dataset_profile": make_profile(),
            "records": make_records(),
        }
    )

    assert result["status"] == "rejected"
    assert result["validation"].valid is False
    assert any(error.code == "UNSUPPORTED_CAPABILITY" for error in result["validation"].errors)
    assert executor.calls == 0


def test_planner_can_supply_missing_plan() -> None:
    """A ready planner response is validated and executed."""
    executor = FakeExecutor()
    planner = FakePlanner(
        PlanningResponse(
            status="ready",
            plan=make_plan(),
            reason=None,
        )
    )

    graph = build_agent_graph(
        registry=make_registry(),
        executor=executor,
        planner=planner,
    )

    result = graph.invoke(
        {
            "instruction": "Calculate distances between capitals.",
            "dataset_profile": make_profile(),
            "records": make_records(),
        }
    )

    assert result["status"] == "completed"
    assert planner.calls == 1
    assert executor.calls == 1


def test_unsupported_request_stops_before_execution() -> None:
    """A planner rejection ends the graph without provider calls."""
    executor = FakeExecutor()
    planner = FakePlanner(
        PlanningResponse(
            status="unsupported",
            plan=None,
            reason="No registered capability supports this request.",
        )
    )

    graph = build_agent_graph(
        registry=make_registry(),
        executor=executor,
        planner=planner,
    )

    result = graph.invoke(
        {
            "instruction": "Perform an unsupported operation.",
            "dataset_profile": make_profile(),
            "records": make_records(),
        }
    )

    assert result["status"] == "rejected"
    assert "No registered capability" in result["error"]
    assert planner.calls == 1
    assert executor.calls == 0


def test_repair_succeeds_on_first_attempt() -> None:
    """One successful repair is revalidated before execution."""
    executor = FakeExecutor()
    repairer = FakeRepairer([make_plan()])

    graph = build_agent_graph(
        registry=make_registry(),
        executor=executor,
        repairer=repairer,
        max_repair_attempts=2,
    )

    result = graph.invoke(
        {
            "plan": make_invalid_plan(),
            "dataset_profile": make_profile(),
            "records": make_records(),
        }
    )

    assert result["status"] == "completed"
    assert result["repair_attempts"] == 1
    assert result["validation"].valid
    assert repairer.calls == 1
    assert executor.calls == 1

    assert "UNSUPPORTED_CAPABILITY" in repairer.diagnostics[0]


def test_repair_succeeds_on_second_attempt() -> None:
    """A second proposal may succeed within the configured budget."""
    executor = FakeExecutor()
    repairer = FakeRepairer(
        [
            make_invalid_plan(),
            make_plan(),
        ]
    )

    graph = build_agent_graph(
        registry=make_registry(),
        executor=executor,
        repairer=repairer,
        max_repair_attempts=2,
    )

    result = graph.invoke(
        {
            "plan": make_invalid_plan(),
            "dataset_profile": make_profile(),
            "records": make_records(),
        }
    )

    assert result["status"] == "completed"
    assert result["repair_attempts"] == 2
    assert result["validation"].valid
    assert repairer.calls == 2
    assert executor.calls == 1


def test_repair_budget_exhausted() -> None:
    """Repeatedly invalid proposals must never reach the executor."""
    executor = FakeExecutor()
    repairer = FakeRepairer(
        [
            make_invalid_plan(),
            make_invalid_plan(),
        ]
    )

    graph = build_agent_graph(
        registry=make_registry(),
        executor=executor,
        repairer=repairer,
        max_repair_attempts=2,
    )

    result = graph.invoke(
        {
            "plan": make_invalid_plan(),
            "dataset_profile": make_profile(),
            "records": make_records(),
        }
    )

    assert result["status"] == "rejected"
    assert result["repair_attempts"] == 2
    assert not result["validation"].valid
    assert repairer.calls == 2
    assert executor.calls == 0


def test_malformed_repair_is_rejected() -> None:
    """A repair service failure must not bypass validation."""
    executor = FakeExecutor()

    class InvalidRepairer:
        def repair(
            self,
            *,
            plan: EnrichmentPlan,
            validation: ValidationResult,
            dataset_profile: DatasetProfile,
            instruction: str | None,
        ) -> EnrichmentPlan:
            raise ValueError("Invalid repaired plan")

    graph = build_agent_graph(
        registry=make_registry(),
        executor=executor,
        repairer=InvalidRepairer(),
        max_repair_attempts=2,
    )

    result = graph.invoke(
        {
            "plan": make_invalid_plan(),
            "dataset_profile": make_profile(),
            "records": make_records(),
        }
    )

    assert result["status"] == "rejected"
    assert result["repair_attempts"] == 1
    assert "invalid proposal" in result["error"]
    assert executor.calls == 0


def test_journal_first_execution_and_saved_result(tmp_path: Path) -> None:
    """The first claim executes the job and persists its result."""
    journal = ExecutionJournal(tmp_path / "journal.sqlite")
    executor = FakeExecutor()
    graph = build_agent_graph(
        registry=make_registry(),
        executor=executor,
        journal=journal,
    )

    result = graph.invoke(
        {
            "execution_id": "job-001",
            "plan": make_plan(),
            "dataset_profile": make_profile(),
            "records": make_records(),
        }
    )

    assert result["status"] == "completed"
    assert executor.calls == 1
    assert journal.load_result(execution_id="job-001") == result["job_result"]


def test_journal_restores_without_reexecuting(tmp_path: Path) -> None:
    """A new graph instance recovers the result from the same SQLite file."""
    path = tmp_path / "journal.sqlite"
    executor = FakeExecutor()
    inputs: AgentState = {
        "execution_id": "job-001",
        "plan": make_plan(),
        "dataset_profile": make_profile(),
        "records": make_records(),
    }

    first_graph = build_agent_graph(
        registry=make_registry(),
        executor=executor,
        journal=ExecutionJournal(path),
    )
    first = first_graph.invoke(inputs)

    second_graph = build_agent_graph(
        registry=make_registry(),
        executor=executor,
        journal=ExecutionJournal(path),
    )
    second = second_graph.invoke(inputs)

    assert first["status"] == "completed"
    assert second["status"] == "completed"
    assert second["job_result"] == first["job_result"]
    assert executor.calls == 1


def test_journal_started_execution_blocks_retry(tmp_path: Path) -> None:
    """An interrupted execution cannot be automatically repeated."""
    path = tmp_path / "journal.sqlite"

    class FailingExecutor(FakeExecutor):
        def execute(self, plan, records, *, job_id=None, previous_job_id=None):
            self.calls += 1
            raise RuntimeError("Simulated interruption")

    failing_executor = FailingExecutor()
    inputs: AgentState = {
        "execution_id": "job-001",
        "plan": make_plan(),
        "dataset_profile": make_profile(),
        "records": make_records(),
    }

    first_graph = build_agent_graph(
        registry=make_registry(),
        executor=failing_executor,
        journal=ExecutionJournal(path),
    )
    first = first_graph.invoke(inputs)

    assert first["status"] == "execution_uncertain"
    assert failing_executor.calls == 1

    replacement_executor = FakeExecutor()
    second_graph = build_agent_graph(
        registry=make_registry(),
        executor=replacement_executor,
        journal=ExecutionJournal(path),
    )
    second = second_graph.invoke(inputs)

    assert second["status"] == "execution_uncertain"
    assert "Automatic retry is prohibited" in second["error"]
    assert replacement_executor.calls == 0


def test_journal_rejects_changed_execution_inputs(tmp_path: Path) -> None:
    """The same execution ID cannot refer to a modified plan."""
    journal = ExecutionJournal(tmp_path / "journal.sqlite")
    executor = FakeExecutor()
    graph = build_agent_graph(
        registry=make_registry(),
        executor=executor,
        journal=journal,
    )

    original_plan = make_plan()
    first = graph.invoke(
        {
            "execution_id": "job-001",
            "plan": original_plan,
            "dataset_profile": make_profile(),
            "records": make_records(),
        }
    )

    changed_plan = original_plan.model_copy(update={"assumptions": ["Use verified source data."]})
    second = graph.invoke(
        {
            "execution_id": "job-001",
            "plan": changed_plan,
            "dataset_profile": make_profile(),
            "records": make_records(),
        }
    )

    assert first["status"] == "completed"
    assert second["status"] == "failed"
    assert "different request" in second["error"]
    assert executor.calls == 1


def test_journal_requires_execution_identity(tmp_path: Path) -> None:
    """Missing execution identity must fail before claiming the job."""
    journal = ExecutionJournal(tmp_path / "journal.sqlite")
    executor = FakeExecutor()
    graph = build_agent_graph(
        registry=make_registry(),
        executor=executor,
        journal=journal,
    )

    result = graph.invoke(
        {
            "plan": make_plan(),
            "dataset_profile": make_profile(),
            "records": make_records(),
        }
    )

    assert result["status"] == "failed"
    assert "execution ID" in result["error"]
    assert executor.calls == 0


def test_graph_exposes_node_transition_trace() -> None:
    """LangGraph exposes ordered node updates without extra infrastructure."""

    executor = FakeExecutor()
    graph = build_agent_graph(
        registry=make_registry(),
        executor=executor,
    )

    events = list(
        graph.stream(
            {
                "plan": make_plan(),
                "dataset_profile": make_profile(),
                "records": make_records(),
            },
            stream_mode="updates",
        )
    )

    node_names = [node_name for event in events for node_name in event]

    assert node_names == [
        "obtain_plan",
        "validate",
        "execute",
    ]

    assert events[-1]["execute"]["status"] == "completed"
    assert executor.calls == 1


def test_graph_trace_includes_bounded_repair_cycle() -> None:
    """Trace validation failure, repair, revalidation and execution."""

    executor = FakeExecutor()
    repairer = FakeRepairer([make_plan()])

    graph = build_agent_graph(
        registry=make_registry(),
        executor=executor,
        repairer=repairer,
        max_repair_attempts=2,
    )

    events = list(
        graph.stream(
            {
                "plan": make_invalid_plan(),
                "dataset_profile": make_profile(),
                "records": make_records(),
                "instruction": "Calculate distances between capitals.",
            },
            stream_mode="updates",
        )
    )

    node_names = [node_name for event in events for node_name in event]

    assert node_names == [
        "obtain_plan",
        "validate",
        "repair_plan",
        "validate",
        "execute",
    ]

    first_validation = events[1]["validate"]
    assert first_validation["status"] == "rejected"
    assert not first_validation["validation"].valid
    assert any(
        diagnostic.code == "UNSUPPORTED_CAPABILITY"
        for diagnostic in first_validation["validation"].errors
    )

    repair_update = events[2]["repair_plan"]
    assert repair_update["repair_attempts"] == 1

    second_validation = events[3]["validate"]
    assert second_validation["validation"].valid

    assert events[-1]["execute"]["status"] == "completed"
    assert executor.calls == 1
    assert repairer.calls == 1
