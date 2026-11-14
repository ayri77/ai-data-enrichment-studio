"""Look a row's value up under another name the user gave for it.

A name in a workbook can be one the source does not know: another language, a
historical spelling, a typo. During review the user may say what to search for
instead. Nothing else changes: the same source is asked, the same checks and
the same decision policy apply, and the row's country still constrains the
match. The audit keeps both names, so a value found this way is never mistaken
for one found under the row's own text.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

from ai_data_enrichment.capabilities.bindings import binding_for, find_binding, name_resolution_for
from ai_data_enrichment.domain.enrichment_plan import PlanStep
from ai_data_enrichment.execution.models import OperationResult, RuntimeValue
from ai_data_enrichment.resolution.job import ParameterizedResolutionService, ResolutionService
from ai_data_enrichment.resolution.models import (
    ResolutionPolicy,
    ResolutionQuery,
    ResolutionReport,
)

OtherNameCapability = Literal["geocode", "resolve_entity"]


@dataclass(frozen=True)
class OtherNameOutcome:
    """The source's result for the given name, audited under the row's value."""

    operation: OperationResult
    report: ResolutionReport


def _normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def same_name(value: str, recorded: str) -> bool:
    """Compare a cell with the name an audit recorded for it.

    An over-long cell is recorded shortened with an ellipsis; it matches by its
    beginning.
    """
    value, recorded = _normalized(value), _normalized(recorded)
    if recorded.endswith("…"):
        return value.startswith(recorded[:-1])
    return value == recorded


def supports_other_name(step: PlanStep) -> bool:
    """Whether a step is one of the audited lookups a name can be given for."""
    binding = find_binding(step.capability)
    resolution = binding.name_resolution if binding else None
    return resolution is not None and step.parameters in resolution.approved_parameters


def name_input(capability: str) -> str:
    return name_resolution_for(capability).name_input


def searched_under(
    report: ResolutionReport, *, row_name: str, searched_as: str
) -> ResolutionReport:
    """Record that the source was searched for another name than the row's."""
    query = report.candidate_set.query.model_copy(
        update={"name": row_name, "searched_as": searched_as}
    )
    candidate_set = report.candidate_set.model_copy(update={"query": query})
    # Rebuilt through validation: the audit model stays the only gate.
    return ResolutionReport.model_validate(
        report.model_copy(update={"candidate_set": candidate_set}).model_dump()
    )


class OtherNameResolutionService:
    """Resolve the rows of one reviewed operation under a user-given name.

    One instance serves every row of that operation, so the source is searched
    once for the name however many rows share it. A source failure is not kept:
    the next row asks again.
    """

    def __init__(
        self,
        service: ResolutionService,
        *,
        capability: OtherNameCapability,
        row_name: str,
        searched_as: str,
    ) -> None:
        if not row_name.strip() or not searched_as.strip():
            raise ValueError("Both the row's name and the name to search for are required.")
        self._service = service
        self._capability = capability
        resolution = name_resolution_for(capability)
        self._input = resolution.name_input
        self._provider_id = binding_for(capability).source_id
        self._parameters = resolution.approved_parameters
        self._takes_parameters = resolution.service_takes_parameters
        self._row_name = row_name
        self._searched_as = " ".join(searched_as.split())
        self._found: dict[tuple[tuple[str, str], ...], OtherNameOutcome] = {}

    def validate_step(self, step: PlanStep, *, provider_id: str) -> None:
        if (
            step.capability != self._capability
            or provider_id != self._provider_id
            or step.parameters not in self._parameters
        ):
            raise ValueError("Another name requires the same audited lookup step.")

    def validate_inputs(self, inputs: Mapping[str, RuntimeValue]) -> None:
        """The row must still hold the value the user gave another name for."""
        value = inputs.get(self._input)
        if not isinstance(value, str) or not same_name(value, self._row_name):
            raise ValueError("The row's value differs from the reviewed one.")

    def resolve(
        self,
        *,
        inputs: Mapping[str, RuntimeValue],
        policy: ResolutionPolicy,
        parameters: Mapping[str, str] | None = None,
    ) -> OtherNameOutcome:
        self.validate_inputs(inputs)
        key = tuple(sorted((name, repr(value)) for name, value in inputs.items())) + tuple(
            sorted((parameters or {}).items())
        )
        found = self._found.get(key)
        if found is not None:
            return found
        lookup = {**inputs, self._input: self._searched_as}
        if self._takes_parameters:
            outcome = cast(ParameterizedResolutionService, self._service).resolve(
                inputs=lookup, policy=policy, parameters=parameters
            )
        else:
            outcome = self._service.resolve(inputs=lookup, policy=policy)
        marked = OtherNameOutcome(
            operation=outcome.operation,
            report=searched_under(
                outcome.report, row_name=self._row_name, searched_as=self._searched_as
            ),
        )
        if marked.report.candidate_set.provider_error_code is None:
            self._found[key] = marked
        return marked


def search_under(
    service: ResolutionService,
    policy: ResolutionPolicy,
    *,
    capability: OtherNameCapability,
    query: ResolutionQuery,
    searched_as: str,
) -> ResolutionReport:
    """Search once for another name, outside a job, as a reviewed query asked.

    The inputs are rebuilt from the audited query: the row's name, its
    recognized country and, for a city, its area and required role. The result
    is the same report a job would produce for that name.
    """
    context = {attribute.name: attribute.value for attribute in query.context}
    country = context.get("country_code") or context.get("country_codes") or context.get("country")
    if not isinstance(country, str):
        raise ValueError("The reviewed query has no country to search in.")
    inputs: dict[str, RuntimeValue] = {name_input(capability): query.name, "country": country}
    parameters: dict[str, str] = {}
    resolution = name_resolution_for(capability)
    for name in resolution.context_inputs:
        value = context.get(name)
        if isinstance(value, str):
            inputs[name] = value
    for name in resolution.context_parameters:
        value = context.get(name)
        if isinstance(value, str):
            parameters[name] = value
    renamed = OtherNameResolutionService(
        service, capability=capability, row_name=query.name, searched_as=searched_as
    )
    return renamed.resolve(inputs=inputs, policy=policy, parameters=parameters).report
