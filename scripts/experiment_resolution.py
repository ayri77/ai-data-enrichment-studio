"""Developer diagnostic: why did each mountain name resolve, or not, against Wikidata?

Run as a module from the repository root:

    uv run python -m scripts.experiment_resolution
    uv run python -m scripts.experiment_resolution --policy verified_only
    uv run python -m scripts.experiment_resolution --names "Annapurna/Nepal" "Annapurna I/Nepal"

For every name it runs the production resolution service once and then reads,
for each candidate the search returned, the raw facts the verification depends
on: its `instance of` classes (P31) and whether it has a country (P17). That
shows which candidates the source could have ruled out and which are genuinely
unknown. Nothing here changes resolution behaviour.

No OpenAI key is needed. Results depend on live Wikidata and can change. The
JSON report is written under the ignored `artifacts/resolution-experiments/`.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter, sleep
from typing import Any
from uuid import uuid4

from openpyxl import load_workbook

from ai_data_enrichment.evaluation.plan_run_fixtures import APITransportFixture
from ai_data_enrichment.execution.models import OperationResult
from ai_data_enrichment.providers.http_transport import JsonTransport, UrllibJsonTransport
from ai_data_enrichment.providers.wikidata_constants import (
    WIKIDATA_API_URL,
    WIKIDATA_P_COUNTRY,
    WIKIDATA_P_INSTANCE_OF,
)
from ai_data_enrichment.providers.wikidata_resolution import WikidataMountainResolutionService
from ai_data_enrichment.providers.wikidata_verification import fetch_wikidata_candidate_entities
from ai_data_enrichment.resolution.models import ResolutionPolicy

USER_AGENT = "AIDataEnrichmentStudio/0.1 (https://github.com/ayri77/ai-data-enrichment-studio)"


def sample_names() -> list[tuple[str, str]]:
    workbook = load_workbook("samples/mountains.xlsx")
    try:
        sheet = workbook.active
        assert sheet is not None
        rows = list(sheet.iter_rows(min_row=2, max_col=2, values_only=True))
    finally:
        workbook.close()
    return [(str(name), str(country)) for name, country in rows if name and country]


def entity_ids(claims: dict[str, Any], property_id: str) -> list[str]:
    """IDs of entity-valued statements; malformed statements are skipped."""
    ids: list[str] = []
    for statement in claims.get(property_id, []):
        try:
            ids.append(statement["mainsnak"]["datavalue"]["value"]["id"])
        except (KeyError, TypeError):
            continue
    return ids


def class_labels(transport: JsonTransport, class_ids: set[str]) -> dict[str, str]:
    """English labels for readability only; a failure leaves bare IDs."""
    labels: dict[str, str] = {}
    ordered = sorted(class_ids)
    for offset in range(0, len(ordered), 50):
        try:
            payload = transport.get_json(
                url=WIKIDATA_API_URL,
                params={
                    "action": "wbgetentities",
                    "ids": "|".join(ordered[offset : offset + 50]),
                    "props": "labels",
                    "languages": "en",
                    "format": "json",
                },
                timeout_seconds=10.0,
            )
        except Exception:
            continue
        if isinstance(payload, dict):
            for qid, entity in payload.get("entities", {}).items():
                label = entity.get("labels", {}).get("en", {}).get("value")
                if isinstance(label, str):
                    labels[qid] = label
    return labels


class CountingTransport:
    """Count the requests that pass through, to see what one name costs."""

    def __init__(self, delegate: JsonTransport) -> None:
        self.delegate = delegate
        self.requests = 0

    def get_json(self, *, url: str, params: Mapping[str, str], timeout_seconds: float) -> object:
        self.requests += 1
        return self.delegate.get_json(url=url, params=params, timeout_seconds=timeout_seconds)


def inspect(
    transport: JsonTransport,
    name: str,
    country: str,
    policy: ResolutionPolicy,
    *,
    service: WikidataMountainResolutionService | None = None,
) -> dict:
    """Resolve one name and describe every candidate.

    Pass the same ``service`` for all names to measure them as one run does:
    the service remembers country codes between names. It must have been built
    on ``transport``, and ``transport`` must be a ``CountingTransport`` for the
    request count to be reported.
    """
    service = service or WikidataMountainResolutionService(transport)
    before = transport.requests if isinstance(transport, CountingTransport) else None
    started = perf_counter()
    outcome = service.resolve(inputs={"name": name, "country": country}, policy=policy)
    seconds = round(perf_counter() - started, 2)
    # Only the resolution itself is measured; the lookups below are for this printout.
    requests = (
        transport.requests - before
        if before is not None and isinstance(transport, CountingTransport)
        else None
    )
    candidate_set = outcome.report.candidate_set
    qids = tuple(c.reference.entity_id for c in candidate_set.candidates)
    entities = fetch_wikidata_candidate_entities(transport, qids=qids) if qids else {}
    if isinstance(entities, OperationResult):
        entities = {}
    facts: dict[str, dict[str, Any]] = {}
    for qid in qids:
        entity = entities.get(qid)
        claims = entity.get("claims", {}) if isinstance(entity, dict) else {}
        facts[qid] = {
            "classes": entity_ids(claims, WIKIDATA_P_INSTANCE_OF),
            "has_country": WIKIDATA_P_COUNTRY in claims,
            "countries": entity_ids(claims, WIKIDATA_P_COUNTRY),
        }
    labels = class_labels(transport, {c for f in facts.values() for c in f["classes"]})
    candidates = []
    for candidate in candidate_set.candidates:
        qid = candidate.reference.entity_id
        shown = {a.name: a.value for a in candidate.attributes}
        match = shown.get("match_kind")
        candidates.append(
            {
                "id": qid,
                "label": candidate.label,
                "match": match,
                # What a person sees on Review to tell namesakes apart.
                "description": shown.get("description"),
                "elevation_m": shown.get("elevation_m"),
                "coordinates": [shown["latitude"], shown["longitude"]]
                if "latitude" in shown and "longitude" in shown
                else None,
                "verification": candidate.verification,
                "reason_codes": list(candidate.reason_codes),
                "classes": [f"{c} {labels.get(c, '?')}" for c in facts[qid]["classes"]],
                "has_country": facts[qid]["has_country"],
            }
        )
    decision = outcome.report.decision
    return {
        "name": name,
        "country": country,
        "status": decision.status,
        "basis": decision.basis,
        "selected": decision.selected.entity_id if decision.selected else None,
        "decision_reason_codes": list(decision.reason_codes),
        "search_complete": candidate_set.search_complete,
        "verification_complete": candidate_set.verification_complete,
        "incomplete_reason": candidate_set.incomplete_reason,
        "requests": requests,
        "seconds": seconds,
        "candidates": candidates,
    }


def summarize(result: dict) -> str:
    candidates = result["candidates"]
    by_state = Counter(
        f"{c['verification']}:{','.join(c['reason_codes']) or '-'}" for c in candidates
    )
    unknown = [c for c in candidates if c["verification"] == "unchecked"]
    typed = sum(1 for c in unknown if c["classes"])
    no_country = sum(1 for c in unknown if not c["has_country"])
    lines = [
        f"{result['name']} ({result['country']}): {result['status']}"
        f"{' -> ' + result['selected'] if result['selected'] else ''}"
        f" | candidates {len(candidates)}"
        f" | incomplete: {result['incomplete_reason'] or '-'}"
        + (
            f" | requests {result['requests']} | {result['seconds']} s"
            if result.get("requests") is not None
            else ""
        ),
        *(f"    {count:>2}  {state}" for state, count in sorted(by_state.items())),
    ]
    confirmed = [c for c in candidates if c["verification"] == "verified"]
    if len(confirmed) > 1:
        # Several confirmed: these lines are what a person chooses between.
        lines.append(f"    confirmed: {len(confirmed)}")
        for candidate in confirmed:
            height = candidate.get("elevation_m")
            lines.append(
                f"      {candidate['id']:<12} {candidate.get('description') or '(no description)'}"
                f" | {f'{height:g} m' if height is not None else 'no elevation recorded'}"
            )
    if unknown:
        lines.append(
            f"    unconfirmed: {len(unknown)} | with a recorded type: {typed}"
            f" | without a country: {no_country}"
        )
        for candidate in unknown:
            lines.append(
                f"      {candidate['id']:<12} {candidate['label']!r:<32}"
                f" type: {'; '.join(candidate['classes']) or 'none'}"
                f" | country: {'yes' if candidate['has_country'] else 'no'}"
                f" | {','.join(candidate['reason_codes'])}"
            )
    return "\n".join(lines)


def run() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", choices=("strict", "verified_only"), default="strict")
    parser.add_argument(
        "--names",
        nargs="+",
        metavar="NAME/COUNTRY",
        help='Names to inspect instead of samples/mountains.xlsx, e.g. "Annapurna I/Nepal"',
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use the injected offline responses (checks that the script itself works)",
    )
    parser.add_argument("--out", type=Path, default=Path("artifacts/resolution-experiments"))
    args = parser.parse_args()

    if args.names:
        names = []
        for item in args.names:
            name, _, country = item.partition("/")
            if not name.strip() or not country.strip():
                parser.error(f'Use NAME/COUNTRY, got "{item}"')
            names.append((name.strip(), country.strip()))
    else:
        names = sample_names()

    transport: JsonTransport = (
        APITransportFixture()
        if args.offline
        else UrllibJsonTransport(headers={"User-Agent": USER_AGENT})
    )
    policy = ResolutionPolicy(
        policy_id=f"mountain_{args.policy}", version="1.0", strategy=args.policy
    )
    # One service for all names, as in a run: it remembers country codes between them.
    counting = CountingTransport(transport)
    service = WikidataMountainResolutionService(counting)
    results = []
    for name, country in names:
        try:
            result = inspect(counting, name, country, policy, service=service)
        except Exception as exc:  # A diagnostic should report, not stop at the first name.
            result = {
                "name": name,
                "country": country,
                "status": f"error:{type(exc).__name__}",
                "basis": None,
                "selected": None,
                "decision_reason_codes": [],
                "search_complete": None,
                "verification_complete": None,
                "incomplete_reason": None,
                "requests": None,
                "seconds": None,
                "candidates": [],
            }
        results.append(result)
        print(summarize(result), flush=True)
        if not args.offline:
            sleep(0.5)  # Be polite to the public API.

    directory = args.out / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    directory.mkdir(parents=True, exist_ok=False)
    report = directory / "report.json"
    report.write_text(
        json.dumps(
            {"policy": args.policy, "offline": args.offline, "results": results},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    resolved = sum(1 for r in results if r["status"] == "resolved")
    measured = [r for r in results if r["requests"] is not None]
    print(f"\nResolved {resolved} of {len(results)} under {args.policy}. Report: {report}")
    if measured:
        print(
            f"Resolution requests: {sum(r['requests'] for r in measured)} for "
            f"{len(measured)} names | {round(sum(r['seconds'] for r in measured), 1)} s"
        )


if __name__ == "__main__":
    run()
