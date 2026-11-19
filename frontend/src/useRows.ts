import { useEffect, useRef, useState, type MutableRefObject } from "react";
import {
    api,
    WorkspaceApiError,
    type Dataset,
    type Preview,
    type RowSource,
    type RowsCount,
    type RowsPreview,
    type RowsRun,
} from "./api";
import { packMails } from "./mailModel";
import {
    availableSources,
    chosenSource,
    conditionsOf,
    editPlan,
    firstRequest,
    initialTask,
    newCondition,
    oldRowsKept,
    proposedFields,
    requestOf,
    rowsFilename,
    rowsNeeds,
    runBlock,
    shownPlan,
    taskKind,
    withField,
    withOperator,
    MAX_CONDITIONS,
    type ConditionDraft,
    type PlanEdit,
    type RowsPlanShown,
    type RowsTask,
} from "./rowsModel";
import type { Operation, Workspace } from "./useWorkflow";

/** The plan of new rows on the second step, with what is known about it. */
export interface RowsPlanState {
    /** The dataset the rows are added to (a copy is made; this one stays). */
    datasetId: string;
    /** What is on the screen and what will run. */
    shown: RowsPlanShown;
    /** The server's answer for `shown`; stale when the last question failed. */
    answer: RowsPreview;
    stale: boolean;
    /** A new question is on its way. */
    refreshing: boolean;
    /** Fields whose column the server proposed and the person has not changed. */
    proposed: string[];
    keyProposed: boolean;
    /** Rows the table had when the plan was made. */
    tableRows: number;
}

/** What a run of new rows amounts to, with what was asked about the new table. */
export interface RowsResultState {
    run: RowsRun;
    /** The plan that ran. */
    shown: RowsPlanShown;
    /** The new table, as the first step takes it over. */
    dataset: Dataset;
    /** What a second run on the same files would do; null when it could not be asked. */
    second: RowsCount | null;
    /** The new table read from its sheet, for its last rows; null when it could not be. */
    tail: Preview | null;
    /**
     * The rows the table had, compared cell by cell with the same rows of the
     * new table; null when the two could not be compared.
     */
    kept: { rows: number; same: boolean } | null;
}

const errorCode = (err: unknown) =>
    err instanceof WorkspaceApiError ? err.code : "request_failed";

/**
 * The second kind of task: new rows from a source. It lives inside the
 * workflow (which owns busy, errors and the steps) and holds what only this
 * task has: the sources, the task as typed, the plan with its counts and the
 * result. Nothing here is saved: a new workbook starts it again.
 */
export function useRows(input: {
    preview: Preview | null;
    /** Workbook and sheet; the task is reset when this changes. */
    tableKey: string;
    filename?: string;
    /** The workflow's way to wait for a request and to show its error. */
    action: (work: () => Promise<void>, kind: Operation) => Promise<void>;
    fail: (code: string) => void;
    navigate: (to: Workspace) => void;
    mounted: MutableRefObject<boolean>;
}) {
    const { preview, mounted } = input;
    const [sources, setSources] = useState<RowSource[]>([]);
    const [task, setTask] = useState<RowsTask>(initialTask);
    const [plan, setPlanState] = useState<RowsPlanState | null>(null);
    const [result, setResult] = useState<RowsResultState | null>(null);
    // The latest plan, for an edit made before the last answer came, and a
    // counter that tells an answer from one an edit has overtaken.
    const latest = useRef<RowsPlanState | null>(null);
    const asked = useRef(0);
    const taskNow = useRef(task);
    taskNow.current = task;
    function setPlan(next: RowsPlanState | null) {
        latest.current = next;
        setPlanState(next);
    }
    useEffect(() => {
        let active = true;
        Promise.resolve()
            .then(() => api.rowSources())
            .then((found) => active && setSources(found?.sources ?? []))
            // Without the list there is no second kind of task; nothing else changes.
            .catch(() => undefined);
        return () => {
            active = false;
        };
    }, []);
    const offered = availableSources(sources).length > 0;
    const source = chosenSource(sources, task.sourceId);
    const kind = taskKind(task);
    // The columns part of the task is on its own as long as no source is offered.
    const mode = offered ? kind : "columns";

    function forget(id: string | undefined) {
        if (!id) return;
        Promise.resolve()
            .then(() => api.forgetCandidates(id))
            .catch(() => undefined);
    }
    /** The plan and the result go; the task stays. */
    function resetPlan() {
        asked.current++;
        setPlan(null);
        setResult(null);
    }
    /** A new workbook: the task is the first one again and the files are forgotten. */
    function resetAll() {
        resetPlan();
        forget(taskNow.current.candidates?.candidates_id);
        setTask(initialTask());
    }
    useEffect(() => {
        resetAll();
    }, [input.tableKey]);

    // --- The task ----------------------------------------------------------------
    function setFill(fill: boolean) {
        setTask((old) => ({ ...old, fill }));
    }
    function setAdd(add: boolean) {
        setTask((old) => ({ ...old, add }));
    }
    function setSource(sourceId: string) {
        if (sourceId === source?.source_id) return;
        forget(task.candidates?.candidates_id);
        setTask((old) => ({
            ...old,
            sourceId,
            candidates: null,
            conditions: [],
        }));
    }
    function addCondition() {
        if (!source || task.conditions.length >= MAX_CONDITIONS) return;
        setTask((old) => ({
            ...old,
            conditions: [...old.conditions, newCondition(source, old.next)],
            next: old.next + 1,
        }));
    }
    function changeCondition(
        id: number,
        change: { field: string } | { op: string } | { value: string },
    ) {
        if (!source) return;
        setTask((old) => ({
            ...old,
            conditions: old.conditions.map((condition): ConditionDraft =>
                condition.id !== id
                    ? condition
                    : "field" in change
                      ? withField(condition, source, change.field)
                      : "op" in change
                        ? withOperator(condition, change.op)
                        : { ...condition, value: change.value },
            ),
        }));
    }
    function removeCondition(id: number) {
        setTask((old) => ({
            ...old,
            conditions: old.conditions.filter((c) => c.id !== id),
        }));
    }
    /**
     * The chosen files are packed here, as the e-mail entrance packs them (their
     * names are not sent), and read by the server into candidates. Choosing again
     * replaces the set; the earlier one is forgotten.
     */
    async function readFiles(files: File[]) {
        if (!source || !files.length) return;
        const body = await packMails(files);
        const found = await api.readCandidates(source.source_id, body);
        if (!mounted.current) return;
        forget(taskNow.current.candidates?.candidates_id);
        resetPlan();
        setTask((old) => ({
            ...old,
            sourceId: source.source_id,
            candidates: found,
        }));
    }

    // --- The plan ----------------------------------------------------------------
    const conditions = conditionsOf(task.conditions);
    const needs = rowsNeeds({ task, source, preview });
    const canBuild = mode === "rows" && needs.length === 0 && !!conditions;

    /** Ask the server for the plan with its proposals and open the second step. */
    async function build() {
        if (!canBuild || !preview || !task.candidates || !conditions) return;
        const answer = await api.previewRows(
            preview.dataset_id,
            firstRequest({
                candidatesId: task.candidates.candidates_id,
                preview,
                conditions,
            }),
        );
        if (!mounted.current) return;
        asked.current++;
        setResult(null);
        setPlan({
            datasetId: preview.dataset_id,
            shown: shownPlan(task.candidates.candidates_id, answer),
            answer,
            stale: false,
            refreshing: false,
            proposed: proposedFields(answer),
            keyProposed: answer.proposed.key,
            tableRows: preview.profile.row_count,
        });
        input.navigate("plan");
    }
    /**
     * One change of the plan: the preview is asked again with the explicit
     * mapping and key. An answer that a later change has overtaken is ignored.
     */
    async function change(edit: PlanEdit) {
        const before = latest.current;
        if (!before || result) return;
        const next = editPlan(before.shown, edit);
        const mine = ++asked.current;
        setPlan({
            ...before,
            shown: next,
            refreshing: true,
            proposed:
                edit.type === "column"
                    ? before.proposed.filter((field) => field !== edit.field)
                    : before.proposed,
            keyProposed:
                before.keyProposed &&
                edit.type !== "key" &&
                next.key.length === before.shown.key.length,
        });
        try {
            const answer = await api.previewRows(
                before.datasetId,
                requestOf(next),
            );
            if (!mounted.current || mine !== asked.current) return;
            setPlan({
                ...latest.current!,
                shown: next,
                answer,
                stale: false,
                refreshing: false,
            });
        } catch (err) {
            if (!mounted.current || mine !== asked.current) return;
            setPlan({ ...latest.current!, stale: true, refreshing: false });
            input.fail(errorCode(err));
        }
    }
    const canRun =
        !!plan &&
        !result &&
        !plan.refreshing &&
        !plan.stale &&
        runBlock(plan.answer) === null;

    /**
     * Run exactly the plan shown. Then ask about the new table what a second run
     * would do and read its end; those two only inform, so a failure of either
     * leaves the result without it.
     */
    async function run() {
        const shown = latest.current;
        if (!shown || !canRun) return;
        const done = await api.runRows(shown.datasetId, requestOf(shown.shown));
        if (!mounted.current) return;
        const table = {
            sheet_name: done.rows.sheet_name,
            range_ref: done.rows.range_ref,
        };
        const second = await api
            .previewRows(done.dataset_id, {
                ...requestOf(shown.shown),
                ...table,
            })
            .then((answer) => (answer.refused ? null : answer.count))
            .catch(() => null);
        const tail = await api
            .preview(done.dataset_id, table.sheet_name, table.range_ref)
            .catch(() => null);
        if (!mounted.current) return;
        setResult({
            run: done,
            shown: shown.shown,
            dataset: {
                dataset_id: done.dataset_id,
                filename: rowsFilename(input.filename),
                worksheets: done.worksheets,
            },
            second: second ?? null,
            tail: tail ?? null,
            kept:
                preview?.dataset_id === shown.datasetId
                    ? oldRowsKept(preview, tail ?? null)
                    : null,
        });
        input.navigate("export");
    }
    /** The workbook of the new table, saved under the client's name for it. */
    async function download() {
        if (!result) return;
        const blob = await api.workbook(result.dataset.dataset_id);
        if (!mounted.current) return;
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = result.dataset.filename;
        document.body.append(link);
        link.click();
        link.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    return {
        sources,
        /** At least one source is available: the second kind of task is offered. */
        offered,
        source,
        task,
        /** "columns" whenever nothing is offered. */
        mode,
        needs,
        canBuild,
        setFill,
        setAdd,
        setSource,
        addCondition,
        changeCondition,
        removeCondition,
        readFiles,
        build,
        plan,
        change,
        canRun,
        run,
        result,
        download,
        resetPlan,
        resetAll,
    };
}
export type Rows = ReturnType<typeof useRows>;
