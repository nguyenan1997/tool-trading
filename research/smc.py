"""
research/smc.py
Nghiên cứu chiến lược SMC (Sweep → CHoCH → OB/FVG) trên XAUUSD M5.
Chỉ backtest/phân tích trên dữ liệu lịch sử — KHÔNG đụng bot live.

Dữ liệu: gộp các file M1 cache → M5, tự tách thành các ĐOẠN LIÊN TỤC
(khoảng trống > 72h) để vị thế không nhảy qua gap dữ liệu giữa các nguồn.
Đánh giá: WR, payoff, Profit Factor, Expectancy, Max Drawdown, train/OOS.
"""
import io
import os
import sys
import contextlib

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from backtest.engine import Backtester
from strategies.smc import SMCSweepChochStrategy

SYMBOL = "XAUUSD"
SPREAD = 0.22
DIGITS = 2
LOT = 0.01
INITIAL = 1000.0
GAP_HOURS = 4
DATA_FILES = [
    "backtest/data/XAUUSD_M1_from_2026-02-23.csv",  # 2026-02-23 → 2026-04-28
    "backtest/data/XAUUSD_M1_from_2026-04-24.csv",  # 2026-04-23 → 2026-05-22
    "backtest/data/XAUUSD_M1_100000.csv",           # 2026-06-04 → 2026-09-15
    "backtest/data/XAUUSD_M1_90000.csv",            # 2026-06-18 → 2026-09-18
]


def load_m5():
    """Nạp các file M1 có sẵn, gộp lên M5, bỏ trùng theo thời gian."""
    frames = []
    for fp in DATA_FILES:
        if not os.path.exists(fp):
            continue
        d = pd.read_csv(fp)
        if "time" not in d.columns:
            print(f"  (bỏ qua {os.path.basename(fp)}: thiếu cột time)")
            continue
        d["time"] = pd.to_datetime(d["time"])
        frames.append(d[["time", "open", "high", "low", "close"]])
    if not frames:
        raise RuntimeError("Không tìm thấy file M1 cache nào!")
    m1 = (
        pd.concat(frames)
        .drop_duplicates(subset="time")
        .sort_values("time")
        .reset_index(drop=True)
    )
    m1 = m1.set_index("time")
    m5 = (
        m1.resample("5min")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    m5.index.name = "time"
    return m5


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


def main():
    m5 = load_m5()
    print(f"Dữ liệu M5: {len(m5):,} nến | {m5.index[0]} → {m5.index[-1]}")
    split = int(len(m5) * 0.7)
    split_ts = m5.index[split]
    print(f"Mốc train/OOS: {split_ts}\n")

    variants = [
        dict(name="DEFAULT config.py"),
        dict(name="FVG+sw.3 nocap tp3", require_fvg=True, min_sweep_atr=0.3, tp_r=3.0,
             min_r_atr=0.0, max_r_atr=999),
        dict(name="FVG+sw.3 R 0.5-4", require_fvg=True, min_sweep_atr=0.3, tp_r=3.0,
             min_r_atr=0.5, max_r_atr=4.0),
        dict(name="FVG+sw.3 R 0.5-3", require_fvg=True, min_sweep_atr=0.3, tp_r=3.0,
             min_r_atr=0.5, max_r_atr=3.0),
        dict(name="FVG+sw.3 R 0.8-2.5", require_fvg=True, min_sweep_atr=0.3, tp_r=3.0,
             min_r_atr=0.8, max_r_atr=2.5),
        dict(name="FVG+sw.3 R 1-2", require_fvg=True, min_sweep_atr=0.3, tp_r=3.0,
             min_r_atr=1.0, max_r_atr=2.0),
        dict(name="FVG+sw.3 R.8-2.5 tp2", require_fvg=True, min_sweep_atr=0.3, tp_r=2.0,
             min_r_atr=0.8, max_r_atr=2.5),
        dict(name="FVG+sw.3 R.8-2.5 tp4", require_fvg=True, min_sweep_atr=0.3, tp_r=4.0,
             min_r_atr=0.8, max_r_atr=2.5),
        dict(name="FVG+sw.3 R.8-2.5 e0", require_fvg=True, min_sweep_atr=0.3, tp_r=3.0,
             min_r_atr=0.8, max_r_atr=2.5, entry_frac=0.0),
        dict(name="FVG+sw.3 R.8-2.5 e.25", require_fvg=True, min_sweep_atr=0.3, tp_r=3.0,
             min_r_atr=0.8, max_r_atr=2.5, entry_frac=0.25),
        dict(name="FVG+sw.3 R.8-2.5 sw2", require_fvg=True, min_sweep_atr=0.3, tp_r=3.0,
             min_r_atr=0.8, max_r_atr=2.5, swing_k=2),
        dict(name="FVG+sw.3 R.8-2.5 sw4", require_fvg=True, min_sweep_atr=0.3, tp_r=3.0,
             min_r_atr=0.8, max_r_atr=2.5, swing_k=4),
        dict(name="FVG+sw.3 R.8-2.5 bias", require_fvg=True, min_sweep_atr=0.3, tp_r=3.0,
             min_r_atr=0.8, max_r_atr=2.5, use_bias=True),
        dict(name="FVG+sw.15 R.8-2.5", require_fvg=True, min_sweep_atr=0.15, tp_r=3.0,
             min_r_atr=0.8, max_r_atr=2.5),
        dict(name="sw.3 R.8-2.5 (no FVG)", min_sweep_atr=0.3, tp_r=3.0,
             min_r_atr=0.8, max_r_atr=2.5),
    ]

    print("=== TRAIN (70% đầu) ===")
    header()
    scored = []
    for v in variants:
        strat = SMCSweepChochStrategy(**{k: x for k, x in v.items() if k != "name"})
        trades = run_segmented(strat, m5)
        tr, oos = split_trades(trades, split_ts)
        m_tr, m_oos, m_all = metrics(tr), metrics(oos), metrics(trades)
        show(v["name"], m_tr)
        robust = min(m_tr["pf"], m_oos["pf"]) if np.isfinite(m_tr["pf"]) and np.isfinite(m_oos["pf"]) else 0.0
        scored.append((v, m_tr, m_oos, m_all, robust))

    print("\n=== BỀN VỮNG: train & OOS (sắp theo min(PF_train, PF_oos)) ===")
    header()
    eligible = [x for x in scored if x[1]["n"] >= 15 and x[2]["n"] >= 12]
    eligible.sort(key=lambda x: x[4], reverse=True)
    for v, m_tr, m_oos, m_all, robust in eligible:
        show(f"{v['name']} [OOS]", m_oos)

    best_v, _, _, best_all, _ = eligible[0] if eligible else scored[0]
    strat = SMCSweepChochStrategy(**{k: x for k, x in best_v.items() if k != "name"})
    trades = run_segmented(strat, m5)
    print(f"\n=== Toàn bộ dữ liệu — best bền vững: {best_v['name']} ===")
    header()
    show("ALL", metrics(trades))
    fold_report(strat, m5, k=4)
    by_month(trades)
    extremes(trades)


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
    print("\n--- Theo tháng (best, toàn dữ liệu) ---")
    print(f"{'month':<10}{'n':>5} {'WR%':>6} {'net':>9} {'EXP':>7}")
    for ym, g in df.groupby("ym"):
        pnl = g["pnl"].to_numpy()
        wr = (pnl > 0).mean() * 100
        print(f"{str(ym):<10}{len(g):>5} {wr:>6.1f} {pnl.sum():>9.2f} {pnl.mean():>7.2f}")


def extremes(trades):
    if not trades:
        return
    pnl = np.array([t["pnl"] for t in trades])
    print("\n--- Phân bố PnL (best) ---")
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


if __name__ == "__main__":
    main()
