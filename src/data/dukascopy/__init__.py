"""Dukascopy public historical datafeed helpers."""

from src.data.dukascopy.batch import DukascopyBatch, DukascopyBatchPlan, build_batch_plan
from src.data.dukascopy.bi5 import (
    DukascopyDownloadSummary,
    DukascopyTickDownloader,
    build_dukascopy_bi5_url,
    decompress_bi5,
    empty_bi5_cache_path,
    ohlcv_cache_path,
    parse_tick_bi5,
    raw_bi5_cache_path,
    resample_ticks_to_ohlcv,
    ticks_cache_path,
)
from src.data.dukascopy.qa import (
    DukascopyGap,
    DukascopyQAReport,
    GapClassification,
    build_qa_report,
    load_cached_ohlcv,
)

__all__ = [
    "DukascopyBatch",
    "DukascopyBatchPlan",
    "DukascopyDownloadSummary",
    "DukascopyGap",
    "DukascopyImportReport",
    "DukascopyQAReport",
    "DukascopyTickDownloader",
    "GapClassification",
    "build_dukascopy_bi5_url",
    "build_batch_plan",
    "build_qa_report",
    "decompress_bi5",
    "empty_bi5_cache_path",
    "load_cached_ohlcv",
    "import_dukascopy_ohlcv_cache",
    "ohlcv_cache_path",
    "parse_tick_bi5",
    "raw_bi5_cache_path",
    "resample_ticks_to_ohlcv",
    "ticks_cache_path",
]


def __getattr__(name: str):
    """Lazily expose DB-backed importer helpers without loading DB settings."""
    if name in {"DukascopyImportReport", "import_dukascopy_ohlcv_cache"}:
        from src.data.dukascopy.importer import (  # noqa: PLC0415
            DukascopyImportReport,
            import_dukascopy_ohlcv_cache,
        )

        return {
            "DukascopyImportReport": DukascopyImportReport,
            "import_dukascopy_ohlcv_cache": import_dukascopy_ohlcv_cache,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
