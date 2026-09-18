"""
research/compare_smc_tm.py
So sánh công bằng SMC (M5) vs Trend Momentum (M1) trên CÙNG kỳ dữ liệu,
cùng engine Backtester, cùng lot/spread/vốn.

Dùng một nguồn M1 liên tục (XAUUSD_M1_100000.csv, 2026-06-04 → 09-15) để
tránh vấn đề thiếu nến. TM cần ~200 nến H1 cho EMA200 nên không chia đoạn.
"""
import io
import os
import sys
import contextlib

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.engine import Backtester
from research.smc import metrics, show, header, load_m5  # noqa: E402
from strategies.smc import SMCSweepChochStrategy
from strategies.trend_momentum import TrendMomentumStrategy

M1_FILE = "backtest/data/XAUUSD_M1_100000.csv"
SPREAD = 0.22
DIGITS = 2
LOT = 0.02   # partial 50%@1R (mặc định SMC + TM) cần lot >= 0.02
INITIAL = 1000.0


def load_m1():
    d = pd.read_csv(M1_FILE)
    d["time"] = pd.to_datetime(d["time"])
    cols = ["time", "open", "high", "low", "close"]
    if "spread" in d.columns:
        cols.append("spread")
    d = d[cols].drop_duplicates("time")
    return d.sort_values("time").set_index("time")


def to_m5(m1):
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "spread" in m1.columns:
        agg["spread"] = "mean"
    m5 = m1.resample("5min").agg(agg).dropna()
    m5.index.name = "time"
    return m5


def run(strategy, df):
    t = Backtester(strategy=strategy, initial_balance=INITIAL, lot_size=LOT,
                   digits=DIGITS, spread=SPREAD)
    with contextlib.redirect_stdout(io.StringIO()):
        return t.run(df)


def split(trades, ts):
    tr = [t for t in trades if pd.Timestamp(t["entry_time"]) < ts]
    oo = [t for t in trades if pd.Timestamp(t["entry_time"]) >= ts]
    return tr, oo


def main():
    m1 = load_m1()
    m5 = to_m5(m1)
    split_ts = m1.index[int(len(m1) * 0.7)]
    print(f"M1: {len(m1):,} nến | M5: {len(m5):,} nến | {m1.index[0]} → {m1.index[-1]}")
    print(f"Mốc train/OOS: {split_ts}\n")

    tm_tr = run(TrendMomentumStrategy(), m1)
    smc_tr = run(SMCSweepChochStrategy(), m5)

    for name, trades in (("Trend Momentum (M1)", tm_tr), ("SMC Sweep→CHoCH→FVG (M5)", smc_tr)):
        tr, oo = split(trades, split_ts)
        print(f"=== {name} ===")
        header()
        show("  train", metrics(tr))
        show("  OOS  ", metrics(oo))
        show("  ALL  ", metrics(trades))
        print()


if __name__ == "__main__":
    main()
