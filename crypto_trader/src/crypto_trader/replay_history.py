"""Frozen historical replay data protocol v1. Public spot data only.

Binance Vision monthly daily candles, including historical/delisted pairs when
explicitly supplied. A present-day symbol list is NOT a point-in-time universe.
No holdout data is downloaded by the development command.
"""
import argparse
import csv
import hashlib
import io
import json
import urllib.request
import urllib.error
import zipfile
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

START = date(2021, 11, 1)
DEV_END = date(2023, 11, 1)
SELECTION_END = date(2025, 11, 1)
END = date(2026, 11, 1)
BASE = "https://data.binance.vision/data/spot/monthly/klines"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
           "LINKUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT", "LTCUSDT")

def months(start, end):
    while start < end:
        yield start.strftime("%Y-%m")
        start = date(start.year + (start.month == 12), start.month % 12 + 1, 1)

def parse_archive(raw, month):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = archive.namelist()
        if len(names) != 1 or not names[0].endswith(".csv"):
            raise ValueError("unexpected archive")
        rows = []
        for row in csv.reader(io.StringIO(archive.read(names[0]).decode())):
            if row[0] == "open_time":
                continue
            ts = int(row[0])
            if ts >= 10**15:
                ts //= 1000
            day = datetime.fromtimestamp(ts / 1000, timezone.utc).date()
            if day.strftime("%Y-%m") != month or ts % 86400000:
                raise ValueError("unexpected timestamp")
            o,h,l,c,v = map(float,row[1:6])
            import math
            if not all(map(math.isfinite,(o,h,l,c,v))) or not (0 < l <= min(o,c) <= max(o,c) <= h and v >= 0):
                raise ValueError("invalid OHLCV")
            rows.append([ts,o,h,l,c,v])
        if any(a[0] >= b[0] for a,b in zip(rows,rows[1:])):
            raise ValueError("duplicate or unordered candles")
        return rows

def load(output, symbols=SYMBOLS, phase="development"):
    end = {"development": DEV_END, "selection": SELECTION_END}[phase]
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = {"protocol": 1, "phase": phase, "start": str(START),
                "end_exclusive": str(end), "holdout": [str(SELECTION_END),str(END)],
                "universe_limitation": "Retrospective fixed cohort; survivorship bias unresolved. First observed candle is not verified listing date.",
                "symbols": {}}
    for symbol in symbols:
        if not symbol.isascii() or not symbol.isalnum() or not symbol.endswith("USDT"):
            raise ValueError("invalid spot symbol")
        rows, sources = [], []
        for month in months(START,end):
            name = f"{symbol}-1d-{month}.zip"
            url = f"{BASE}/{symbol}/1d/{name}"
            path = output / name
            try:
                if not path.exists():
                    with urllib.request.urlopen(url, timeout=45) as response:
                        raw = response.read()
                    parse_archive(raw,month)
                    path.write_bytes(raw)
                raw = path.read_bytes()
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    raise
                sources.append({"month":month,"status":"missing_404","url":url})
                continue
            rows.extend(parse_archive(raw,month))
            sources.append({"month":month,"status":"available","url":url,
                            "sha256":hashlib.sha256(raw).hexdigest()})
        observed = {datetime.fromtimestamp(r[0]/1000,timezone.utc).date() for r in rows}
        expected = {START+timedelta(days=i) for i in range((end-START).days)}
        (output/f"{symbol}.json").write_text(json.dumps(rows,allow_nan=False))
        manifest["symbols"][symbol] = {"sources":sources,"count":len(rows),
            "first_observed":str(min(observed)) if observed else None,
            "last_observed":str(max(observed)) if observed else None,
            "missing_days":sorted(map(str,expected-observed))}
    (output/"manifest.json").write_text(json.dumps(manifest,indent=2))
    return manifest

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",required=True)
    parser.add_argument("--phase",choices=("development","selection"),default="development")
    args = parser.parse_args()
    load(args.output,phase=args.phase)
