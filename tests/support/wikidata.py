"""Reusable, independent Wikidata HTTP fixture statements for tests."""


def entity_statement(qid: str) -> dict[str, object]:
    """Return an independent best-ranked entity reference statement."""
    return {
        "rank": "normal",
        "mainsnak": {
            "snaktype": "value",
            "datavalue": {
                "type": "wikibase-entityid",
                "value": {"id": qid},
            },
        },
    }
