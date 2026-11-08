"""Deterministic fingerprint for bounded enrichment execution inputs."""

import hashlib
import json

from ai_data_enrichment.domain.dataset_profile import DatasetProfile
from ai_data_enrichment.domain.enrichment_plan import EnrichmentPlan
from ai_data_enrichment.execution.job_models import InputRecord


def calculate_execution_fingerprint(
    *,
    plan: EnrichmentPlan,
    dataset_profile: DatasetProfile,
    records: tuple[InputRecord, ...],
) -> str:
    """Hash canonical execution inputs with explicit schema versioning."""

    payload = {
        "fingerprint_version": 1,
        "plan": plan.model_dump(mode="json"),
        "dataset_profile": dataset_profile.model_dump(mode="json"),
        "records": [record.model_dump(mode="json") for record in records],
    }

    canonical_json = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )

    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
