export interface Worksheet {
    name: string;
    columns: string[];
    row_count: number;
}
export interface Dataset {
    dataset_id: string;
    filename: string;
    worksheets: Worksheet[];
}
export interface Preview {
    dataset_id: string;
    sheet_name: string;
    range_ref: string;
    sample_limit: number;
    profile: {
        row_count: number;
        columns: {
            name: string;
            inferred_type: string;
            null_count: number;
            warnings: string[];
        }[];
        sample_records: Record<string, string | number | boolean | null>[];
        schema_warnings: string[];
    };
}
async function response<T>(request: Promise<Response>): Promise<T> {
    let result: Response;
    try {
        result = await request;
    } catch {
        throw new Error(
            "Cannot reach the local API. Start the backend and try again.",
        );
    }
    if (!result.ok) {
        const body = await result.json().catch(() => null);
        throw new Error(
            body?.error?.message ??
                "The request could not be completed. Please try again.",
        );
    }
    return result.json() as Promise<T>;
}
export const api = {
    upload: (file: File) =>
        response<Dataset>(
            fetch(`/api/datasets?filename=${encodeURIComponent(file.name)}`, {
                method: "POST",
                body: file,
                headers: { "Content-Type": "application/octet-stream" },
            }),
        ),
    preview: (id: string, sheet_name: string, range_ref: string) =>
        response<Preview>(
            fetch(`/api/datasets/${id}/preview`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ sheet_name, range_ref }),
            }),
        ),
    remove: (id: string) =>
        fetch(`/api/datasets/${id}`, { method: "DELETE" }).catch(
            () => undefined,
        ),
};
