from __future__ import annotations

import json

from scripts.run_eth_edge_research import daily_gap_count, write_research_artifacts


DAY_MS = 86_400_000


def test_daily_gap_count_counts_missing_buckets_without_inventing_them():
    candles = [{"ts": 0}, {"ts": DAY_MS}, {"ts": 4 * DAY_MS}]

    assert daily_gap_count(candles) == 2


def test_write_research_artifacts_persists_each_source_and_report_atomically(tmp_path):
    datasets = {
        "coinbase": {"BTC/EUR": [{"ts": 1}], "ETH/EUR": [{"ts": 2}]},
        "kraken": {"BTC/EUR": [{"ts": 3}], "ETH/EUR": [{"ts": 4}]},
    }
    report = {"promotion": {"eligible": False}, "retrieved_at": "test"}

    paths = write_research_artifacts(tmp_path, datasets, report)

    assert sorted(path.name for path in paths) == [
        "coinbase_btc_eur_1d.json",
        "coinbase_eth_eur_1d.json",
        "eth_edge_report.json",
        "kraken_btc_eur_1d.json",
        "kraken_eth_eur_1d.json",
    ]
    assert json.loads((tmp_path / "eth_edge_report.json").read_text()) == report
    assert not list(tmp_path.glob("*.tmp"))
