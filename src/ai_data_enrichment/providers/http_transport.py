"""HTTP transport for JSON-based providers."""

import json
from collections.abc import Mapping
from urllib.parse import urlencode
from urllib.request import urlopen


class UrllibJsonTransport:
    """Fetch JSON over HTTP using Python's standard library."""

    def get_json(
        self,
        *,
        url: str,
        params: Mapping[str, str],
        timeout_seconds: float,
    ) -> object:
        query_string = urlencode(params)
        request_url = f"{url}?{query_string}" if query_string else url

        with urlopen(request_url, timeout=timeout_seconds) as response:
            return json.loads(response.read())
