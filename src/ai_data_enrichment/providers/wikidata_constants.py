"""Wikidata API endpoints, semantic identifiers, and bounded lookup settings."""

WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"
WIKIDATA_SEARCH_PAGE_SIZE = 50
WIKIDATA_MAX_SEARCH_PAGES = 3

# Wikidata semantic identifiers.
WIKIDATA_P_INSTANCE_OF = "P31"
WIKIDATA_P_SUBCLASS_OF = "P279"
WIKIDATA_P_COUNTRY = "P17"
WIKIDATA_P_ISO_COUNTRY_CODE = "P297"
WIKIDATA_P_ELEVATION = "P2044"

WIKIDATA_Q_MOUNTAIN = "Q8502"
WIKIDATA_Q_METRE = "Q11573"

# Maximum subclass links and unique class nodes to inspect.
WIKIDATA_CLASS_MAX_HOPS = 3
WIKIDATA_CLASS_MAX_NODES = 24

# wbgetentities permits at most 50 IDs for ordinary API clients.
WIKIDATA_ENTITY_BATCH_SIZE = 50

# Maximum candidate entities to verify per operation.
# This is an application-owned budget, not an LLM or plan-supplied argument.
WIKIDATA_MAX_VERIFY_CANDIDATES = 12
