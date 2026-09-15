"""
research/trend_momentum_validate.py
Kiểm chứng bổ sung cho Trend Momentum:
  - Walk-forward (expanding window) 3 đoạn test ngoài mẫu
  - Nhãn chế độ thị trường (TREND / RANGE) theo ADX H1
  - Độ nhạy với spread
Chỉ chạy trên dữ liệu lịch sử — KHÔNG đụng bot.
"""
import sys
import os

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import research.trend_momentum as tm


# ───────────────────────── ADX (Wilder) trên H1 ─────────────────────────
def adx(df, period=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    up = h.diff()
    dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def load_mm():
    m1, m15, h1 = tm.load_all()
    mm = tm.build(m1, m15, h1)
    h1c = h1.copy()
    h1c["adx14"] = adx(h1c).shift(1)
    mm = pd.merge_asof(mm, h1c[["time", "adx14"]], on="time", direction="backward")
    return mm


def seg_split(mm, nsegs=6):
    idx = np.array_split(np.arange(len(mm)), nsegs)
    return idx


def run_seg(mm, seg_idx, cfg, spread):
    sub = mm.iloc[seg_idx].copy()
    buy, sell = tm.build_signals(sub, cfg["lookback"], cfg["rsi_buy"], cfg["rsi_sell"], cfg["session"])
    old = tm.SPREAD
    tm.SPREAD = spread
    try:
        s = tm.run(sub, buy, sell, cfg["sl_atr"], cfg["tp_r"], cfg["be_move"])
        return s
    finally:
        tm.SPREAD = old


def main():
    mm = load_mm()
    n = len(mm)
    segs = seg_split(mm, nsegs=6)
    print(f"Tổng nến M1 (sau warm-up): {n:,}  |  ~{n/1440:.0f} ngày giao dịch")
    for i, s in enumerate(segs):
        t0 = mm.iloc[s[0]]["time"]
        t1 = mm.iloc[s[-1]]["time"]
        adxv = mm.iloc[s]["adx14"].mean()
        print(f"  Đoạn S{i}: {t0:%d/%m} → {t1:%d/%m}  | ADX H1 tb={adxv:.1f} {'TREND' if adxv>=25 else 'RANGE'}")
    print()

    grids = [dict(lookback=lb, sl_atr=sl, tp_r=tp, rsi_buy=55, rsi_sell=45, session=True, be_move=True)
             for lb in (15, 20, 30) for sl in (1.0, 1.5, 2.0) for tp in (2.0, 3.0)]
    folds = [(1, 4), (2, 5)]  # (first_train_seg, test_seg); fold3 test=S5 is a Train of nothing... simpler below

    # Expanding walk-forward: test segs S3, S4, S5 with growing train
    train_sets = {
        "Fold1(test=S3)": (slice(0, 3), 3),
        "Fold2(test=S4)": (slice(0, 4), 4),
        "Fold3(test=S5)": (slice(0, 5), 5),
    }

    choices = []
    for cfg in grids:
        total = 0
        for (train_expr, _test) in train_sets.values():
            tr_idx = np.concatenate(segs[train_expr])
            s = run_seg(mm, tr_idx, cfg, tm.SPREAD)
            total += s["profit"]
        choices.append((total, cfg))

    top = sorted(choices, key=lambda x: x[0], reverse=True)
    print("=== TOP 8 cấu hình theo tổng lợi nhuận TRAIN (3 fold) ===")
    print(f"{'lookback':>8} {'sl_atr':>7} {'tp_r':>5} {'Tổng train':>12}")
    for (p, c) in top[:8]:
        print(f"{c['lookback']:>8} {c['sl_atr']:>7} {c['tp_r']:>5} {p:>12.2f}")
    chosen = top[0][1]
    print(f"\n→ Chọn cấu hình tốt nhất trên train: lookback={chosen['lookback']} sl_atr={chosen['sl_atr']} tp_r={chosen['tp_r']}\n")

    print("=== WALK-FORWARD: OOS cho cấu hình đã chọn ===")
    print(f"{'Fold':<14}{'test seg':>9}{'regime':>8}{'n':>5}{'WR%':>7}{'profit':>9}{'final':>9}{'EXP':>7}{'DD':>8}")
    tot_trades = 0
    tot_profit = 0.0
    oos_dd = 0.0
    for name, (train_expr, test_seg) in train_sets.items():
        tr_idx = np.concatenate(segs[train_expr])
        run_seg(mm, tr_idx, chosen, tm.SPREAD)  # warm (không dùng)
        s = run_seg(mm, segs[test_seg], chosen, tm.SPREAD)
        regime = "TREND" if mm.iloc[segs[test_seg]]["adx14"].mean() >= 25 else "RANGE"
        t0 = mm.iloc[segs[test_seg][0]]["time"]; t1 = mm.iloc[segs[test_seg][-1]]["time"]
        print(f"{name:<14}{t0:%d/%m}-{t1:%d/%m} {regime:>8}{s['trades']:>5}{s['winrate']:>7.1f}"
              f"{s['profit']:>9.2f}{s['final']:>9.2f}{s['expectancy']:>7.2f}{s['max_dd']:>8.2f}")
        tot_trades += s["trades"]
        tot_profit += s["profit"]
        oos_dd = max(oos_dd, s["max_dd"])
    print(f"\nTỔNG OOS: {tot_trades} lệnh | lợi nhuận +${tot_profit:.2f} | max DD/đoạn: ${oos_dd:.2f}")

    print("\n=== ĐỘ NHẠY SPREAD (tổng OOS của cấu hình đã chọn) ===")
    monthly = {name: (ex, seg) for name, (ex, seg) in train_sets.items()}
    for spread in (0.22, 0.30, 0.40):
        tot = 0
        for _name, (_ex, seg) in monthly.items():
            tot += run_seg(mm, segs[seg], chosen, spread)["profit"]
        print(f"  spread {spread:.2f}: tổng OOS = ${tot:+.2f}")

    print("\n=== BỘ LỌC ADX H1 (tổng OOS của cấu hình đã chọn) ===")
    print(f"{'ADX>=':>6}{'lệnh':>7}{'WR%':>7}{'profit':>10}{'EXP':>7}{'max DD':>9}")
    for th in (0, 20, 22, 25, 28):
        tot_n = tot_p = 0
        wr = np.nan
        exp = 0.0
        dd = 0.0
        for _name, (_ex, seg) in monthly.items():
            sub = mm.iloc[segs[seg]].copy()
            buy, sell = tm.build_signals(sub, chosen["lookback"], chosen["rsi_buy"],
                                         chosen["rsi_sell"], chosen["session"])
            if th > 0:
                adx_ok = sub["adx14"].to_numpy() >= th
                buy = buy & adx_ok
                sell = sell & adx_ok
            s = tm.run(sub, buy, sell, chosen["sl_atr"], chosen["tp_r"], chosen["be_move"])
            tot_n += s["trades"]
            tot_p += s["profit"]
            dd = max(dd, s["max_dd"])
            exp += s["expectancy"]
        exp = exp / 3
        print(f"{th:>6}{tot_n:>7}{wr:>7.1f}{tot_p:>10.2f}{exp:>7.2f}{dd:>9.2f}")

    print("\n=== PHƯƠNG ÁN ĐƠN GIẢN HOÁ (không BE-move) — tổng OOS ===")
    for be in (True, False):
        tot_n = tot_p = 0
        dd = 0.0
        for _name, (_ex, seg) in monthly.items():
            sub = mm.iloc[segs[seg]].copy()
            buy, sell = tm.build_signals(sub, chosen["lookback"], chosen["rsi_buy"],
                                         chosen["rsi_sell"], chosen["session"])
            c2 = dict(chosen)
            c2["be_move"] = be
            s = tm.run(sub, buy, sell, c2["sl_atr"], c2["tp_r"], c2["be_move"])
            tot_n += s["trades"]
            tot_p += s["profit"]
            dd = max(dd, s["max_dd"])
        print(f"  BE-move={str(be):<5}: {tot_n} lệnh | tổng OOS = ${tot_p:+.2f} | max DD ${dd:.2f}")

    print("\n=== LỢI NHUẬN THEO THÁNG (cấu hình chọn; lot 0.01, vốn $100) ===")
    mm2 = mm.copy()
    mm2["month"] = mm2["time"].dt.to_period("M")
    for th in (22, 0):
        print(f"  --- ADX> {th} ---")
        print(f"  {'tháng':<10}{'lệnh':>6}{'WR%':>7}{'profit':>10}{'EXP':>7}{'maxDD':>8}")
        for month, g in mm2.groupby("month"):
            buy, sell = tm.build_signals(g, chosen["lookback"], chosen["rsi_buy"],
                                         chosen["rsi_sell"], chosen["session"])
            if th > 0:
                adx_ok = g["adx14"].to_numpy() >= th
                buy = buy & adx_ok
                sell = sell & adx_ok
            s = tm.run(g, buy, sell, chosen["sl_atr"], chosen["tp_r"], chosen["be_move"])
            print(f"  {str(month):<10}{s['trades']:>6}{s['winrate']:>7.1f}{s['profit']:>10.2f}"
                  f"{s['expectancy']:>7.2f}{s['max_dd']:>8.2f}")


if __name__ == "__main__":
    main()