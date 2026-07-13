"""Point-in-time, headline-only evidence from the no-key GDELT DOC 2.0 API."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from orum.llm.contracts import ContractError, Evidence


GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_QUERY = (
    '(bitcoin OR BTC OR cryptocurrency OR crypto) '
    '(market OR inflation OR "interest rates" OR "central bank" OR regulation '
    "OR sanctions OR war OR election OR tariff)"
)

# GDELT's official DOC 2.0 guide documents ArtList JSON, precise UTC
# STARTDATETIME/ENDDATETIME, DateDesc sorting and a maximum of 250 records:
# https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/


class NewsProviderError(RuntimeError):
    """Raised when the news boundary cannot produce a trustworthy batch."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _sanitize_text(value: object, *, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    without_controls = "".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in value)
    return " ".join(without_controls.split())[:maximum].strip()


def _title_key(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.casefold(), flags=re.UNICODE))


def _canonical_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return None
    host = parts.hostname.lower()
    if parts.port and not (
        (parts.scheme.lower() == "http" and parts.port == 80)
        or (parts.scheme.lower() == "https" and parts.port == 443)
    ):
        host = f"{host}:{parts.port}"
    query = urlencode(
        sorted(
            (key, item)
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}
        )
    )
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), host, path, query, ""))


def _published_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    for pattern in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(value, pattern).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


class GdeltNewsProvider:
    """Fetch normalized headlines only; article bodies are never downloaded."""

    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        clock: Callable[[], datetime] = _utcnow,
        timeout_seconds: float = 20.0,
        query: str = GDELT_QUERY,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be non-empty")
        self._http_client = http_client or httpx.Client()
        self._clock = clock
        self.timeout_seconds = float(timeout_seconds)
        self.query = query.strip()

    def fetch(
        self,
        *,
        cutoff: datetime,
        lookback_hours: float = 6,
        limit: int = 30,
    ) -> tuple[Evidence, ...]:
        if cutoff.tzinfo is None:
            raise ValueError("cutoff must include a timezone")
        if not isinstance(lookback_hours, (int, float)) or lookback_hours <= 0:
            raise ValueError("lookback_hours must be positive")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 250:
            raise ValueError("limit must be between 1 and 250")
        cutoff_utc = cutoff.astimezone(UTC)
        start = cutoff_utc - timedelta(hours=float(lookback_hours))
        retrieved_at = self._clock()
        if retrieved_at.tzinfo is None:
            raise ValueError("retrieval clock must include a timezone")
        retrieved_at = retrieved_at.astimezone(UTC)
        params = {
            "query": self.query,
            "mode": "artlist",
            "maxrecords": str(limit),
            "format": "json",
            "sort": "datedesc",
            "startdatetime": start.strftime("%Y%m%d%H%M%S"),
            "enddatetime": cutoff_utc.strftime("%Y%m%d%H%M%S"),
        }
        try:
            response = self._http_client.get(
                GDELT_DOC_URL,
                params=params,
                timeout=self.timeout_seconds,
            )
        except httpx.TransportError as exc:
            raise NewsProviderError(f"GDELT transport failure: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise NewsProviderError(f"GDELT HTTP {response.status_code}")
        try:
            envelope = response.json()
        except ValueError as exc:
            raise NewsProviderError("GDELT returned invalid JSON") from exc
        if not isinstance(envelope, Mapping):
            raise NewsProviderError("GDELT response must be an object")
        articles = envelope.get("articles")
        if isinstance(articles, (str, bytes)) or not isinstance(articles, Sequence):
            raise NewsProviderError("GDELT response articles must be a list")

        candidates: list[tuple[datetime, str, str, Mapping[str, object]]] = []
        for raw in articles:
            if not isinstance(raw, Mapping):
                continue
            title = _sanitize_text(raw.get("title"), maximum=300)
            title_key = _title_key(title)
            url = _canonical_url(raw.get("url"))
            published = _published_at(raw.get("seendate"))
            if not title or not title_key or url is None or published is None:
                continue
            if published < start or published > cutoff_utc:
                continue
            candidates.append((published, url, title, raw))

        candidates.sort(key=lambda row: row[0], reverse=True)
        evidence: list[Evidence] = []
        seen_urls: set[str] = set()
        seen_titles: set[str] = set()
        for published, url, title, raw in candidates:
            title_key = _title_key(title)
            if url in seen_urls or title_key in seen_titles:
                continue
            seen_urls.add(url)
            seen_titles.add(title_key)
            publisher = _sanitize_text(raw.get("domain"), maximum=253).lower()
            if not publisher:
                publisher = urlsplit(url).hostname or ""
            digest = hashlib.sha256(
                f"{url}|{published.isoformat()}".encode("utf-8")
            ).hexdigest()
            try:
                item = Evidence.from_mapping(
                    {
                        "evidence_id": f"ev-gdelt-{digest[:24]}",
                        "kind": "headline",
                        "source": "gdelt_doc_2",
                        "observed_at": retrieved_at.isoformat(),
                        "published_at": published.isoformat(),
                        "title": title,
                        "url": url,
                        "payload": {
                            "publisher_domain": publisher,
                            "language": _sanitize_text(raw.get("language"), maximum=80) or None,
                            "source_country": _sanitize_text(raw.get("sourcecountry"), maximum=120) or None,
                            "timestamp_semantics": "gdelt_seendate",
                        },
                        "untrusted_text": True,
                    }
                )
            except ContractError:
                continue
            evidence.append(item)
            if len(evidence) >= limit:
                break
        return tuple(evidence)
