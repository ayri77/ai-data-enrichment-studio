import {
    ArrowRight,
    CircleAlert,
    ExternalLink,
    LoaderCircle,
    Play,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ReviewCandidate, ReviewItem } from "../api";
import { useAdvanced } from "../mode";
import type { Workflow } from "../useWorkflow";
import {
    candidateKey,
    countryName,
    isCandidate,
    pendingRecommendations,
    rowList,
    sourceLink,
} from "../reviewModel";
import { issueGroups } from "../workflowModel";
import { Button } from "./ui/button";
import { Back, EmptyState, ModeNotice, QualitySummary } from "./WorkspaceViews";

/** Known codes are translated; an unknown code is shown as it is, never hidden. */
function useCode() {
    const { t } = useTranslation();
    return (group: string, code: string | null | undefined) =>
        code ? t(`review.${group}.${code}`, { defaultValue: code }) : "";
}

function contextLine(item: ReviewItem, locale: string, kind: string) {
    const { country_code, country_codes, administrative_area } =
        item.query.context;
    return [
        kind,
        typeof administrative_area === "string" ? administrative_area : null,
        countryName(country_code ?? country_codes, locale),
    ]
        .filter(Boolean)
        .join(" · ");
}

/** Plain facts that help tell namesakes apart; nothing here is a judgement. */
function Facts({ candidate }: { candidate: ReviewCandidate }) {
    const { t, i18n } = useTranslation();
    const code = useCode();
    const locale = i18n.resolvedLanguage ?? "en";
    const a = candidate.attributes;
    const facts = [
        typeof a.administrative_area === "string"
            ? a.administrative_area
            : null,
        countryName(a.country_code ?? a.country_codes, locale),
        typeof a.administrative_role === "string"
            ? code("role", a.administrative_role)
            : null,
        typeof a.population === "number"
            ? t("review.population", {
                  value: new Intl.NumberFormat(locale).format(a.population),
              })
            : null,
        typeof a.latitude === "number" && typeof a.longitude === "number"
            ? `${a.latitude.toFixed(2)}, ${a.longitude.toFixed(2)}`
            : null,
        typeof a.match_kind === "string" ? code("match", a.match_kind) : null,
    ].filter(Boolean);
    return facts.length ? (
        <span className="candidate-facts">{facts.join(" · ")}</span>
    ) : null;
}

function Candidate({
    item,
    candidate,
    w,
}: {
    item: ReviewItem;
    candidate: ReviewCandidate;
    w: Workflow;
}) {
    const { t } = useTranslation();
    const code = useCode();
    const choice = w.choices[item.operation_id];
    const key = candidateKey(candidate);
    const checked = isCandidate(choice) && candidateKey(choice) === key;
    const link = sourceLink(candidate);
    return (
        <div
            className="candidate"
            data-selected={checked}
            data-disabled={!candidate.selectable}
            data-testid={`candidate-${key}`}
        >
            <label>
                <input
                    type="radio"
                    name={item.operation_id}
                    checked={checked}
                    disabled={!candidate.selectable || w.locked}
                    onChange={() =>
                        w.choose(item.operation_id, {
                            source: candidate.source,
                            entity_id: candidate.entity_id,
                        })
                    }
                />
                <span className="candidate-body">
                    <span className="candidate-title">
                        {candidate.label}
                        {candidate.recommended && candidate.selectable && (
                            <span className="candidate-badge">
                                {t("review.recommended")}
                            </span>
                        )}
                    </span>
                    <Facts candidate={candidate} />
                    {candidate.recommended &&
                        candidate.selectable &&
                        candidate.recommendation_reasons.length > 0 && (
                            <span className="muted">
                                {t("review.why", {
                                    reasons: candidate.recommendation_reasons
                                        .map((reason) => code("reason", reason))
                                        .join(", "),
                                })}
                            </span>
                        )}
                    {!candidate.selectable && (
                        <span className="muted">
                            {t("review.blocked", {
                                reason: code("blocker", candidate.blocker),
                            })}
                        </span>
                    )}
                </span>
            </label>
            {link && (
                <a
                    className="candidate-link"
                    href={link.href}
                    target="_blank"
                    rel="noreferrer noopener"
                >
                    {t(`review.link.${link.kind}`)}
                    <ExternalLink aria-hidden="true" />
                </a>
            )}
        </div>
    );
}

/** Second layer: identifiers, codes and evidence for audit and debugging. */
function Technical({ item }: { item: ReviewItem }) {
    const { t } = useTranslation();
    if (!useAdvanced()) return null;
    return (
        <details className="review-technical" data-advanced="true">
            <summary>{t("review.technical")}</summary>
            <dl>
                <div>
                    <dt>{t("review.tech.operation")}</dt>
                    <dd className="mono">{item.operation_id}</dd>
                </div>
                <div>
                    <dt>{t("review.tech.source")}</dt>
                    <dd className="mono">
                        {item.provider_id} · {item.capability ?? "—"}
                    </dd>
                </div>
                <div>
                    <dt>{t("review.tech.decision")}</dt>
                    <dd className="mono">
                        {item.status} · {item.reason_codes.join(", ") || "—"}
                    </dd>
                </div>
                <div>
                    <dt>{t("review.tech.records")}</dt>
                    <dd className="mono">
                        {item.affected
                            .map((a) => `${a.record_id} → ${a.step_id}`)
                            .join(" · ")}
                    </dd>
                </div>
                {item.candidates.map((c) => (
                    <div key={candidateKey(c)}>
                        <dt className="mono">{candidateKey(c)}</dt>
                        <dd className="mono">
                            {[
                                c.verification,
                                c.entity_type,
                                ...c.reason_codes,
                                ...c.evidence.map((e) => e.reference),
                            ]
                                .filter(Boolean)
                                .join(" · ")}
                        </dd>
                    </div>
                ))}
            </dl>
        </details>
    );
}

/** Matches the source did not confirm: kept for audit, out of the way of the choice. */
function Blocked({ item, w }: { item: ReviewItem; w: Workflow }) {
    const { t } = useTranslation();
    const advanced = useAdvanced();
    const blocked = item.candidates.filter((c) => !c.selectable);
    if (!advanced || !blocked.length) return null;
    return (
        <details className="review-technical" data-advanced="true">
            <summary>
                {t("review.blockedCount", { count: blocked.length })}
            </summary>
            <div className="candidate-list">
                {blocked.map((candidate) => (
                    <Candidate
                        key={candidateKey(candidate)}
                        item={item}
                        candidate={candidate}
                        w={w}
                    />
                ))}
            </div>
        </details>
    );
}

function ReviewCard({ item, w }: { item: ReviewItem; w: Workflow }) {
    const { t, i18n } = useTranslation();
    const code = useCode();
    const locale = i18n.resolvedLanguage ?? "en";
    const choice = w.choices[item.operation_id];
    return (
        <article
            className="review-card"
            data-decided={!!choice}
            data-testid={`review-${item.query.name}`}
        >
            <header>
                <h3>{item.query.name}</h3>
                <span className="muted">
                    {contextLine(
                        item,
                        locale,
                        code("type", item.query.entity_type),
                    )}
                </span>
                <span className="review-rows">
                    {t("review.rows", { rows: rowList(item) })}
                </span>
            </header>
            <fieldset
                className="candidate-list"
                aria-label={t("review.candidates", { name: item.query.name })}
            >
                {item.candidates
                    .filter((candidate) => candidate.selectable)
                    .map((candidate) => (
                        <Candidate
                            key={candidateKey(candidate)}
                            item={item}
                            candidate={candidate}
                            w={w}
                        />
                    ))}
                <div className="candidate" data-selected={choice === "empty"}>
                    <label>
                        <input
                            type="radio"
                            name={item.operation_id}
                            checked={choice === "empty"}
                            disabled={w.locked}
                            onChange={() =>
                                w.choose(item.operation_id, "empty")
                            }
                        />
                        <span className="candidate-body">
                            <span className="candidate-title">
                                {t("review.leaveEmpty")}
                            </span>
                            <span className="muted">
                                {t("review.leaveEmptyHint")}
                            </span>
                        </span>
                    </label>
                </div>
            </fieldset>
            <Blocked item={item} w={w} />
            <Technical item={item} />
        </article>
    );
}

function Informational({ items }: { items: ReviewItem[] }) {
    const { t, i18n } = useTranslation();
    const code = useCode();
    const locale = i18n.resolvedLanguage ?? "en";
    return (
        <section className="review-informational">
            <h3>{t("review.informational")}</h3>
            <p className="muted">{t("review.informationalHint")}</p>
            <ul>
                {items.map((item) => (
                    <li
                        key={item.operation_id}
                        data-testid={`unresolved-${item.query.name}`}
                    >
                        <strong>{item.query.name}</strong>
                        <span className="muted">
                            {contextLine(
                                item,
                                locale,
                                code("type", item.query.entity_type),
                            )}
                        </span>
                        <span>{code("status", item.status)}</span>
                        <span className="review-rows">
                            {t("review.rows", { rows: rowList(item) })}
                        </span>
                    </li>
                ))}
            </ul>
        </section>
    );
}

/** The earlier per-code summary, kept for audit; it is not needed to decide. */
function ErrorCodes({ w }: { w: Workflow }) {
    const { t } = useTranslation();
    const advanced = useAdvanced();
    const groups = issueGroups(w.report);
    if (!advanced || !groups.length) return null;
    return (
        <details className="review-technical" data-advanced="true">
            <summary>{t("review.errorCodes")}</summary>
            <p className="muted">{t("ws.groupingHint")}</p>
            {groups.map((group) => (
                <article className="issue-group" key={group.code}>
                    <div className="panel-title">
                        <CircleAlert aria-hidden="true" />
                        <h3>{group.code}</h3>
                        <span className="status-label">
                            {t(
                                group.derived
                                    ? "ws.derivedIssue"
                                    : "ws.rootIssue",
                            )}
                        </span>
                    </div>
                    <p>
                        {t("ws.affectedRecords", {
                            count: group.records.size,
                            operations: group.operations,
                        })}
                    </p>
                    <p className="mono muted">
                        {[...group.records].join(" · ")}
                    </p>
                </article>
            ))}
        </details>
    );
}

export function ReviewWorkspace({ w }: { w: Workflow }) {
    const { t } = useTranslation();
    const review = w.review?.job_id === w.job?.job_id ? w.review : null;
    const needsReview = w.job?.status === "needs_review";
    const total = review?.items.length ?? 0;
    const decided = (review?.items ?? []).filter(
        (item) => w.choices[item.operation_id],
    ).length;
    const suggestions = pendingRecommendations(review, w.choices);
    return (
        <div className="stage-layout">
            <section className="workspace-panel panel-scroll summary-content">
                <div className="panel-title">
                    <CircleAlert aria-hidden="true" />
                    <h2>{t("review.title")}</h2>
                </div>
                <ModeNotice w={w} />
                <QualitySummary w={w} />
                {!w.report ? (
                    <EmptyState text={t("ws.reviewPending")} />
                ) : !needsReview ? (
                    <p className="notice">
                        {t(
                            w.report.status === "completed"
                                ? "ws.noIssues"
                                : "ws.noRowDetails",
                        )}
                    </p>
                ) : !review ? (
                    <p className="muted" role="status">
                        {t("review.loading")}
                    </p>
                ) : (
                    <>
                        {total > 0 ? (
                            <p data-testid="review-intro">
                                {t("review.intro", {
                                    items: total,
                                    rows: review.affected_rows,
                                })}
                            </p>
                        ) : (
                            <p className="notice">
                                {t("review.nothingToChoose")}
                            </p>
                        )}
                        {review.items.map((item) => (
                            <ReviewCard
                                key={item.operation_id}
                                item={item}
                                w={w}
                            />
                        ))}
                        {review.informational.length > 0 && (
                            <Informational items={review.informational} />
                        )}
                    </>
                )}
                {w.report && <ErrorCodes w={w} />}
            </section>
            <aside className="workspace-panel stage-sidebar">
                <div className="panel-scroll summary-content">
                    <h2>{t("review.sidebar")}</h2>
                    {total > 0 ? (
                        <>
                            <p data-testid="review-progress">
                                {t("review.progress", { decided, total })}
                            </p>
                            <Button
                                variant="outline"
                                disabled={!suggestions || w.locked}
                                onClick={w.acceptRecommendations}
                            >
                                {t("review.accept")}
                            </Button>
                            <Button
                                disabled={!w.canResume}
                                onClick={() =>
                                    void w.action(w.resume, "resume")
                                }
                            >
                                {w.operation?.kind === "resume" ? (
                                    <LoaderCircle
                                        className="spin"
                                        aria-hidden="true"
                                    />
                                ) : (
                                    <Play aria-hidden="true" />
                                )}
                                {t("review.resume")}
                            </Button>
                            <p className="muted">{t("review.resumeHint")}</p>
                            {!w.network && (
                                <p className="muted">
                                    {t("ws.executionNetwork")}
                                </p>
                            )}
                        </>
                    ) : (
                        <p className="muted">{t("review.sidebarIdle")}</p>
                    )}
                    {review?.previous_job_id && (
                        <p className="notice">{t("review.continued")}</p>
                    )}
                </div>
                <div className="workspace-actions">
                    <Back w={w} to="execution" />
                    <Button onClick={() => w.navigate("export")}>
                        {t("ws.toExport")}
                        <ArrowRight />
                    </Button>
                </div>
            </aside>
        </div>
    );
}
