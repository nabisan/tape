"""Central paths and constants.

Paths can be overridden per machine with environment variables, so the same
code runs on your Windows PC and on a Linux server (e.g. EC2):

    GT_DATA_DIR   folder with the raw 1-second parquet files (one per ticker)
    GT_CACHE_DIR  folder with the 1-minute cache (built on first use)

The raw folder is optional: if only the cache folder exists, the app runs from it.
"""
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("GT_DATA_DIR", r"D:\DataBento\data_split_adjusted"))
CACHE_DIR = Path(os.environ.get("GT_CACHE_DIR", ROOT_DIR / "cache" / "1m"))

# Spin-off adjustments (core/corporate_actions.csv).
# Since 09/11/2026 these factors are applied DIRECTLY in the raw files in
# D:\DataBento\data_split_adjusted (originals backed up in
# D:\DataBento\backup_before_spinoff_adjust), so the app must NOT apply them
# again on load. Set to True only when pointing GT_DATA_DIR at unadjusted data.
ACTIONS_FILE = ROOT_DIR / "core" / "corporate_actions.csv"
ADJUST_CORPORATE_ACTIONS = False

# Timestamps in the source are US/Eastern exchange-local time (04:00-20:00).
RTH_START = "09:30"
RTH_END = "16:00"

# label -> pandas offset alias ("1D" is handled separately: one bar per trading date)
TIMEFRAMES = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "1h": "60min",
    "4h": "240min",  # RTH: 09:30-13:30 and 13:30-16:00
    "1D": "1D",
}
DEFAULT_TIMEFRAMES = ["1D"]
