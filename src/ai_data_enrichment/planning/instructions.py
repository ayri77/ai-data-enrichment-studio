"""Provider-independent contract guidance shared by planning and M4 repair."""

PLAN_CONTRACT_GUIDANCE = (
    "Compose registered capabilities into a typed DAG: an input may come from a "
    "dataset column OR the complete typed output of another step. Intermediate "
    "GeoPoint, EntityRef or Quantity values need not exist in source columns. "
    "Work backwards from the requested output, inventory compatible producers "
    "and consumers, and bind every required input to a real source or compatible "
    "step output. Reuse a capability in separate steps when different inputs require it. "
    "For example, if a registered producer returns EntityRef and a consumer needs "
    'EntityRef, the consumer input can be {"kind":"step_output",'
    '"step_id":"producer_step"}, where producer_step is an actual step ID. '
    "This illustrates a reference shape, not a plan template or an extra capability. "
    'Only inputs use typed reference objects: {"kind":"column","name":"source_column"}, '
    '{"kind":"step_output","step_id":"producer_step"}, or '
    '{"kind":"literal","value":"explicit task constant"}. '
    "Parameters are direct JSON values matching each capability's parameter contract, "
    "never input-reference objects. Text parameters require strings from allowed_values. "
    'For example, geodesic_distance uses parameters={"unit":"km"}, '
    'not {"unit":{"kind":"literal","value":"km"}} or {"unit":{"value":"km"}}. '
    "Output mappings project declared output fields into requested destination columns; "
    "intermediate steps do not need destination columns. "
    "Before abstaining, check the full compatible capability chain; missing intermediate "
    "values alone are not a reason to abstain when registered steps can derive them. "
    "If no supported chain exists or intent is insufficiently specified, abstain and "
    "explain the missing capability, source input or clarification. "
    "Do not invent source values, coordinates, capabilities, columns or semantics. "
    "Preserve literal entity names and their country context; a column label alone "
    "does not impose an administrative role such as country_capital. "
    "Unresolved or ambiguous row values remain execution-time failures for review, "
    "never guessed constants in a plan."
)
