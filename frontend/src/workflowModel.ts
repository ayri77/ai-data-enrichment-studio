import type { AuditReport, EnrichmentPlan, Preview } from "./api";
import { columnLetter, columnNumber, parseRange } from "./range";

export interface OutputTarget {
    mode: "existing" | "new";
    name: string;
    column: string;
}
export function normalizeColumn(value: string) {
    const text = value.trim().toUpperCase();
    return /^\d+$/.test(text)
        ? columnLetter(Math.min(Number(text), 101))
        : text;
}
export function validMappings(
    mapping: OutputTarget[],
    preview: Preview,
    sheetColumns: string[],
) {
    const bounds = parseRange(preview.range_ref).bounds;
    if (!bounds || !mapping.length) return false;
    return (
        mapping.every((entry) => {
            const index = columnNumber(entry.column);
            if (
                !entry.name.trim() ||
                !/^[A-Z]{1,2}$/.test(entry.column) ||
                index > 100
            )
                return false;
            if (entry.mode === "existing")
                return (
                    preview.profile.columns[index - bounds.firstColumn]
                        ?.name === entry.name
                );
            return (
                index > Math.max(bounds.lastColumn, sheetColumns.length) &&
                !preview.profile.columns.some(
                    (c) => c.name === entry.name.trim(),
                ) &&
                !sheetColumns.includes(entry.name.trim())
            );
        }) &&
        new Set(mapping.map((x) => x.name.trim())).size === mapping.length &&
        new Set(mapping.map((x) => x.column)).size === mapping.length
    );
}
// Read only the API's typed input references; this is graph presentation, never execution.
export function dependencies(step: EnrichmentPlan["steps"][number]) {
    return Object.entries(step.inputs).flatMap(([port, raw]) => {
        if (
            raw &&
            typeof raw === "object" &&
            "kind" in raw &&
            raw.kind === "step_output" &&
            "step_id" in raw &&
            typeof raw.step_id === "string"
        )
            return [{ port, step: raw.step_id }];
        return [];
    });
}
// How many steps a step waits for. Presentation only: server validation stays
// authoritative, so a cycle is simply cut here instead of being reported.
export function stepDepths(plan: EnrichmentPlan) {
    const depths = new Map<string, number>();
    function depth(id: string, path = new Set<string>()): number {
        if (depths.has(id)) return depths.get(id)!;
        if (path.has(id)) return 0;
        const node = plan.steps.find((x) => x.id === id);
        const parents = node ? dependencies(node) : [];
        const nextPath = new Set([...path, id]);
        return parents.length
            ? Math.min(
                  12,
                  1 + Math.max(...parents.map((p) => depth(p.step, nextPath))),
              )
            : 0;
    }
    plan.steps.forEach((s) => depths.set(s.id, depth(s.id)));
    return depths;
}
export function issueGroups(report: AuditReport | null) {
    const groups = new Map<
        string,
        {
            code: string;
            derived: boolean;
            records: Set<string>;
            operations: number;
        }
    >();
    for (const record of report?.execution?.records ?? [])
        for (const step of record.row_result.steps) {
            if (step.result.status !== "failure") continue;
            const code = step.result.error_code ?? "unknown";
            const group = groups.get(code) ?? {
                code,
                derived: code.toUpperCase() === "DEPENDENCY_FAILED",
                records: new Set<string>(),
                operations: 0,
            };
            group.records.add(record.record_id);
            group.operations++;
            groups.set(code, group);
        }
    return [...groups.values()].sort(
        (a, b) => Number(a.derived) - Number(b.derived),
    );
}
