# AI Data Enrichment Studio
## Conceptual System Architecture

**Document ID:** ARCH-001  
**Version:** 0.1  
**Status:** Proposed  
**Architecture style:** Modular Monolith with Agentic Orchestration  
**Related document:** [Product Requirements Specification](01-requirements.md)

---

## 1. Architecture Overview

### 1.1 Purpose

This document defines the conceptual architecture of AI Data Enrichment Studio, including its major components, responsibilities, communication boundaries, and execution principles.

The architecture supports the initial Excel enrichment use cases while providing a foundation for future data enrichment and analytical capabilities.

This document focuses on component-level design. Detailed domain contracts, LangGraph state schemas, provider interfaces, and deployment specifications will be defined separately.

### 1.2 Architectural Approach

The system follows a hybrid architecture combining:

- **Agentic orchestration** for task understanding, planning, conditional routing, and recovery decisions.
- **Deterministic execution** for structured data retrieval, calculations, validation, and bulk processing.
- **Provider-based integration** for accessing external data sources.
- **Evidence-driven validation** for producing traceable and trustworthy results.
- **Independent evaluation** for measuring accuracy, reliability, performance, and cost.

The initial implementation follows a modular monolith architecture.

The components execute within a single Python application but are organized into independently testable modules with explicit responsibilities and interfaces.

### 1.3 Key Architectural Decision

LangGraph orchestrates enrichment jobs at the workflow level.

The deterministic execution engine processes individual records and batches using registered capabilities and providers.

LLMs are not responsible for directly processing every record when conventional software can perform the required operations.

This separation is intended to improve accuracy, reproducibility, scalability, and cost efficiency.

---

## 2. System Context

### 2.1 External Actors

**User**

Provides an input dataset and natural-language enrichment instructions.

The user may inspect plans, review uncertain results, and download enriched datasets and reports.

**External Data Providers**

Supply structured or unstructured information required for enrichment.

Examples include Wikidata, geocoding APIs, research organization registries, and other approved sources.

**LLM Provider**

Supplies language-model capabilities for semantic task interpretation, plan generation, and selected ambiguity-resolution or recovery operations.

**Evaluation Framework**

Independently evaluates system outputs against controlled datasets and reference information.

### 2.2 System Inputs

Each enrichment job may receive:

- Input dataset or file reference.
- Natural-language user request.
- Optional worksheet and column selections.
- Execution configuration.
- Provider and model configuration.
- Resource and cost limits.

### 2.3 System Outputs

A job produces:

- Enriched dataset artifact.
- Structured execution manifest.
- Per-record processing outcomes.
- Provenance information.
- Execution statistics.
- Diagnostic information.

The evaluation subsystem may additionally produce independent quality reports.

---

## 3. High-Level Components

### 3.1 Interface Layer

**Responsibility:** Provide entry points for creating and managing enrichment jobs.

Initial implementation:

- Command-line interface.

Portfolio v1:

- REST API.
- Web user interface.
- Job progress and history.
- Plan review and approval.
- Results inspection and export.

All interfaces must invoke the same application services and core enrichment logic.

### 3.2 Data Ingestion and Profiling

**Responsibility:** Convert supported input files into a normalized dataset representation.

Main operations:

- Validate input files.
- Inspect workbook structure.
- Identify worksheets and columns.
- Determine basic data types.
- Analyze missing values.
- Identify candidate target fields.
- Build a dataset profile.
- Preserve source row and worksheet references.

The output of this component must not depend on the internal representation used by the Excel-processing library.

### 3.3 Task Interpretation and Planning

**Responsibility:** Translate a natural-language request into a structured enrichment plan.

The planner receives:

- User request.
- Dataset profile.
- Available capability descriptions.
- Applicable constraints and policies.

The planner produces an EnrichmentPlan describing:

- Target attributes.
- Input field mappings.
- Required operations.
- Dependencies between operations.
- Expected output types and units.
- Validation requirements.
- Potential alternative strategies.

The planner must select only supported capabilities.

It must not generate arbitrary executable Python or shell code.

### 3.4 Plan Validation

**Responsibility:** Verify that a proposed plan is executable, structurally valid, and consistent with system policies.

Validation includes:

- Plan schema validation.
- Input column existence.
- Capability availability.
- Operation dependency consistency.
- Type and unit compatibility.
- Target-field protection.
- Detection of unsupported operations.
- Resource and execution policy checks.

Invalid plans may be returned for bounded correction or rejected with diagnostic information.

### 3.5 Agentic Orchestration

**Responsibility:** Coordinate the lifecycle of an enrichment job.

LangGraph manages:

- Workflow transitions.
- Structured plan generation.
- Plan validation decisions.
- Execution initiation.
- Assessment of execution outcomes.
- Conditional recovery and replanning.
- Workflow termination.
- Optional human-in-the-loop interactions.

The orchestrator should operate at the job level rather than managing each row as an independent LLM interaction.

Its decisions must be bounded by explicit execution policies.

### 3.6 Execution Engine

**Responsibility:** Execute validated enrichment plans over normalized datasets.

Main operations:

- Resolve operation dependencies.
- Group equivalent requests.
- Deduplicate entities and lookups.
- Execute local calculations.
- Invoke appropriate capabilities.
- Select approved providers.
- Coordinate batching and bounded concurrency.
- Apply caching and rate limits.
- Handle recoverable errors.
- Produce structured processing results.

The execution engine must not depend on LangGraph-specific state objects or Excel-specific worksheet objects.

### 3.7 Capability Registry

**Responsibility:** Describe the operations supported by the enrichment engine.

Initial candidate capabilities:

- Entity resolution.
- Entity property lookup.
- Geocoding.
- Geodesic distance calculation.
- Unit conversion.
- Basic data normalization.

Possible future capabilities:

- Knowledge graph queries.
- Structured content extraction.
- Search-based research.
- Cross-source comparison.
- Analytical aggregation.

Each capability defines a logical operation and its input/output expectations.

Capabilities should be composable into multi-step execution plans.

### 3.8 Provider Registry

**Responsibility:** Manage implementations and external integrations used to execute capabilities.

Providers may include:

- Structured external APIs.
- Local Python implementations.
- Local reference datasets.
- Approved search services.
- LLM-based extraction services.

A capability may have one or multiple providers.

Provider selection should consider:

- Operation compatibility.
- Source suitability.
- Reliability.
- Availability.
- Rate limits.
- Estimated cost.
- Cache availability.
- Configured fallback policies.

The planner expresses the required capability. The execution engine applies approved provider-selection policies.

LLM-generated plans must not introduce arbitrary network destinations or unregistered providers.

### 3.9 Validation and Evidence

**Responsibility:** Assess the correctness, consistency, and traceability of retrieved or calculated values.

Validation may include:

- Data type checks.
- Unit checks.
- Domain-specific range checks.
- Entity match verification.
- Source metadata validation.
- Conflict detection.
- Independent calculation checks.

Different operations require different verification strategies.

For example:

- Geodesic distance: input coordinate validity and deterministic calculation.
- Mountain elevation: entity identity and evidence from suitable reference sources.
- Organization lookup: identifier matching and agreement with contextual attributes.

A source citation alone does not guarantee factual correctness.

The system must distinguish successful retrieval from sufficiently verified information.

Unsupported results must not be silently treated as verified facts.

### 3.10 Provenance and Diagnostics

**Responsibility:** Record how results were produced.

Provenance may include:

- Input record reference.
- Selected capability.
- Selected provider.
- Source identifier or URL.
- Retrieval timestamp.
- Transformation or calculation method.
- Validation outcomes.
- Cache usage.
- Relevant model and configuration versions.

Diagnostics include:

- API errors.
- Missing records.
- Ambiguous matches.
- Validation failures.
- Retry attempts.
- Execution limits.

Detailed provenance is stored separately from the main output values unless explicitly requested in the output format.

### 3.11 Output Adapters

**Responsibility:** Convert normalized enrichment results into user-facing artifacts.

The initial output adapter generates Excel files.

It must:

- Open the original workbook as the basis for output.
- Apply only explicitly authorized cell modifications.
- Preserve unrelated source data and supported workbook features.
- Avoid overwriting existing values without authorization.
- Generate a separate output file.

An execution manifest is produced independently of the enriched workbook.

Future adapters may support CSV, JSON, and database outputs.

### 3.12 Independent Evaluation Framework

**Responsibility:** Evaluate system quality without influencing the enrichment process.

The evaluation framework measures:

- Planning correctness.
- Entity resolution quality.
- Enrichment accuracy.
- Coverage.
- Workbook preservation.
- Error handling.
- Provider usage.
- Cache effectiveness.
- Latency.
- Estimated execution cost.

Evaluation datasets and reference answers must remain logically independent of runtime provider results.

Evaluation is not implemented as an LLM node in the production enrichment graph.

---

## 4. Conceptual Execution Workflow

### 4.1 Standard Job Lifecycle

1. Receive the dataset and user request.
2. Validate and profile the input dataset.
3. Generate a structured enrichment plan.
4. Validate the plan against capabilities and policies.
5. Return an actionable status when clarification or correction is required.
6. Execute the validated plan using registered capabilities and providers.
7. Validate the produced results.
8. Attempt bounded recovery for eligible unresolved operations.
9. Generate the enriched dataset and execution manifest.
10. Record final job status and execution statistics.

### 4.2 Conditional Execution

The orchestration workflow must support conditional decisions.

Examples:

**Invalid plan**

Attempt a bounded plan correction when the problem is recoverable. Otherwise stop with an explanatory status.

**Missing provider result**

Try an approved alternative provider if one is available and permitted.

**Ambiguous entity**

Apply additional deterministic matching rules. If uncertainty remains, mark the record for review.

**External API failure**

Apply bounded retries for transient failures. Continue unaffected operations where possible.

**Unrecoverable result**

Record the failure without generating an unsupported replacement value.

**Unacceptable plan cost**

Reject the plan or require explicit authorization rather than exceeding configured limits.

### 4.3 Agentic Behavior

The MVP must demonstrate more than a fixed sequence of unrelated functions.

Agentic behavior includes:

- Interpreting different natural-language requests.
- Constructing multi-step plans from available capabilities.
- Selecting operation strategies.
- Routing execution based on intermediate outcomes.
- Revising plans when supported alternatives exist.
- Terminating safely when a reliable answer cannot be obtained.

Recovery loops must have explicit iteration, request, and cost limits.

---

## 5. Data and Control Flow

The architecture distinguishes four logical data flows.

### 5.1 Control Flow

Managed by the LangGraph orchestrator.

Includes job status, plan status, workflow transitions, and recovery decisions.

### 5.2 Dataset Flow

Managed by the ingestion layer and execution engine.

Includes normalized input records, intermediate values, and enrichment results.

Large datasets should not be repeatedly copied into workflow checkpoints.

### 5.3 Provider Flow

Managed by the execution engine and provider adapters.

Includes external requests, responses, caching, throttling, and provider errors.

### 5.4 Evidence Flow

Managed by validation and provenance services.

Includes source references, calculation details, validation outcomes, and record-level diagnostics.

These flows must remain logically separated even when implemented within one application process.

---

## 6. State and Persistence Boundaries

### 6.1 Workflow State

LangGraph workflow state represents the current execution stage and relevant control information.

Conceptual fields include:

- Job identifier.
- Input artifact reference.
- Dataset profile reference or summary.
- Current enrichment plan.
- Plan validation outcome.
- Workflow phase.
- Execution summary.
- Recovery counters.
- Error summary.
- Output artifact references.

The precise schema will be defined in the Domain Model and Technical Design documents.

### 6.2 Execution Data

The execution engine maintains or accesses:

- Normalized records.
- Intermediate operation results.
- Entity resolution results.
- Provider responses.
- Row-level outcomes.
- Detailed provenance.

Large execution datasets should not be stored directly in every LangGraph checkpoint.

### 6.3 Persistent Storage

Core MVP may use local files and lightweight local persistence for execution artifacts and configuration.

Portfolio v1 is expected to introduce persistent job metadata, artifact storage, and durable workflow state.

Persistence technology will be selected during detailed technical design.

### 6.4 Idempotency and Recovery

Operations with side effects must be designed to tolerate retries and workflow resumption.

Repeated execution should not unintentionally overwrite protected data, duplicate results, or perform avoidable external requests.

Checkpointing must not be assumed to guarantee exactly-once execution of external API calls.

---

## 7. MVP Reference Workflows

### 7.1 Mountain Elevation

Input fields:

- Mountain.
- Country.

Target field:

- height.

Conceptual plan:

1. Resolve the mountain entity using its name and country context.
2. Retrieve the elevation property from a suitable structured source.
3. Normalize the result to meters.
4. Validate the entity match, value, and source.
5. Store the result and provenance.
6. Write accepted values into the output workbook.

### 7.2 Distance Between Capitals

Input fields:

- Capital_From.
- Country_From.
- Capital_To.
- Country_To.

Target field:

- distance.

Conceptual plan:

1. Resolve both capital locations using country context.
2. Obtain or retrieve their geographic coordinates.
3. Deduplicate repeated location lookups.
4. Calculate the straight-line distance using an explicitly documented geodesic method.
5. Normalize the result to kilometers.
6. Validate the calculation inputs and result.
7. Store the result and provenance.
8. Write accepted values into the output workbook.

The two scenarios must use the same planning and execution infrastructure.

Differences must be expressed through data mappings, generated plans, and reusable capabilities rather than filename-specific application code.

### 7.3 Research Organization Enrichment — Portfolio v1

Input fields may include:

- Organization.
- Country.
- City.

Target attributes may include:

- Official website.
- Headquarters location.
- Organization identifier.

Conceptual plan:

1. Resolve organization identity.
2. Retrieve attributes from appropriate structured sources.
3. Reconcile contextual attributes and available identifiers.
4. Detect ambiguous or conflicting matches.
5. Validate and record the results.
6. Route uncertain records to a review workflow.
7. Generate the enriched dataset and report.

This workflow is not a Core MVP acceptance dependency.

---

## 8. Error Handling Strategy

Errors must be classified at the appropriate level.

**Input errors:** Unsupported files, malformed workbooks, missing required fields.

**Planning errors:** Invalid plans, unsupported capabilities, ambiguous instructions.

**Provider errors:** Timeouts, rate limits, authentication errors, unavailable services, malformed responses.

**Data errors:** Missing entities, conflicting sources, invalid values, ambiguous matches.

**Execution errors:** Resource limits, exhausted retry budgets, unexpected internal failures.

A recoverable record-level error must not automatically cause the entire job to fail.

The system must support partial results and provide explicit outcomes for unresolved records.

No result may be fabricated solely to satisfy output completeness.

---

## 9. Scalability and Cost Strategy

The Core MVP targets datasets containing at least 1,000 records.

The execution engine should optimize processing through:

- Input normalization.
- Request deduplication.
- Entity-level caching.
- Intermediate result reuse.
- Provider-supported batching.
- Bounded concurrency.
- Rate-limit compliance.
- Deterministic local calculations.
- Selective LLM usage.
- Bounded fallback strategies.

LLM planning should operate primarily at the dataset/job level.

Per-record LLM operations should only be introduced where semantically necessary and explicitly controlled.

Performance and cost claims must be validated with actual benchmark measurements.

---

## 10. Security Boundaries

Uploaded datasets and externally retrieved content are untrusted inputs.

The architecture must enforce the following boundaries:

- External text must not override system instructions.
- LLM-generated plans must be structurally and semantically validated.
- Only registered capabilities may be executed.
- Arbitrary generated code execution is prohibited.
- Provider destinations and credentials must be controlled.
- Sensitive content must not be transmitted unnecessarily.
- Execution limits must be enforced independently of model instructions.
- Source file integrity must be protected.

Additional controls will be required before public multi-user deployment.

---

## 11. Deployment Evolution

### Core MVP

The initial system is a Python application with:

- CLI entry point.
- LangGraph orchestration.
- Local execution engine.
- Registered capabilities and providers.
- Local artifacts and execution manifests.
- Automated tests and evaluation tools.

No distributed infrastructure is required.

### Portfolio v1

The application evolves into a deployable service with:

- Web frontend.
- FastAPI backend.
- Background job worker.
- Persistent job storage.
- Durable execution state.
- Artifact storage.
- Monitoring and tracing.
- Containerized deployment.
- CI/CD integration.

The architecture should support this evolution without rewriting core enrichment logic.

---

## 12. Architectural Constraints

The following constraints are accepted:

1. Core enrichment logic must remain independent of file formats.
2. Agent orchestration must remain separate from bulk execution.
3. Capabilities must be explicitly registered and validated.
4. Providers must be replaceable through defined interfaces.
5. Source provenance must be preserved.
6. Missing information must remain explicitly unresolved.
7. External operations must respect configurable execution limits.
8. Evaluation must remain independent of production enrichment.
9. New use cases should reuse existing capabilities wherever possible.
10. Future modules must not dictate unnecessary MVP infrastructure complexity.

---

## 13. Open Architectural Questions

The following decisions remain open:

- Exact EnrichmentPlan schema and operation dependency representation.
- Capability interface and input/output contracts.
- Provider selection and fallback policies.
- Internal representation of normalized datasets.
- RowResult and provenance schemas.
- Separation of execution state and workflow state.
- LangGraph node boundaries and checkpoint strategy.
- Caching architecture and invalidation.
- Entity resolution and verification thresholds.
- Supported Excel preservation guarantees.
- Storage architecture and job persistence.
- UI/API and background processing integration.
- Observability technologies and deployment platform.

These decisions will be addressed in subsequent design documents and Architecture Decision Records.

---

## 14. Next Design Activities

1. Define the domain entities and their relationships.
2. Define conceptual contracts for EnrichmentPlan, Capability, Provider, and RowResult.
3. Identify additional entities required for dataset profiling, execution, and provenance.
4. Walk through the mandatory scenarios using the proposed contracts.
5. Design the LangGraph workflow and state transitions.
6. Record important architectural decisions through ADRs.
7. Begin implementation with a minimal end-to-end workflow.

---

**Document status:** Conceptual architecture proposal.  
**Next document:** `docs/03-domain-model.md`.