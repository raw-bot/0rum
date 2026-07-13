from datetime import UTC, datetime

import pytest

from orum.llm.contracts import Evidence
from orum.llm.snapshot import MarketSnapshotBuilder, SnapshotError


CUTOFF = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


def _candle(hour, minute, close):
    opened = datetime(2026, 7, 13, hour, minute, tzinfo=UTC)
    return {
        "ts": int(opened.timestamp() * 1000),
        "open": close - 1,
        "high": close + 2,
        "low": close - 2,
        "close": close,
        "volume": 10,
    }


def _headline(published="2026-07-13T11:30:00+00:00", evidence_id="ev-1"):
    return Evidence.from_mapping(
        {
            "evidence_id": evidence_id,
            "kind": "headline",
            "source": "gdelt_doc_2",
            "observed_at": "2026-07-13T12:01:00+00:00",
            "published_at": published,
            "title": "A market headline",
            "url": "https://example.test/story",
            "payload": {"publisher_domain": "example.test"},
            "untrusted_text": True,
        }
    )


def _build(**overrides):
    values = {
        "cutoff": CUTOFF,
        "symbol": "BTC/USDT",
        "candles": {"15m": [_candle(11, 30, 101), _candle(11, 15, 100)]},
        "indicators": {"15m": {"atr": 2.5, "rsi": 55}},
        "derivatives": None,
        "macro": {},
        "onchain": {},
        "evidence": [_headline()],
        "paper_account": {"equity_usd": 10_000, "positions": []},
    }
    values.update(overrides)
    return MarketSnapshotBuilder(clock=lambda: datetime(2026, 7, 13, 12, 2, tzinfo=UTC)).build(
        **values
    )


def test_snapshot_orders_closed_candles_and_marks_missing_derivatives_explicitly():
    snapshot = _build()

    rows = snapshot.candles["15m"]
    assert [row["close"] for row in rows] == [100.0, 101.0]
    assert snapshot.derivatives == {"status": "unavailable"}
    assert snapshot.snapshot_id.startswith("snap-")
    assert len(snapshot.content_hash) == 64
    assert snapshot.to_mapping()["evidence"][0]["evidence_id"] == "ev-1"


@pytest.mark.parametrize(
    "candle",
    [
        _candle(11, 50, 102),  # 15m candle would close after cutoff
        _candle(12, 0, 102),   # future/forming candle starts at cutoff
    ],
)
def test_snapshot_rejects_forming_or_future_candles(candle):
    with pytest.raises(SnapshotError, match="closed at cutoff"):
        _build(candles={"15m": [_candle(11, 30, 101), candle]})


def test_snapshot_rejects_future_headline():
    with pytest.raises(SnapshotError, match="published after cutoff"):
        _build(evidence=[_headline("2026-07-13T12:00:01+00:00")])


def test_snapshot_keeps_headline_evidence_newest_first():
    snapshot = _build(
        evidence=[
            _headline("2026-07-13T10:00:00+00:00", "ev-old"),
            _headline("2026-07-13T11:30:00+00:00", "ev-new"),
        ]
    )

    assert [item.evidence_id for item in snapshot.evidence] == ["ev-new", "ev-old"]


def test_semantically_identical_mappings_have_the_same_hash():
    first = _build(
        indicators={"15m": {"atr": 2.5, "rsi": 55}},
        paper_account={"equity_usd": 10_000, "positions": [], "cash_usd": 10_000},
    )
    second = _build(
        indicators={"15m": {"rsi": 55, "atr": 2.5}},
        paper_account={"cash_usd": 10_000, "positions": [], "equity_usd": 10_000},
    )

    assert first.content_hash == second.content_hash
    assert first.snapshot_id == second.snapshot_id


def test_snapshot_rejects_duplicate_candle_and_evidence_ids():
    with pytest.raises(SnapshotError, match="duplicate candle"):
        _build(candles={"15m": [_candle(11, 30, 101), _candle(11, 30, 102)]})

    with pytest.raises(SnapshotError, match="duplicate evidence_id"):
        _build(evidence=[_headline(), _headline()])
