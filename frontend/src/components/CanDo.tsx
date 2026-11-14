import { useTranslation } from "react-i18next";
import { capabilityTitle, useOffered } from "../capabilities";

// The one list of what the system can do. It is shown before a workbook is
// chosen and again when a plan could not be built, so the two never disagree,
// and it is the server's list: the overview page shows the same one in full.
export function CanDo({
    maxRows,
    capabilities = [],
}: {
    maxRows: number;
    /** Ids of what the server offers, used until its full list has arrived. */
    capabilities?: string[];
}) {
    const { t } = useTranslation();
    const offered = useOffered();
    const ids = offered?.map((item) => item.id) ?? capabilities;
    return (
        <div className="can-do">
            <p>{t("can.title")}</p>
            <ul>
                {ids.map((id) => (
                    <li key={id}>{capabilityTitle(t, id)}</li>
                ))}
            </ul>
            <p>{t("can.needs")}</p>
            <p>{t("can.limit", { max: maxRows })}</p>
        </div>
    );
}
