import { ArrowRight, Info } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { Capability, CapabilityField, Sample } from "../api";
import { capabilityTitle, useOffered } from "../capabilities";
import { useAdvanced, useHosted } from "../mode";
import { Button } from "./ui/button";

const HOW = ["table", "task", "plan", "result"] as const;

// The start page: what the system is, how a table is filled, and the list of
// what a plan may be built from. That list is the server's, so it grows with
// it; the interface adds its own wording where it has one.
export function OverviewWorkspace({
    maxRows,
    capabilities,
    samples,
    busy,
    onStart,
    onSample,
}: {
    maxRows: number;
    /** Ids of what the server offers, used until its full list has arrived. */
    capabilities: string[];
    samples: Sample[];
    busy: boolean;
    onStart: () => void;
    onSample: (sample: Sample) => void;
}) {
    const { t } = useTranslation();
    const advanced = useAdvanced();
    const hosted = useHosted();
    const offered = useOffered();
    const known = [
        "limit",
        "empty",
        "copy",
        "only",
        "sends",
        ...(hosted ? ["shared", "counted"] : []),
    ];
    return (
        <div className="stage-layout">
            <section className="workspace-panel overview">
                <div className="panel-scroll overview-content">
                    <div className="overview-lead">
                        <p className="overview-claim">{t("overview.claim")}</p>
                        <p>{t("overview.lead")}</p>
                    </div>
                    <section aria-labelledby="overview-how">
                        <h2 id="overview-how">{t("overview.howTitle")}</h2>
                        <ol className="overview-how">
                            {HOW.map((step) => (
                                <li key={step}>
                                    <strong>
                                        {t(`overview.how.${step}.title`)}
                                    </strong>
                                    <span>
                                        {t(`overview.how.${step}.text`)}
                                    </span>
                                </li>
                            ))}
                        </ol>
                    </section>
                    <section aria-labelledby="overview-can">
                        <h2 id="overview-can">{t("overview.canTitle")}</h2>
                        <p className="muted">
                            {t(
                                advanced
                                    ? "overview.canHintAdvanced"
                                    : "overview.canHint",
                            )}
                        </p>
                        <ul
                            className="capability-list"
                            data-testid="capability-list"
                        >
                            {offered
                                ? offered.map((item) => (
                                      <CapabilityCard
                                          key={item.id}
                                          item={item}
                                      />
                                  ))
                                : capabilities.map((id) => (
                                      <li key={id} data-capability={id}>
                                          <strong>
                                              {capabilityTitle(t, id)}
                                          </strong>
                                          <span>
                                              {t(`cap.${id}.text`, {
                                                  defaultValue: "",
                                              })}
                                          </span>
                                      </li>
                                  ))}
                        </ul>
                    </section>
                </div>
            </section>
            <aside className="workspace-panel stage-sidebar">
                <div className="panel-scroll summary-content">
                    <h2>{t("overview.beginTitle")}</h2>
                    <p>{t("overview.beginText")}</p>
                    {samples.length > 0 && (
                        <div className="overview-samples">
                            <p className="muted">{t("sample.try")}</p>
                            {samples.map((sample) => (
                                <Button
                                    key={sample.sample_id}
                                    variant="outline"
                                    size="sm"
                                    disabled={busy}
                                    onClick={() => onSample(sample)}
                                >
                                    {t(`sample.${sample.sample_id}`, {
                                        defaultValue: sample.filename,
                                    })}
                                </Button>
                            ))}
                        </div>
                    )}
                    <h2 className="overview-know-title">
                        <Info aria-hidden="true" />
                        {t("overview.knowTitle")}
                    </h2>
                    <ul className="overview-know">
                        {known.map((key) => (
                            <li key={key}>
                                {key === "counted"
                                    ? t("access.counted")
                                    : t(`overview.know.${key}`, {
                                          max: maxRows,
                                      })}
                            </li>
                        ))}
                    </ul>
                </div>
                <div className="workspace-actions">
                    <Button disabled={busy} onClick={onStart}>
                        {t("overview.begin")}
                        <ArrowRight aria-hidden="true" />
                    </Button>
                </div>
            </aside>
        </div>
    );
}

function CapabilityCard({ item }: { item: Capability }) {
    const { t } = useTranslation();
    const advanced = useAdvanced();
    // A capability the interface has no wording for is still listed: under its
    // id made readable, with the server's own description.
    const text = t(`cap.${item.id}.text`, { defaultValue: item.description });
    return (
        <li data-capability={item.id}>
            <strong>{capabilityTitle(t, item.id)}</strong>
            <span>{text}</span>
            <span className="capability-source">
                {item.source === "local"
                    ? t("overview.computed")
                    : t("overview.source", {
                          source: t(`source.${item.source}`, {
                              defaultValue: item.source,
                          }),
                      })}
            </span>
            {advanced && (
                <Contract item={item} described={text !== item.description} />
            )}
        </li>
    );
}

// The contract exactly as the planning model is shown it: nothing is reworded,
// so what is read here is what a plan is checked against.
function Contract({
    item,
    described,
}: {
    item: Capability;
    /** False where the card above already shows the server's description. */
    described: boolean;
}) {
    const { t } = useTranslation();
    const fields = Object.entries(item.output.fields);
    return (
        <div className="capability-contract" data-testid="capability-contract">
            <p className="mono">
                {item.id} · v{item.version} · {item.source}
            </p>
            {described && <p className="muted">{item.description}</p>}
            <Fields
                caption={t("overview.contract.inputs")}
                fields={item.inputs}
            />
            <Fields
                caption={t("overview.contract.parameters")}
                fields={item.parameters}
            />
            <table>
                <caption>{t("overview.contract.output")}</caption>
                <tbody>
                    <tr>
                        <th scope="row" className="mono">
                            {item.output.type}
                        </th>
                        <td className="mono">
                            {fields
                                .map(([name, type]) => `${name}: ${type}`)
                                .join(", ")}
                        </td>
                        <td>{item.output.description}</td>
                    </tr>
                </tbody>
            </table>
        </div>
    );
}

function Fields({
    caption,
    fields,
}: {
    caption: string;
    fields: Record<string, CapabilityField>;
}) {
    const { t } = useTranslation();
    const rows = Object.entries(fields);
    if (!rows.length) return null;
    return (
        <table>
            <caption>{caption}</caption>
            <tbody>
                {rows.map(([name, field]) => (
                    <tr key={name}>
                        <th scope="row" className="mono">
                            {name}
                        </th>
                        <td className="mono">
                            {field.allowed_values?.length
                                ? field.allowed_values.join(" | ")
                                : field.type}
                            {" · "}
                            {t(
                                field.required
                                    ? "overview.contract.required"
                                    : "overview.contract.optional",
                            )}
                        </td>
                        <td>{field.description}</td>
                    </tr>
                ))}
            </tbody>
        </table>
    );
}
