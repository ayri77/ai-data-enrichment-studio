import {
    ArrowRight,
    Calculator,
    ChevronDown,
    Database,
    FileSearch,
    FileText,
    Globe,
    Info,
    LayoutGrid,
    Route,
    Server,
    Shapes,
    Sigma,
    TriangleAlert,
    type LucideIcon,
} from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import type { Capability, CapabilityField, Sample } from "../api";
import {
    WEB_SEARCH_CATEGORY,
    capabilityProvenance,
    capabilityTitle,
    groupCapabilities,
    provenanceLabel,
    readable,
    useCategories,
    useOffered,
} from "../capabilities";
import { LOOKUP_CAPABILITY } from "../fallbackModel";
import { useAdvanced, useHosted } from "../mode";
import { Button } from "./ui/button";

const HOW = ["table", "task", "plan", "result"] as const;

// The start page: what the system is, how a table is filled, and the list of
// what a plan may be built from. That list is the server's, so it grows with
// it; the interface adds its own wording where it has one.
export function OverviewWorkspace({
    maxRows,
    capabilities,
    webLookup = false,
    samples,
    busy,
    onStart,
    onSample,
}: {
    maxRows: number;
    /** Ids of what the server offers, used until its full list has arrived. */
    capabilities: string[];
    /** The server can search the web with AI where no plan can be built. */
    webLookup?: boolean;
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
                            {webLookup && ` ${t("overview.canHintWeb")}`}
                        </p>
                        {offered ? (
                            <Catalog items={offered} webLookup={webLookup} />
                        ) : (
                            <div data-testid="capability-list">
                                <ul className="capability-list">
                                    {capabilities.map((id) => (
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
                                    {webLookup && <WebSearchCard />}
                                </ul>
                            </div>
                        )}
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

const CATEGORY_ICONS: Record<string, LucideIcon> = {
    lookup: Database,
    distance: Route,
    calculate: Calculator,
    [WEB_SEARCH_CATEGORY]: Globe,
};
// A small mark of what stands behind the values; the sentence beside it says it.
const TRUST_ICONS: Record<string, LucideIcon> = {
    computed: Sigma,
    record: FileText,
    resolved_name: FileSearch,
    service: Server,
    ai_found: TriangleAlert,
};

interface Group {
    id: string;
    cards: { key: string; item?: Capability }[];
    /** The AI web search: built here, not listed by the server. */
    web: boolean;
}

// The catalog: tiles that filter, then the capabilities grouped by category.
// The filter lives in this component only: it is not stored and not in the URL.
function Catalog({
    items,
    webLookup,
}: {
    items: Capability[];
    webLookup: boolean;
}) {
    const { t } = useTranslation();
    const advanced = useAdvanced();
    const categories = useCategories();
    const [filter, setFilter] = useState<string | null>(null);
    const groups: Group[] = groupCapabilities(items, categories).map(
        (group) => ({
            id: group.id,
            web: false,
            cards: group.items.map((item) => ({ key: item.id, item })),
        }),
    );
    // The web search is a category of its own, with the interface's wording. Off
    // on this server it is shown to Advanced only, as a muted note.
    if (webLookup || advanced)
        groups.push({
            id: WEB_SEARCH_CATEGORY,
            web: true,
            cards: [{ key: LOOKUP_CAPABILITY }],
        });
    const tileGroups = groups.filter((group) => !group.web || webLookup);
    const tiles = tileGroups.length > 1;
    const active =
        tiles && tileGroups.some((g) => g.id === filter) ? filter : null;
    const shown = active
        ? groups.filter((group) => group.id === active)
        : groups;
    const total = tileGroups.reduce(
        (sum, group) => sum + group.cards.length,
        0,
    );
    return (
        <div data-testid="capability-catalog">
            {tiles && (
                <div className="category-tiles" data-testid="category-tiles">
                    <CategoryTile
                        icon={LayoutGrid}
                        title={t("cat.all.title")}
                        text={t("cat.all.text")}
                        count={total}
                        pressed={active === null}
                        onClick={() => setFilter(null)}
                        id="all"
                    />
                    {tileGroups.map((group) => (
                        <CategoryTile
                            key={group.id}
                            icon={CATEGORY_ICONS[group.id] ?? Shapes}
                            title={categoryTitle(t, group.id)}
                            text={t(`cat.${group.id}.text`, {
                                defaultValue: "",
                            })}
                            count={group.cards.length}
                            pressed={active === group.id}
                            onClick={() =>
                                setFilter(active === group.id ? null : group.id)
                            }
                            id={group.id}
                        />
                    ))}
                </div>
            )}
            <div data-testid="capability-list" className="capability-groups">
                {shown.map((group) => (
                    <section
                        key={group.id || "all"}
                        className="capability-group"
                        data-category={group.id}
                        aria-labelledby={
                            group.id ? `group-${group.id}` : undefined
                        }
                    >
                        {group.id && (
                            <h3 id={`group-${group.id}`}>
                                {categoryTitle(t, group.id)}
                            </h3>
                        )}
                        <ul className="capability-list">
                            {group.cards.map((card) =>
                                card.item ? (
                                    <CapabilityCard
                                        key={card.key}
                                        item={card.item}
                                    />
                                ) : webLookup ? (
                                    <WebSearchCard key={card.key} />
                                ) : (
                                    <li
                                        key={card.key}
                                        data-capability={LOOKUP_CAPABILITY}
                                        className="capability-off"
                                    >
                                        <strong>
                                            {capabilityTitle(
                                                t,
                                                LOOKUP_CAPABILITY,
                                            )}
                                        </strong>
                                        <span>{t("overview.web.off")}</span>
                                    </li>
                                ),
                            )}
                        </ul>
                    </section>
                ))}
            </div>
        </div>
    );
}

function categoryTitle(t: TFunction, id: string) {
    return t(`cat.${id}.title`, { defaultValue: readable(id) });
}

function CategoryTile({
    icon: Icon,
    title,
    text,
    count,
    pressed,
    onClick,
    id,
}: {
    icon: LucideIcon;
    title: string;
    text: string;
    count: number;
    pressed: boolean;
    onClick: () => void;
    id: string;
}) {
    return (
        <button
            type="button"
            className="category-tile"
            aria-pressed={pressed}
            data-category={id}
            onClick={onClick}
        >
            <span className="category-tile-head">
                <Icon aria-hidden="true" />
                <strong>{title}</strong>
                <span className="category-tile-count">{count}</span>
            </span>
            {text && <span className="category-tile-text">{text}</span>}
        </button>
    );
}

function TrustMark({
    trust,
    label,
}: {
    trust?: string;
    label: string | null | undefined;
}) {
    if (!label) return null;
    const Icon = (trust && TRUST_ICONS[trust]) || Info;
    return (
        <span className="capability-source" data-trust={trust ?? "unknown"}>
            <Icon aria-hidden="true" />
            <span>{label}</span>
        </span>
    );
}

// The one shape of a card: what it is, how its values are obtained, and a
// control that opens the rest in place. Several cards can be open at once.
function CardShell({
    id,
    title,
    text,
    trust,
    provenance,
    expandable,
    children,
}: {
    id: string;
    title: string;
    text: string;
    trust?: string;
    provenance: string | null | undefined;
    expandable: boolean;
    children: ReactNode;
}) {
    const { t } = useTranslation();
    const [open, setOpen] = useState(false);
    const panel = useId();
    return (
        <li data-capability={id} data-open={open && expandable}>
            <strong>{title}</strong>
            <span>{text}</span>
            <TrustMark trust={trust} label={provenance} />
            {expandable && (
                <button
                    type="button"
                    className="capability-toggle"
                    aria-expanded={open}
                    aria-controls={panel}
                    aria-label={t("overview.detailsFor", { title })}
                    onClick={() => setOpen(!open)}
                >
                    {t("overview.details")}
                    <ChevronDown aria-hidden="true" />
                </button>
            )}
            {expandable && open && (
                <div id={panel} className="capability-details">
                    {children}
                </div>
            )}
        </li>
    );
}

// What a capability needs and gives, in the interface's own words.
function Explained({ id }: { id: string }) {
    const { t } = useTranslation();
    return (
        <dl className="capability-explained">
            <dt>{t("overview.needs")}</dt>
            <dd>{t(`cap.${id}.needs`)}</dd>
            <dt>{t("overview.gives")}</dt>
            <dd>{t(`cap.${id}.gives`)}</dd>
            <dt>{t("overview.example")}</dt>
            <dd>{t(`cap.${id}.example`)}</dd>
        </dl>
    );
}

function CapabilityCard({ item }: { item: Capability }) {
    const { t, i18n } = useTranslation();
    const advanced = useAdvanced();
    // A capability the interface has no wording for is still listed: under its
    // id made readable, with the server's own description.
    const text = t(`cap.${item.id}.text`, { defaultValue: item.description });
    const explained = ["needs", "gives", "example"].every((part) =>
        i18n.exists(`cap.${item.id}.${part}`),
    );
    return (
        <CardShell
            id={item.id}
            title={capabilityTitle(t, item.id)}
            text={text}
            trust={item.trust}
            provenance={provenanceLabel(t, item)}
            expandable={explained || advanced}
        >
            {explained && <Explained id={item.id} />}
            {advanced && (
                <Contract item={item} described={text !== item.description} />
            )}
        </CardShell>
    );
}

// The AI web search is not a step a plan is built from, so the server does not
// list it; it is shown here from what the interface already knows of it.
function WebSearchCard() {
    const { t } = useTranslation();
    const offered = useOffered();
    return (
        <CardShell
            id={LOOKUP_CAPABILITY}
            title={capabilityTitle(t, LOOKUP_CAPABILITY)}
            text={t(`cap.${LOOKUP_CAPABILITY}.text`)}
            trust="ai_found"
            provenance={provenanceLabel(
                t,
                capabilityProvenance(offered, LOOKUP_CAPABILITY),
            )}
            expandable
        >
            <Explained id={LOOKUP_CAPABILITY} />
        </CardShell>
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
