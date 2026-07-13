from datetime import UTC, datetime

import httpx
import pytest

from orum.llm.news import GdeltNewsProvider, NewsProviderError


CUTOFF = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
RETRIEVED = datetime(2026, 7, 13, 12, 1, tzinfo=UTC)


def test_gdelt_query_is_point_in_time_deduplicated_and_untrusted():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "articles": [
                    {
                        "url": "https://News.Example/a?utm_source=x#fragment",
                        "title": "Bitcoin rallies\u0000 after CPI",
                        "seendate": "20260713T115500Z",
                        "domain": "news.example",
                        "language": "English",
                        "sourcecountry": "United States",
                    },
                    {
                        "url": "https://news.example/a?utm_medium=y",
                        "title": "Bitcoin rallies after CPI",
                        "seendate": "20260713T115400Z",
                        "domain": "news.example",
                        "language": "English",
                    },
                    {
                        "url": "https://second.example/story",
                        "title": "  BITCOIN   RALLIES AFTER CPI  ",
                        "seendate": "20260713T115300Z",
                        "domain": "second.example",
                        "language": "English",
                    },
                    {
                        "url": "https://macro.example/rates",
                        "title": "Central bank surprises markets",
                        "seendate": "20260713T113000Z",
                        "domain": "macro.example",
                        "language": "French",
                        "sourcecountry": "France",
                    },
                    {
                        "url": "https://future.example/story",
                        "title": "Future headline must not leak",
                        "seendate": "20260713T120100Z",
                        "domain": "future.example",
                        "language": "English",
                    },
                    {
                        "url": "https://old.example/story",
                        "title": "Older than lookback",
                        "seendate": "20260713T050000Z",
                        "domain": "old.example",
                        "language": "English",
                    },
                ]
            },
        )

    provider = GdeltNewsProvider(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: RETRIEVED,
    )
    evidence = provider.fetch(cutoff=CUTOFF, lookback_hours=6, limit=30)

    assert [item.title for item in evidence] == [
        "Bitcoin rallies after CPI",
        "Central bank surprises markets",
    ]
    assert [item.published_at for item in evidence] == [
        datetime(2026, 7, 13, 11, 55, tzinfo=UTC),
        datetime(2026, 7, 13, 11, 30, tzinfo=UTC),
    ]
    assert evidence[0].url == "https://news.example/a"
    assert evidence[0].source == "gdelt_doc_2"
    assert evidence[0].untrusted_text is True
    assert evidence[0].observed_at == RETRIEVED
    assert evidence[0].payload["publisher_domain"] == "news.example"
    assert evidence[1].payload["source_country"] == "France"
    assert all(item.evidence_id.startswith("ev-gdelt-") for item in evidence)
    assert seen["params"]["mode"] == "artlist"
    assert seen["params"]["format"] == "json"
    assert seen["params"]["sort"] == "datedesc"
    assert seen["params"]["startdatetime"] == "20260713060000"
    assert seen["params"]["enddatetime"] == "20260713120000"
    assert seen["params"]["maxrecords"] == "30"
    assert "bitcoin" in seen["params"]["query"].lower()
    assert "inflation" in seen["params"]["query"].lower()


def test_evidence_id_is_stable_across_retrieval_times():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "articles": [
                    {
                        "url": "https://example.test/story",
                        "title": "A stable headline",
                        "seendate": "20260713T110000Z",
                        "domain": "example.test",
                        "language": "English",
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    first = GdeltNewsProvider(http_client=client, clock=lambda: RETRIEVED).fetch(cutoff=CUTOFF)
    second = GdeltNewsProvider(
        http_client=client,
        clock=lambda: datetime(2026, 7, 13, 12, 5, tzinfo=UTC),
    ).fetch(cutoff=CUTOFF)

    assert first[0].evidence_id == second[0].evidence_id


@pytest.mark.parametrize("payload", [{"articles": "wrong"}, ["wrong"]])
def test_gdelt_rejects_malformed_response(payload):
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
    )
    provider = GdeltNewsProvider(http_client=client)

    with pytest.raises(NewsProviderError):
        provider.fetch(cutoff=CUTOFF)


def test_gdelt_http_failure_is_visible():
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(503, text="busy"))
    )

    with pytest.raises(NewsProviderError, match="HTTP 503"):
        GdeltNewsProvider(http_client=client).fetch(cutoff=CUTOFF)
