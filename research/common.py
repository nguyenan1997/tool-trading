"""
research/common.py
Tiện ích dùng chung cho các script nghiên cứu backtest (ngoài bot live).

Nguồn dữ liệu: gộp các file M1 cache → nến khung mong muốn, tự tách đoạn liên tục
(gap > GAP_HOURS) để vị thế không nhảy qua lỗ hổng dữ liệu giữa các nguồn.
Đánh giá: WR, Profit Factor, Expectancy, Max Drawdown, train/OOS.
"""
import io
import os
import contextlib

import numpy as np
import pandas as pd

import config
from backtest.engine import Backtester

SYMBOL = "XAUUSD"
SPREAD = 0.22
DIGITS = 2
LOT = 0.02
INITIAL = 1000.0
GAP_HOURS = 4

DATA_FILES = [
    "backtest/data/XAUUSD_M1_from_2026-02-23.csv",  # 2026-02-23 → 2026-04-28
    "backtest/data/XAUUSD_M1_from_2026-04-24.csv",  # 2026-04-23 → 2026-05-22
    "backtest/data/XAUUSD_M1_100000.csv",           # 2026-06-04 → 2026-09-15
    "backtest/data/XAUUSD_M1_90000.csv",            # 2026-06-18 → 2026-09-18
]


def load_m5(files=None, rule="5min"):
    """Nạp các file M1 có sẵn, gộp lên khung `rule`, bỏ trùng theo thời gian."""
    files = files or DATA_FILES
    frames = []
    for fp in files:
        if not os.path.exists(fp):
            continue
        d = pd.read_csv(fp)
        if "time" not in d.columns:
            print(f"  (bỏ qua {os.path.basename(fp)}: thiếu cột time)")
            continue
        d["time"] = pd.to_datetime(d["time"])
        cols = ["time", "open", "high", "low", "close"]
        if "spread" in d.columns:
            cols.append("spread")
        if "volume" in d.columns:
            cols.append("volume")
        frames.append(d[cols])
    if not frames:
        raise RuntimeError("Không tìm thấy file M1 cache nào!")
    m1 = (
        pd.concat(frames)
        .drop_duplicates(subset="time")
        .sort_values("time")
        .reset_index(drop=True)
    )
    m1 = m1.set_index("time")
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "spread" in m1.columns:
        agg["spread"] = "mean"   # spread trung bình của các nến M1 trong nến gộp
    if "volume" in m1.columns:
        agg["volume"] = "sum"    # tổng tick-volume của các nến M1
    out = m1.resample(rule).agg(agg).dropna()
    out.index.name = "time"
    return out


def run_once(strategy, df, silent=True):
    tester = Backtester(
        strategy=strategy,
        initial_balance=INITIAL,
        lot_size=LOT,
        digits=DIGITS,
        spread=SPREAD,
    )
    if silent:
        with contextlib.redirect_stdout(io.StringIO()):
            trades = tester.run(df)
    else:
        trades = tester.run(df)
    return trades


def run_segmented(strategy, df, gap_hours=GAP_HOURS):
    """Chạy backtest trên từng đoạn liên tục của dữ liệu rồi gộp lệnh."""
    gaps = df.index.to_series().diff().dt.total_seconds().fillna(0.0)
    seg_id = (gaps > gap_hours * 3600).cumsum()
    trades = []
    for _, seg in df.groupby(seg_id):
        if len(seg) > 200:
            trades.extend(run_once(strategy, seg))
    trades.sort(key=lambda t: pd.Timestamp(t["entry_time"]))
    return trades


def metrics(trades):
    pnl = np.array([t["pnl"] for t in trades], dtype=float)
    n = len(pnl)
    if n == 0:
        return dict(n=0, wr=0.0, pf=0.0, exp=0.0, net=0.0, avgW=0.0, avgL=0.0,
                    maxdd=0.0, ddpct=0.0, final=INITIAL, streak=0)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    gp = wins.sum()
    gl = -losses.sum()
    pf = gp / gl if gl > 1e-9 else float("inf")
    equity = INITIAL + np.cumsum(pnl)
    curve = np.concatenate([[INITIAL], equity])
    peak = np.maximum.accumulate(curve)
    dd = peak - curve
    maxdd = float(dd.max())
    peak_at = float(peak[int(np.argmax(dd))])
    ddpct = (maxdd / peak_at * 100) if peak_at > 0 else 0.0
    streak = cur = 0
    for p in pnl:
        cur = cur + 1 if p < 0 else 0
        streak = max(streak, cur)
    return dict(
        n=n,
        wr=float(len(wins) / n * 100),
        pf=float(pf),
        exp=float(pnl.mean()),
        net=float(pnl.sum()),
        avgW=float(wins.mean()) if len(wins) else 0.0,
        avgL=float(losses.mean()) if len(losses) else 0.0,
        maxdd=maxdd,
        ddpct=ddpct,
        final=float(INITIAL + pnl.sum()),
        streak=streak,
    )


def show(tag, m):
    pf = " inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
    print(f"{tag:<26}{m['n']:>5} {m['wr']:>6.1f} {pf:>6} {m['exp']:>7.2f} "
          f"{m['net']:>8.2f} {m['final']:>8.2f} {m['avgW']:>6.2f} {m['avgL']:>6.2f} "
          f"{m['ddpct']:>6.1f}% {m['streak']:>4}")


def header():
    print(f"{'config':<26}{'n':>5} {'WR%':>6} {'PF':>6} {'EXP':>7} {'net':>8} "
          f"{'final':>8} {'avgW':>6} {'avgL':>6} {'DD%':>7} {'lose':>4}")
    print("-" * 100)


def split_trades(trades, ts):
    tr = [t for t in trades if pd.Timestamp(t["entry_time"]) < ts]
    oos = [t for t in trades if pd.Timestamp(t["entry_time"]) >= ts]
    return tr, oos


def fold_report(strategy, df, k=4):
    """Chia dữ liệu thành k đoạn liên tiếp; chạy cùng tham số trên từng đoạn."""
    trades = run_segmented(strategy, df)
    if not trades:
        return
    tdf = pd.DataFrame(trades)
    tdf["entry_time"] = pd.to_datetime(tdf["entry_time"], errors="coerce")
    tdf = tdf.dropna(subset=["entry_time"])
    edges = pd.date_range(tdf["entry_time"].min(), tdf["entry_time"].max(), periods=k + 1)
    print(f"\n--- Walk-forward {k} fold (tham số cố định) ---")
    print(f"{'fold':<24}{'n':>5} {'WR%':>6} {'PF':>6} {'EXP':>7} {'net':>9}")
    for i in range(k):
        seg = tdf[(tdf["entry_time"] >= edges[i]) & (tdf["entry_time"] < edges[i + 1])]
        m = metrics(seg.to_dict("records"))
        pf = " inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
        print(f"{str(edges[i])[:16] + '→':<24}{m['n']:>5} {m['wr']:>6.1f} {pf:>6} "
              f"{m['exp']:>7.2f} {m['net']:>9.2f}")


def by_month(trades):
    if not trades:
        return
    df = pd.DataFrame(trades)
    df["entry_time"] = pd.to_datetime(df["entry_time"], errors="coerce")
    df = df.dropna(subset=["entry_time"])
    if df.empty:
        return
    df["ym"] = df["entry_time"].dt.to_period("M")
    print("\n--- Theo tháng (toàn dữ liệu) ---")
    print(f"{'month':<10}{'n':>5} {'WR%':>6} {'net':>9} {'EXP':>7}")
    for ym, g in df.groupby("ym"):
        pnl = g["pnl"].to_numpy()
        wr = (pnl > 0).mean() * 100
        print(f"{str(ym):<10}{len(g):>5} {wr:>6.1f} {pnl.sum():>9.2f} {pnl.mean():>7.2f}")


def extremes(trades):
    if not trades:
        return
    pnl = np.array([t["pnl"] for t in trades])
    print("\n--- Phân bố PnL ---")
    print(f"tổng {len(pnl)} | thắng {int((pnl > 0).sum())} | thua {int((pnl < 0).sum())} "
          f"| hòa {int((pnl == 0).sum())}")
    order = np.argsort(pnl)
    print("5 lệnh thua lớn nhất:")
    for j in order[:5]:
        t = trades[j]
        print(f"   {t['type']:<4} {t['entry_time']} -> {t['exit_time']} "
              f"entry={t['entry']} exit={t['exit']} pnl={t['pnl']:.2f}")
    print("5 lệnh thắng lớn nhất:")
    for j in order[-5:][::-1]:
        t = trades[j]
        print(f"   {t['type']:<4} {t['entry_time']} -> {t['exit_time']} "
              f"entry={t['entry']} exit={t['exit']} pnl={t['pnl']:.2f}")
    print(f"trung vị pnl: {np.median(pnl):.2f} | độ lệch chuẩn: {pnl.std():.2f}")
