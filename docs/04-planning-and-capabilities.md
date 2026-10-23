# AI Data Enrichment Studio
## Planning and Capability Contracts

**Document ID:** DESIGN-001  
**Version:** 0.1  
**Status:** Proposed — Initial Implementation Baseline  
**Related documents:**
- [Product Requirements](01-requirements.md)
- [Conceptual Architecture](02-architecture.md)
- [Domain Model](03-domain-model.md)

---

## 1. Purpose

This document defines the initial technical contracts for the planning subsystem of AI Data Enrichment Studio.

The primary objective is to enable an LLM to generate executable enrichment plans by composing registered operations, while ensuring that the resulting plans can be independently validated and safely executed by conventional Python components.

The initial design focuses on two mandatory scenarios:

1. Retrieving mountain elevations.
2. Calculating straight-line distances between capitals.

The contracts should support future extensions without attempting to solve every possible data enrichment problem in the initial implementation.

### 1.1 Design Principle

**Design for extension, implement for current needs.**

The system should expose clear extension points while implementing only the functionality required by the current milestones.

Architectural flexibility must not introduce unnecessary runtime complexity.

---

## 2. Planning Architecture

### 2.1 Separation of Responsibilities

The planning subsystem consists of four conceptual components.

**Capability Registry**

Maintains definitions of available enrichment operations, including their input/output contracts and supported parameters.

**LLM Planner**

Interprets the user request and dataset profile, selects available capabilities, and constructs a declarative execution plan.

**Plan Validator**

Performs deterministic validation of the generated plan, including structural correctness, dependency resolution, type compatibility, and execution restrictions.

**Execution Engine**

Receives a validated plan and executes its operations using registered capability implementations and providers.

### 2.2 Planning Workflow

```text
User Request + Dataset Profile
              |
              v
       Capability Registry
              |
              v
          LLM Planner
              |
              v
        EnrichmentPlan
              |
              v
         Plan Validator
              |
       +------+------+
       |             |
     Valid         Invalid
       |             |
       v             v
  Plan Review    Error Report
       |             |
       |        Optional Replan
       |
       v
  Plan Approval
       |
       v
  Revalidation
       |
       v
  Execution Engine
```

The LLM generates the plan but does not execute arbitrary operations.

The deterministic validator controls whether a plan may be executed.

Invalid plans may be corrected through bounded replanning attempts or explicit user modification.

### 2.3 Planning Strategy

The initial implementation uses a **plan-first execution model**.

The LLM generates a complete declarative plan before data processing begins.

The plan describes operation dependencies as a Directed Acyclic Graph (DAG).

Independent operations may execute concurrently, while dependent operations execute only after their prerequisites become available.

The initial implementation does not require an unrestricted ReAct loop for every dataset row.

---

## 3. Capability Abstraction

### 3.1 Definition

A Capability represents a reusable semantic operation supported by the system.

A Capability describes *what* the system can do, rather than how a particular external API or Python library implements the operation.

Examples include:

- Resolving an entity.
- Retrieving an entity property.
- Geocoding a location.
- Calculating a geodesic distance.

### 3.2 Capability Granularity

The system distinguishes three conceptual abstraction levels.

| Level | Description | Initial Availability |
|---|---|---|
| Internal Primitives | Low-level Python functions and integration utilities | Internal only |
| Semantic Capabilities | Reusable operations exposed to the LLM Planner | Core MVP |
| Scenarios | Reusable, parameterized combinations of capabilities | Future extension |

Semantic Capabilities are the primary building blocks of LLM-generated plans.

Low-level operations such as HTTP requests, JSON parsing, and text normalization remain implementation details.

The initial LLM Planner must not directly invoke unrestricted low-level primitives.

### 3.3 CapabilitySpec

Each registered Capability is described by a structured CapabilitySpec.

Initial conceptual contract:

| Field | Description |
|---|---|
| `id` | Unique capability identifier |
| `version` | Capability contract version |
| `description` | Semantic description of the operation |
| `inputs` | Named input definitions and logical types |
| `parameters` | Supported operation parameters and constraints |
| `output` | Logical output type |
| `execution` | Execution characteristics |
| `validation` | Reference to applicable validation rules |

Capability IDs and input/parameter names must follow lowercase snake_case, start with a letter, contain only ASCII lowercase letters, digits, and single underscores between segments, and have a maximum length of 64 characters. Capability IDs must be unique within the registry.

Example:

```yaml
id: geocode
version: "1.0"

description: >
  Resolve a city and country into geographic coordinates.

inputs:
  city:
    type: Text
    required: true
  country:
    type: Text
    required: true

parameters: {}

output:
  type: GeoPoint

execution:
  cacheable: true
  batch_supported: false

validation:
  policy: geographic_coordinates
```

This is an illustrative contract, not the final serialization format.

The implementation will use Pydantic models to define and validate capability metadata.

`batch_supported` describes native batching supported by the capability implementation or provider. It does not prevent the Execution Engine from performing request deduplication or bounded concurrent execution.

### 3.4 Single Source of Truth

Capability definitions must serve as the authoritative source for:

- Input and output validation.
- Parameter validation.
- LLM-facing capability descriptions.
- Execution Engine compatibility checks.
- Technical documentation.

The system should avoid maintaining separate, inconsistent definitions of the same operation.

Provider-specific implementation details are not part of the LLM-generated plan.

---

## 4. Initial Capability Registry

The Core MVP implements four semantic capabilities.

### 4.1 `resolve_entity`

**Purpose:** Resolve a named entity to a structured entity reference.

**Inputs:**

| Name | Type | Required |
|---|---|---|
| `name` | Text | Yes |
| `country` | Text | Yes |

**Parameters:**

| Name | Type | Allowed Values |
|---|---|---|
| `entity_type` | Text | `mountain` |

**Output:** `EntityRef`

An EntityRef contains a stable identifier and source namespace, with optional contextual metadata.

The implementation must distinguish between successful resolution, ambiguous candidates, and unresolved entities.

**Initial use case:** Identify a mountain using its name and country.

Future versions may support additional entity types.

### 4.2 `lookup_property`

**Purpose:** Retrieve a registered property of an identified entity.

**Inputs:**

| Name | Type | Required |
|---|---|---|
| `entity` | EntityRef | Yes |

**Parameters:**

| Name | Type | Allowed Values |
|---|---|---|
| `property` | Text | `elevation` |

**Output:** `Quantity`

For the initial implementation:

- Only `elevation` is supported.
- The canonical output unit is meters.
- The returned Quantity contains a numeric value and unit.

The provider implementation is responsible for obtaining and normalizing the value.

This restriction allows the initial validator to determine the output type without implementing a general-purpose attribute type system.

### 4.3 `geocode`

**Purpose:** Resolve a geographical location to coordinates.

**Inputs:**

| Name | Type | Required |
|---|---|---|
| `city` | Text | Yes |
| `country` | Text | Yes |

**Parameters:** None in MVP.

**Output:** `GeoPoint`

A GeoPoint contains:

- Latitude.
- Longitude.
- Coordinate reference system.
- Optional source metadata.

The initial coordinate reference system is WGS84.

Ambiguous or unsuccessful lookups must produce explicit operation outcomes rather than arbitrary default coordinates.

### 4.4 `geodesic_distance`

**Purpose:** Calculate the straight-line distance between two geographic points.

**Inputs:**

| Name | Type | Required |
|---|---|---|
| `point_a` | GeoPoint | Yes |
| `point_b` | GeoPoint | Yes |

**Parameters:**

| Name | Type | Allowed Values |
|---|---|---|
| `unit` | Text | `km` |

**Output:** `Quantity`

The initial implementation returns distance in kilometers.

The calculation uses a documented deterministic geodesic algorithm.

Driving distances or transportation routes are outside the scope of this capability.

### 4.5 Capability Registry Summary

| Capability | Input Types | Output Type | Initial Strategy |
|---|---|---|---|
| `resolve_entity` | Text, Text | EntityRef | Structured entity lookup |
| `lookup_property` | EntityRef | Quantity | Structured property retrieval |
| `geocode` | Text, Text | GeoPoint | Geographic lookup |
| `geodesic_distance` | GeoPoint, GeoPoint | Quantity | Local calculation |

The registry may be extended by registering additional capability definitions and implementations.

No scenario-specific capabilities such as `enrich_mountains` or `calculate_capital_distances` will be introduced.

---

## 5. Logical Data Types

The initial contract requires a small semantic type system.

| Type | Description |
|---|---|
| `Text` | String value |
| `Number` | Numeric value without an attached measurement unit |
| `EntityRef` | Reference to a resolved entity |
| `GeoPoint` | Geographic coordinates |
| `Quantity` | Numeric value with an explicit measurement unit |

An initial Quantity representation is conceptually equivalent to:

```json
{
  "value": 8848.86,
  "unit": "m"
}
```

An initial GeoPoint representation is conceptually equivalent to:

```json
{
  "latitude": 50.45,
  "longitude": 30.52,
  "crs": "EPSG:4326"
}
```

These examples illustrate the intended data structures.

Detailed runtime models will be established during implementation.

Additional types, including URL, Date, Boolean, and structured collections, may be introduced when required by new supported scenarios.

---

## 6. EnrichmentPlan Contract

### 6.1 Definition

EnrichmentPlan is a declarative specification generated by the LLM Planner.

It describes:

- Which registered capabilities to invoke.
- Which input fields provide operation arguments.
- How operation outputs are connected.
- Which results should be written to the dataset.
- Which assumptions were made during planning.

The plan must not contain executable Python code or arbitrary external API endpoints.

### 6.2 Initial Schema

The initial EnrichmentPlan contains:

| Field | Type | Description |
|---|---|---|
| `version` | Text | Plan schema version |
| `steps` | List[PlanStep] | Planned operations |
| `outputs` | List[OutputMapping] | Target column mappings |
| `assumptions` | List[Text] | Relevant interpretation assumptions |

### 6.3 PlanStep

Each PlanStep contains:

| Field | Type | Description |
|---|---|---|
| `id` | Text | Unique identifier within the plan |
| `capability` | Text | Registered capability ID |
| `inputs` | Map | Named input references |
| `parameters` | Map | Capability-specific parameters |

Each step produces one logical output value of the type declared by the CapabilitySpec.

The Execution Engine may retain additional metadata, including execution status, evidence, and diagnostics.

These metadata fields are not treated as the step's primary output value.

### 6.4 Input References

Each PlanStep input must reference one of the following:

**ColumnReference**

```json
{
  "kind": "column",
  "name": "Capital_From"
}
```

**StepOutputReference**

```json
{
  "kind": "step_output",
  "step_id": "origin"
}
```

**LiteralValue**

```json
{
  "kind": "literal",
  "value": "Germany"
}
```

The implementation will use explicitly discriminated reference types.

A step output reference resolves to the typed value produced by the referenced step, not the entire OperationResult wrapper.

References to nonexistent columns or steps must be rejected.

### 6.5 OutputMapping

An OutputMapping identifies the value to write into a target column.

Conceptual structure:

```json
{
  "step_id": "distance",
  "value_field": "value",
  "target_column": "distance"
}
```

For a Quantity result, `value_field: "value"` selects its numeric component.

The selected field must exist in the output contract and be compatible with the destination column.

Unit compatibility must be checked before output generation.

Output mappings must respect the configured write policy.

### 6.6 Operation Dependencies

Dependencies are defined through StepOutputReferences.

The resulting operation graph must be acyclic.

The validator must reject:

- Missing step references.
- Duplicate step identifiers.
- Circular dependencies.
- Incompatible input/output types.
- Unsupported capabilities.

The Execution Engine determines the execution order through dependency analysis.

The order of steps in the serialized JSON is not required to define execution order.

---

## 7. Example Plan: Mountain Elevation

Input columns:

- `Mountain`
- `Country`
- `height`

User request:

"Find the elevation of each mountain in meters."

Expected plan:

```json
{
  "version": "1.0",
  "steps": [
    {
      "id": "resolve_mountain",
      "capability": "resolve_entity",
      "inputs": {
        "name": {
          "kind": "column",
          "name": "Mountain"
        },
        "country": {
          "kind": "column",
          "name": "Country"
        }
      },
      "parameters": {
        "entity_type": "mountain"
      }
    },
    {
      "id": "retrieve_elevation",
      "capability": "lookup_property",
      "inputs": {
        "entity": {
          "kind": "step_output",
          "step_id": "resolve_mountain"
        }
      },
      "parameters": {
        "property": "elevation"
      }
    }
  ],
  "outputs": [
    {
      "step_id": "retrieve_elevation",
      "value_field": "value",
      "target_column": "height"
    }
  ],
  "assumptions": [
    "Elevation is expressed in meters."
  ]
}
```

The plan must be executable using the registered capabilities without adding task-specific Python logic.

The property lookup capability returns elevation values normalized to meters, so a separate unit conversion step is unnecessary in this initial scenario.

---

## 8. Example Plan: Distance Between Capitals

Input columns:

- `Capital_From`
- `Country_From`
- `Capital_To`
- `Country_To`
- `distance`

User request:

"Calculate straight-line distances between the capitals in kilometers."

Expected plan:

```json
{
  "version": "1.0",
  "steps": [
    {
      "id": "origin",
      "capability": "geocode",
      "inputs": {
        "city": {
          "kind": "column",
          "name": "Capital_From"
        },
        "country": {
          "kind": "column",
          "name": "Country_From"
        }
      },
      "parameters": {}
    },
    {
      "id": "destination",
      "capability": "geocode",
      "inputs": {
        "city": {
          "kind": "column",
          "name": "Capital_To"
        },
        "country": {
          "kind": "column",
          "name": "Country_To"
        }
      },
      "parameters": {}
    },
    {
      "id": "distance",
      "capability": "geodesic_distance",
      "inputs": {
        "point_a": {
          "kind": "step_output",
          "step_id": "origin"
        },
        "point_b": {
          "kind": "step_output",
          "step_id": "destination"
        }
      },
      "parameters": {
        "unit": "km"
      }
    }
  ],
  "outputs": [
    {
      "step_id": "distance",
      "value_field": "value",
      "target_column": "distance"
    }
  ],
  "assumptions": [
    "Straight-line distance means geodesic distance, not driving distance.",
    "Distances are calculated between resolved city reference coordinates."
  ]
}
```

The origin and destination operations are independent.

The distance calculation depends on both coordinate results.

The engine may reuse identical location lookup results across records.

---

## 9. Plan Validation

### 9.1 Purpose

The Plan Validator determines whether an EnrichmentPlan is structurally correct, executable, and compliant with the applicable execution policies.

Plan validation must be deterministic.

It must not depend on asking an LLM whether the plan is valid.

### 9.2 Validation Stages

**Stage 1 — Schema Validation**

Checks:

- Required plan fields.
- Valid data structures.
- Supported schema version.
- Correct input reference formats.
- Valid parameter representations.

**Stage 2 — Reference and Graph Validation**

Checks:

- Unique step identifiers.
- Existing source columns.
- Existing referenced steps.
- Valid operation dependencies.
- Absence of cycles.
- Resolvable output references.

**Stage 3 — Type and Contract Validation**

Checks:

- Registered capability IDs.
- Required input arguments.
- Input/output type compatibility.
- Supported parameters and parameter values.
- Valid output field selection.
- Compatible measurement units.

**Stage 4 — Policy Validation**

Checks:

- Allowed capabilities and configured providers.
- Plan complexity limits.
- Applicable execution restrictions.
- Target column protection.
- Output write authorization.

Dynamic execution limits, including API usage and actual expenditure, must additionally be enforced during execution.

### 9.3 Validation Results

The validator returns a structured result:

```json
{
  "valid": false,
  "errors": [
    {
      "code": "TYPE_MISMATCH",
      "step_id": "distance",
      "field": "point_a",
      "expected": "GeoPoint",
      "actual": "Text"
    }
  ]
}
```

Errors must be sufficiently descriptive for:

- Developer diagnostics.
- User inspection.
- Optional LLM-assisted plan correction.

### 9.4 Limits of Plan Validation

Structural validity does not guarantee semantic correctness.

For example, a technically valid geodesic distance plan may still be inappropriate when the user requested driving distances.

Semantic mismatches may be detected through:

- Explicit task constraints.
- Known test scenarios.
- Additional LLM-based review.
- Human approval.

An LLM reviewer must not be considered an authoritative correctness verifier.

The initial implementation prioritizes deterministic structural validation and independent scenario testing.

---

## 10. Plan Review and Modification

Users must be able to inspect generated plans before execution.

### 10.1 Core MVP

The initial CLI workflow should support:

1. Generate a plan from a dataset and user request.
2. Validate the generated plan.
3. Export the plan as JSON.
4. Review and optionally modify the JSON.
5. Submit the plan for execution.
6. Revalidate the plan immediately before execution.

The exact CLI command syntax will be defined during implementation.

### 10.2 Plan Versioning

Plans should retain:

- Schema version.
- Plan content.
- Relevant assumptions.
- Generation or modification origin.

Possible modification origins:

- `generated`
- `user_modified`
- `replanned`

Plan content must remain inspectable and reproducible.

### 10.3 Portfolio v1

Future UI functionality may include:

- Visual plan inspection.
- Parameter editing.
- Column mapping modification.
- Plan approval.
- Saved reusable scenarios.

All modifications must pass the same deterministic validation rules.

---

## 11. Execution and Optimization

The Execution Engine consumes validated plans.

### 11.1 Execution Responsibilities

The engine must:

- Resolve operation dependencies.
- Determine executable steps.
- Resolve input values.
- Invoke registered capabilities.
- Select approved providers.
- Deduplicate equivalent operations.
- Reuse eligible cached results.
- Apply execution limits.
- Record operation results and evidence.
- Continue independent operations when recoverable failures occur.

### 11.2 Deduplication

Equivalent operations with identical normalized inputs and relevant parameters should not require repeated execution.

For example, multiple references to the same capital may share one geocoding result.

### 11.3 Caching

The initial implementation should support:

- Job-level deduplication and cache.
- A lightweight persistent cache when appropriate.

SQLite is the preferred initial persistent cache technology.

Cache eligibility and validity depend on the operation, provider, source restrictions, and relevant input parameters.

The cache must not silently convert unresolved or outdated observations into verified results.

### 11.4 Failure Handling

Operation failures must be represented explicitly.

Downstream steps that depend on unavailable results must be skipped or marked unresolved.

Independent steps and records may continue.

Retry and fallback behavior must remain bounded by ExecutionPolicy.

---

## 12. Verification and Evidence

Capability definitions may reference default ValidationPolicies.

The initial implementation uses capability-specific validation rather than attempting universal factual verification.

Examples:

**Entity resolution**
- Candidate identity matches expected context.
- Source identifier is recorded.
- Ambiguous matches are not silently accepted.

**Property lookup**
- Property is supported.
- Returned value has the expected type.
- Units are normalized.
- Source evidence is recorded.

**Geocoding**
- Coordinates fall within valid ranges.
- Location identity is consistent with available country context.
- Unresolved locations are reported.

**Geodesic distance**
- Inputs contain valid coordinates.
- The selected algorithm is deterministic.
- Output is a valid non-negative quantity in kilometers.

Passing these checks does not imply that every factual assertion has been independently proven.

Validation outcomes and source evidence must remain accessible for diagnostics and evaluation.

---

## 13. Explicitly Deferred Features

The following features are not part of the initial implementation:

- Arbitrary low-level tool composition.
- User-defined executable capabilities.
- General-purpose attribute ontology.
- Universal property lookup with dynamic output types.
- Arbitrary code generation and execution.
- Nested loops or conditional expressions inside plans.
- Visual drag-and-drop workflow editing.
- Full scenario marketplace or template management.
- Universal semantic plan verification.
- Universal factual correctness scoring.
- Complex cost-based query optimization.
- Distributed execution and scheduling.

These features may be considered when supported by concrete use cases.

---

## 14. Initial Implementation Milestone

### M1 — Planner and Validator

**Objective:** Demonstrate that an LLM can generate valid, executable enrichment plans for two different tasks using the same registered capability definitions.

**Deliverables:**

1. Initial CapabilitySpec model.
2. Four registered capabilities.
3. Dataset profiling sufficient for both input files.
4. EnrichmentPlan and PlanStep models.
5. LLM-based plan generation.
6. Deterministic Plan Validator.
7. JSON plan export and loading.
8. Automated validation tests.
9. Example plans for the mandatory datasets.

**Acceptance Criteria:**

- Both mandatory datasets can be profiled.
- The LLM receives the dataset profile and registered capability descriptions.
- The LLM generates structured plans without task-specific application code.
- Both plans pass deterministic validation.
- Invalid capability references are rejected.
- Invalid column references are rejected.
- Type mismatches are rejected.
- Circular dependencies are rejected.
- Invalid parameters are rejected.
- Plans can be saved, inspected, modified, and revalidated.
- Planner behavior can be evaluated using controlled test cases.

### Subsequent Milestones

**M2 — Execution Engine**

Implement capability execution, provider integration, operation dependencies, and request deduplication.

**M3 — Excel Output and Evaluation**

Implement workbook-preserving output, structured diagnostics, independent benchmark evaluation, and integration tests.

**M4 — Agentic Orchestration**

Integrate LangGraph workflow control, bounded replanning, conditional recovery, and job-level state management.

**M5 — Portfolio Expansion**

Implement organization enrichment, web UI, persistent jobs, deployment, and extended observability.

Milestone boundaries may be adjusted based on implementation findings.

---

## 15. Accepted Design Decisions

| ID | Decision |
|---|---|
| PL-001 | Semantic Capabilities are the primary operations exposed to the LLM Planner. |
| PL-002 | Low-level primitives remain internal implementation details. |
| PL-003 | The LLM may independently compose registered Capabilities into a valid operation DAG. |
| PL-004 | Plans are declarative and cannot contain arbitrary executable code. |
| PL-005 | Plans must pass deterministic validation before execution. |
| PL-006 | Plans are inspectable, editable, and revalidated after modification. |
| PL-007 | Initial Capability contracts are limited to the two mandatory scenarios. |
| PL-008 | Execution Engine handles deduplication, caching, retries, and execution policies. |
| PL-009 | Validation is capability-specific; universal factual verification is not required. |
| PL-010 | Reusable Scenarios are a future extension built on validated plans. |

---

## 16. Next Steps

1. Create the Python project structure.
2. Configure dependency management, formatting, linting, and testing.
3. Implement the initial domain contracts using Pydantic.
4. Register the four initial CapabilitySpecs.
5. Implement deterministic plan validation.
6. Add controlled tests for valid and invalid plans.
7. Implement LLM-based plan generation.
8. Evaluate generated plans against both mandatory scenarios.

The contracts defined in this document are the initial implementation baseline.

Changes discovered during development should be incorporated through focused revisions rather than speculative redesign.

---

**Document status:** Initial implementation design, v0.1.  
**Next milestone:** M1 — Planner and Validator.