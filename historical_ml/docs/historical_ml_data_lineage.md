# historical_ml data lineage

`data/etf_daily.csv` is a local replay input, not a committed artifact.
It is produced from the repository's ETF cache and universe metadata so the
historical replay can be reproduced without contacting QMT or emitting real-time
trading advice.

## Source

- Price cache: `data/cache/<symbol>.csv`
- Cache metadata: `data/cache/_metadata.csv`
- Core ETF pool for the Phase 1/2 validation run: `config/etf_pool.yaml`
- Optional full-universe metadata: `data/universe/etf_universe.csv`

## Required columns

`historical_ml` expects a long-format table with these fields:

```text
date, code, name, close, sector
```

Recommended optional fields:

```text
sector_l1, open, high, low, volume, amount
```

## Fallbacks

- If `sector_l1` is missing, it is set to `sector`.
- If `open`, `high`, or `low` is missing, it is set to `close`.
- If `volume` or `amount` is missing, it is set to `0`.
- `code` is normalized as text to preserve six-digit ETF symbols.

## Reproduce the Phase 1/2 local input

The acceptance run used the core ETF pool to keep replay runtime bounded:

```powershell
@'
from pathlib import Path
import re
import pandas as pd

root = Path(".")
pool_text = (root / "config" / "etf_pool.yaml").read_text(encoding="utf-8")
symbols = [m.group(1).zfill(6) for m in re.finditer(r'symbol:\s*"(\d+)"', pool_text)]
meta = pd.read_csv(root / "data" / "cache" / "_metadata.csv", dtype={"symbol": str})
meta["symbol"] = meta["symbol"].str.zfill(6)
meta_by_symbol = meta.set_index("symbol").to_dict("index")
frames = []

for symbol in symbols:
    info = meta_by_symbol.get(symbol, {})
    path = root / str(info.get("cache_path") or f"data/cache/{symbol}.csv")
    if not path.exists():
        continue
    df = pd.read_csv(path, dtype={"symbol": str})
    required = {"date", "open", "high", "low", "close", "volume", "amount"}
    if df.empty or not required.issubset(df.columns):
        continue
    name = str(info.get("name") or symbol)
    df = df.copy()
    df["code"] = symbol
    df["name"] = df.get("name", name).fillna(name) if "name" in df.columns else name
    df["name"] = df["name"].replace("", name)
    df["sector"] = name
    df["sector_l1"] = "core_pool"
    frames.append(df[["date", "code", "name", "sector", "sector_l1", "open", "high", "low", "close", "volume", "amount"]])

out = pd.concat(frames, ignore_index=True)
out["date"] = pd.to_datetime(out["date"], errors="coerce")
out = out.dropna(subset=["date", "code", "close"]).sort_values(["date", "code"])
out.to_csv(root / "data" / "etf_daily.csv", index=False, encoding="utf-8-sig", date_format="%Y-%m-%d")
'@ | python -
```

Then run:

```powershell
python -m historical_ml.cli run-all `
  --prices data/etf_daily.csv `
  --start 2024-09-24 `
  --end 2026-05-19 `
  --out artifacts/historical_ml `
  --format csv
```

## Artifact policy

`data/etf_daily.csv` and `artifacts/historical_ml/*` are generated local outputs.
They can be regenerated from cache and are intentionally not committed. This
keeps repository history focused on code, contracts, tests, and documentation.

## Historical window

- Replay start: `2024-09-24`
- Replay end: `2026-05-19`
- Future labels are attached only after replay. The final samples without enough
  future trading days must keep `label_status=insufficient_future_data` and
  `auto_label=unlabeled`.
