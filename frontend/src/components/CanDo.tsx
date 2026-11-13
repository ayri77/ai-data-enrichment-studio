import { useTranslation } from "react-i18next";

// The one list of what the system can do. It is shown before a workbook is
// chosen and again when a plan could not be built, so the two never disagree.
export function CanDo({
    maxRows,
    capabilities = [],
}: {
    maxRows: number;
    /** What the server offers; a road distance needs a configured source. */
    capabilities?: string[];
}) {
    const { t } = useTranslation();
    return (
        <div className="can-do">
            <p>{t("can.title")}</p>
            <ul>
                <li>{t("can.cityCoordinates")}</li>
                <li>{t("can.cityDistance")}</li>
                {capabilities.includes("road_distance") && (
                    <li>{t("can.roadDistance")}</li>
                )}
                <li>{t("can.mountainElevation")}</li>
            </ul>
            <p>{t("can.needs")}</p>
            <p>{t("can.limit", { max: maxRows })}</p>
        </div>
    );
}
