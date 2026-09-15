"""
research/trend_momentum.py
Nghiên cứu chiến lược Trend Momentum trên XAUUSD M1.
Chỉ backtest/phân tích trên dữ liệu lịch sử — KHÔNG đụng bot.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.data_loader import get_historical_data

SYMBOL = "XAUUSD"
SPREAD = 0.22      # spread thật broker (đơn vị price)
DIGITS = 2
LOT = 0.01
CONTRACT = 100.0
INITIAL = 100.0
START_DATE = "2026-06-01"
SESSION = (12, 21)  # London + New York UTC


def load_m1_chunked(target_rows=150000):
    """Nếu copy_rates_range M1 lỗi (giới hạn terminal), tải theo cụm từ pos 0."""
    import MetaTrader5 as mt5
    from core import mt5_handler as mt5h

    os.makedirs("backtest/data", exist_ok=True)
    cache = os.path.join("backtest/data", f"{SYMBOL}_M1_{target_rows}.csv")
    if os.path.exists(cache):
        print(f"Lấy M1 từ CACHE: {cache}")
        df = pd.read_csv(cache, index_col="time", parse_dates=True)
        return df.reset_index()

    if not mt5h.connect():
        return None
    try:
        pieces = []
        total = 0
        while total < target_rows:
            rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M1, total, 60000)
            if rates is None or len(rates) == 0:
                break
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            df.rename(columns={"tick_volume": "volume"}, inplace=True)
            pieces.append(df)
            total += len(rates)
            if len(rates) < 60000:
                break
        df = pd.concat(pieces).drop_duplicates(subset="time").sort_values("time")
        df = df[(df["time"] >= pd.Timestamp(START_DATE))].reset_index(drop=True)
        df.to_csv(cache, index=False)
        print(f"Đã lưu M1: {cache} ({len(df):,} nến)")
        return df
    finally:
        mt5h.disconnect()


def load_all():
    m1 = get_historical_data(SYMBOL, "M1", start_date=START_DATE, use_cache=True)
    if m1 is None:
        m1 = load_m1_chunked()
    m15 = get_historical_data(SYMBOL, "M15", start_date=START_DATE, use_cache=True)
    h1 = get_historical_data(SYMBOL, "H1", start_date=START_DATE, use_cache=True)
    for d in (m1, m15, h1):
        if d is None:
            raise RuntimeError("Không tải được dữ liệu!")
    parts = []
    for d in (m1, m15, h1):
        d = d.reset_index()
        d["time"] = pd.to_datetime(d["time"]).astype("datetime64[us]")
        parts.append(d.sort_values("time"))
    return parts


def atr(df, period=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def wilder_rsi(close, period=7):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def build(m1, m15, h1):
    m15 = m15.copy()
    m15["atr15"] = atr(m15, 14).shift(1)
    h1 = h1.copy()
    h1["ema200"] = h1["close"].ewm(span=200, adjust=False).mean().shift(1)
    mm = pd.merge_asof(m1, m15[["time", "atr15"]], on="time", direction="backward")
    mm = pd.merge_asof(mm, h1[["time", "ema200"]], on="time", direction="backward")
    mm = mm.dropna(subset=["atr15", "ema200"]).reset_index(drop=True)
    mm["rsi"] = wilder_rsi(mm["close"])
    mm["hour"] = mm["time"].dt.hour
    return mm


def build_signals(mm, lookback, rsi_buy, rsi_sell, session):
    high = mm["high"]
    low = mm["low"]
    prior_high = high.shift(1).rolling(lookback).max()
    prior_low = low.shift(1).rolling(lookback).min()
    in_session = mm["hour"].between(SESSION[0], SESSION[1]) if session else pd.Series(True, index=mm.index)
    buy = (mm["close"] > mm["ema200"]) & (mm["close"] > prior_high) & (mm["rsi"] >= rsi_buy) & in_session
    sell = (mm["close"] < mm["ema200"]) & (mm["close"] < prior_low) & (mm["rsi"] <= rsi_sell) & in_session
    return buy.to_numpy(), sell.to_numpy()


def run(mm, buy_sig, sell_sig, sl_atr, tp_r, be_move):
    o = mm["open"].to_numpy()
    h = mm["high"].to_numpy()
    l = mm["low"].to_numpy()
    atr15 = mm["atr15"].to_numpy()
    times = mm["time"].to_numpy()
    n = len(mm)

    balance = INITIAL
    pos = None
    trades = []
    balance_curve = []

    for i in range(0, n - 1):
        closed_here = False
        if pos is not None:
            p = pos
            entry, sl, tp, typ = p["entry"], p["sl"], p["tp"], p["typ"]
            if be_move and typ == "BUY":
                if h[i] >= entry + (entry - sl):
                    sl = entry
                    p["sl"] = sl
            elif be_move and typ == "SELL":
                if l[i] <= entry - (sl - entry):
                    sl = entry
                    p["sl"] = sl

            exit_px = None
            result = None
            if typ == "BUY":
                if l[i] <= sl:
                    exit_px, result = sl, "LOSS"
                elif h[i] >= tp:
                    exit_px, result = tp, "PROFIT"
            else:
                if h[i] + SPREAD >= sl:      # SELL chạm SL khi giá ASK lên
                    exit_px, result = sl, "LOSS"
                elif l[i] + SPREAD <= tp:    # SELL chạm TP khi giá ASK xuống
                    exit_px, result = tp, "PROFIT"

            if result:
                pnl = (exit_px - entry) * LOT * CONTRACT if typ == "BUY" else (entry - exit_px) * LOT * CONTRACT
                balance += pnl
                trades.append(dict(entry=entry, exit=exit_px, result=result, pnl=pnl,
                                   entry_time=p["time"], exit_time=times[i]))
                balance_curve.append(balance)
                pos = None
                closed_here = True

        if pos is None and not closed_here:
            if buy_sig[i]:
                entry = round(o[i + 1] + SPREAD, DIGITS)
                sd = sl_atr * atr15[i]
                pos = dict(typ="BUY", entry=entry, sl=round(entry - sd, DIGITS),
                           tp=round(entry + tp_r * sd, DIGITS), time=times[i + 1])
            elif sell_sig[i]:
                entry = round(o[i + 1], DIGITS)
                sd = sl_atr * atr15[i]
                pos = dict(typ="SELL", entry=entry, sl=round(entry + sd, DIGITS),
                           tp=round(entry - tp_r * sd, DIGITS), time=times[i + 1])

    return summarize(trades, balance_curve)


def summarize(trades, balance_curve):
    total = len(trades)
    if total == 0:
        return dict(trades=0, winrate=0, profit=0, final=INITIAL, avg_win=0, avg_loss=0,
                    payoff=0, expectancy=0, max_dd=0)
    wins = [t for t in trades if t["result"] == "PROFIT"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    avg_win = np.mean([t["pnl"] for t in wins]) if wins else 0
    avg_loss = np.mean([abs(t["pnl"]) for t in losses]) if losses else 0
    profit = balance_curve[-1] - INITIAL
    peak = INITIAL
    max_dd = 0
    for b in balance_curve:
        peak = max(peak, b)
        max_dd = max(max_dd, peak - b)
    return dict(
        trades=total,
        winrate=len(wins) / total * 100,
        profit=round(profit, 2),
        final=round(balance_curve[-1], 2),
        avg_win=round(avg_win, 2),
        avg_loss=round(avg_loss, 2),
        payoff=round(avg_win / avg_loss, 2) if avg_loss else 0,
        expectancy=round(float(profit) / total, 2) if total else 0,
        max_dd=round(max_dd, 2),
    )


def print_table(header, rows, split_at):
    print(f"{'config':<34}{'n':>5} {'WR%':>6} {'profit':>8} {'final':>8} {'avgW':>6} {'avgL':>6} {'pay':>5} {'EXP':>6} {'DD':>7}")
    print("-" * 100)
    for i, (name, s, seg) in enumerate(zip(header, rows, split_at)):
        mark = "  <-- OOS" if seg == "oos" else ""
        print(f"{name:<34}{s['trades']:>5} {s['winrate']:>6.1f} {s['profit']:>8.2f} {s['final']:>8.2f} "
              f"{s['avg_win']:>6.2f} {s['avg_loss']:>6.2f} {s['payoff']:>5.2f} {s['expectancy']:>6.2f} {s['max_dd']:>7.2f}{mark}")


def main():
    m1, m15, h1 = load_all()
    mm = build(m1, m15, h1)
    n = len(mm)
    split = int(n * 0.7)
    train = mm.iloc[:split].copy()
    oos = mm.iloc[split:].copy()
    print(f"Nến M1: {n:,} | train: {split:,} | oos: {n - split:,}")
    print()

    variants = [
        dict(name="base rsi55/20 / sl1.5 tp2 / BE / sess", lookback=20, rsi_buy=55, rsi_sell=45, session=True, sl_atr=1.5, tp_r=2.0, be_move=True),
        dict(name="sl1.5 tp3", lookback=20, rsi_buy=55, rsi_sell=45, session=True, sl_atr=1.5, tp_r=3.0, be_move=True),
        dict(name="sl2.0 tp2", lookback=20, rsi_buy=55, rsi_sell=45, session=True, sl_atr=2.0, tp_r=2.0, be_move=True),
        dict(name="sl2.0 tp3", lookback=20, rsi_buy=55, rsi_sell=45, session=True, sl_atr=2.0, tp_r=3.0, be_move=True),
        dict(name="lookback 15", lookback=15, rsi_buy=55, rsi_sell=45, session=True, sl_atr=1.5, tp_r=2.0, be_move=True),
        dict(name="lookback 30", lookback=30, rsi_buy=55, rsi_sell=45, session=True, sl_atr=1.5, tp_r=2.0, be_move=True),
        dict(name="rsi 60/40", lookback=20, rsi_buy=60, rsi_sell=40, session=True, sl_atr=1.5, tp_r=2.0, be_move=True),
        dict(name="rsi 50/50", lookback=20, rsi_buy=50, rsi_sell=50, session=True, sl_atr=1.5, tp_r=2.0, be_move=True),
        dict(name="no session", lookback=20, rsi_buy=55, rsi_sell=45, session=False, sl_atr=1.5, tp_r=2.0, be_move=True),
        dict(name="no BE move", lookback=20, rsi_buy=55, rsi_sell=45, session=True, sl_atr=1.5, tp_r=2.0, be_move=False),
    ]

    train_rows = []
    for v in variants:
        buy, sell = build_signals(train, v["lookback"], v["rsi_buy"], v["rsi_sell"], v["session"])
        s = run(train, buy, sell, v["sl_atr"], v["tp_r"], v["be_move"])
        train_rows.append((v, s))

    print("=== TRAIN (70% đầu) ===")
    print_table([v["name"] for v in variants], [s for _, s in train_rows], ["train"] * len(variants))

    best = sorted(train_rows, key=lambda x: x[1]["profit"], reverse=True)[:3]
    best_names = [b[0]["name"] for b in best]
    print()
    print("=== TOP 3 trên train → kiểm chứng OOS (30% cuối) ===")
    oos_header, oos_rows, oos_seg = [], [], []
    for bname in best_names:
        v = [x for x in variants if x["name"] == bname][0]
        buy, sell = build_signals(oos, v["lookback"], v["rsi_buy"], v["rsi_sell"], v["session"])
        s = run(oos, buy, sell, v["sl_atr"], v["tp_r"], v["be_move"])
        oos_header.append(bname)
        oos_rows.append(s)
        oos_seg.append("oos")
    print_table(oos_header, oos_rows, oos_seg)


if __name__ == "__main__":
    main()