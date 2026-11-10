"""Pre-build the 1-minute cache for many tickers so the UI never waits on it.

    python build_cache.py              # all tickers
    python build_cache.py AAPL MSFT    # only these
"""
import sys
import time

from core.data import build_minute_cache, list_tickers

tickers = sys.argv[1:] or list_tickers()
t0 = time.time()
for i, t in enumerate(tickers, 1):
    s = time.time()
    try:
        build_minute_cache(t)
        print(f"[{i}/{len(tickers)}] {t}  {time.time() - s:.1f}s")
    except Exception as e:  # keep going on bad files
        print(f"[{i}/{len(tickers)}] {t}  FAILED: {e}")
print(f"Done in {(time.time() - t0) / 60:.1f} min")
