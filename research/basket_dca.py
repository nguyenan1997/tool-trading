"""
research/basket_dca.py
Backtest cho EA "Dynamic Basket DCA" (straddle stop + DCA Fibonacci + TP động, KHÔNG SL).

Chạy trên dữ liệu nến (mặc định M1 có sẵn trong backtest/data/).
Mô phỏng theo từng nến OHLC + spread thật (cột 'spread', points).

Mục tiêu: đo drawdown, số cấp nhồi tối đa, và tìm giai đoạn trend làm CHÁY tài khoản.

Cách dùng:
    python research/basket_dca.py
    python research/basket_dca.py backtest/data/XAUUSD_M1_150000.csv
    python research/basket_dca.py --balance 10000 --max-levels 0
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

POINT = 0.01
CONTRACT = 100.0
FIB = [1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610, 987, 1597, 2584, 4181]


def fib_lot(level: int, lot0: float) -> float:
    f = FIB[level] if level < len(FIB) else FIB[-1] * (2 ** (level - len(FIB) + 1))
    return round(f * lot0, 2)


def run_backtest(df: pd.DataFrame, *, lot0=0.01, init_dist=0.50, step=1.00,
                 tp_initial=0.15, tp_basket=0.05, max_levels=0, max_total_lot=0.0,
                 balance=10000.0, leverage=1000.0, stopout_pct=50.0, default_spread=0.22,
                 requote_bars=1, volume_max=100.0, verbose=False):
    """Trả về dict thống kê + sự kiện cháy."""
    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy();  c = df["close"].to_numpy()
    sp = (df["spread"].to_numpy() * POINT) if "spread" in df.columns else np.full(len(df), default_spread)
    idx = df.index

    equity = balance
    peak = balance
    max_dd = 0.0
    min_margin_level = 1e18
    blowup = None
    worst_float = 0.0

    basket = None          # {"side":0/1, "entries":[(price,lot)...], "last":price, "level":int}
    pending = None         # {"buy":price, "sell":price}
    trades = []            # pnl realized per basket
    trade_rows = []        # chi tiết từng rổ (cho UI)
    level_hist = []
    lot_hist = []
    events = []

    n = len(df)

    def floating(bk, price, spread):
        """Lãi/lỗ nổi của rổ theo giá bid hiện tại."""
        tot = 0.0
        for e, v in bk["entries"]:
            if bk["side"] == 0:
                tot += (price - e) * v * CONTRACT
            else:
                tot += (e - (price + spread)) * v * CONTRACT
        return tot

    def basket_lot(bk):
        return sum(v for _, v in bk["entries"])

    def margin_needed(lot, price):
        return lot * price * CONTRACT / leverage

    for i in range(n):
        hi, lo, op, cl, s = h[i], l[i], o[i], c[i], sp[i]

        # ---------- 1) xử lý straddle đang chờ ----------
        if pending is not None and basket is None:
            hit_buy = hi >= pending["buy"]
            hit_sell = lo <= pending["sell"]
            side = None
            trig_price = None
            if hit_buy and hit_sell:
                # cả 2 chạm trong 1 nến -> chọn bên gần giá mở (chạm trước)
                if abs(pending["buy"] - op) <= abs(op - pending["sell"]):
                    side = 0; trig_price = pending["buy"]
                else:
                    side = 1; trig_price = pending["sell"]
            elif hit_buy:
                side = 0; trig_price = pending["buy"]
            elif hit_sell:
                side = 1; trig_price = pending["sell"]
            if side is not None:
                lot = lot0
                basket = {"side": side, "entries": [(trig_price, lot)], "last": trig_price,
                          "level": 1, "start": idx[i]}
                pending = None
            else:
                # chưa khớp -> re-quote (mô phỏng EA đặt lại gần giá)
                if (i % max(1, requote_bars)) == 0:
                    pending = {"buy": cl + init_dist, "sell": cl - init_dist}

        # ---------- 2) xử lý rổ đang mở ----------
        added_this_bar = False
        if basket is not None:
            side = basket["side"]
            while max_levels <= 0 or basket["level"] < max_levels:
                nxt_lot = fib_lot(basket["level"], lot0)
                if nxt_lot > volume_max + 1e-9:
                    break
                if max_total_lot > 0 and basket_lot(basket) + nxt_lot > max_total_lot:
                    break
                # đủ margin mới nhồi được
                eq_now = equity + floating(basket, cl, sp[i])
                if eq_now - margin_needed(basket_lot(basket), cl) < margin_needed(nxt_lot, cl):
                    break
                if side == 0:
                    if lo > basket["last"] - step:
                        break
                    newp = basket["last"] - step
                else:
                    if hi < basket["last"] + step:
                        break
                    newp = basket["last"] + step
                basket["entries"].append((newp, nxt_lot))
                basket["last"] = newp
                basket["level"] += 1
                added_this_bar = True

            # TP động — KHÔNG cho hồi & chốt trong cùng nến vừa nhồi (tránh nhìn trước)
            totv = basket_lot(basket)
            wavg = sum(e * v for e, v in basket["entries"]) / totv
            tp_dist = tp_initial if basket["level"] == 1 else tp_basket
            target = wavg + tp_dist if side == 0 else wavg - tp_dist

            hit_tp = (h[i] >= target) if side == 0 else (l[i] <= target)
            if hit_tp and not added_this_bar:
                pnl = 0.0
                for e, v in basket["entries"]:
                    if side == 0:
                        pnl += (target - e) * v * CONTRACT
                    else:
                        pnl += (e - target) * v * CONTRACT
                equity += pnl
                trades.append(pnl)
                level_hist.append(basket["level"])
                lot_hist.append(totv)
                trade_rows.append({
                    "type": "BUY" if side == 0 else "SELL",
                    "entry": round(float(wavg), 2), "exit": round(float(target), 2),
                    "entry_time": str(basket["start"]), "exit_time": str(idx[i]),
                    "result": "PROFIT" if pnl > 1e-9 else ("LOSS" if pnl < -1e-9 else "BREAKEVEN"),
                    "partial": False, "pnl": round(float(pnl), 2), "balance": round(float(equity), 2),
                })
                if basket["level"] >= 5 and verbose:
                    events.append((idx[i], side, basket["level"], round(totv, 2), round(pnl, 2)))
                basket = None
                pending = None  # mở chu kỳ mới ở nến sau

        # ---------- 3) nếu flat & không pending -> đặt straddle mới ----------
        if basket is None and pending is None:
            pending = {"buy": cl + init_dist, "sell": cl - init_dist}

        # ---------- 4) equity / drawdown / margin / stop-out (theo điểm XẤU NHẤT trong nến) ----------
        if basket is not None:
            adverse = l[i] if basket["side"] == 0 else h[i]
            fl = floating(basket, adverse, s)
            worst_float = min(worst_float, fl)
            eq = equity + fl
            peak = max(peak, eq)
            max_dd = max(max_dd, peak - eq)
            m = margin_needed(basket_lot(basket), adverse)
            if m > 0 and eq > 0:
                ml = eq / m * 100.0
                min_margin_level = min(min_margin_level, ml)
                if ml < stopout_pct and blowup is None:
                    # STOP-OUT: sàn cắt toàn bộ tại giá hiện tại -> hiện thực hóa lỗ nổi
                    equity += fl
                    trades.append(fl)
                    level_hist.append(basket["level"])
                    lot_hist.append(basket_lot(basket))
                    _tv = basket_lot(basket)
                    _wa = sum(e * v for e, v in basket["entries"]) / _tv
                    trade_rows.append({
                        "type": "BUY" if basket["side"] == 0 else "SELL",
                        "entry": round(float(_wa), 2), "exit": round(float(adverse), 2),
                        "entry_time": str(basket["start"]), "exit_time": str(idx[i]),
                        "result": "LOSS", "partial": False,
                        "pnl": round(float(fl), 2), "balance": round(float(equity), 2),
                    })
                    blowup = (str(idx[i]), round(equity, 2), basket["level"],
                              round(basket_lot(basket), 2), round(ml, 1))
                    basket = None
                    pending = None
                    break

    # thống kê
    t = np.array(trades) if trades else np.array([0.0])
    wins = t[t > 0]
    net = float(t.sum())
    return {
        "bars": n,
        "period": f"{idx[0]} -> {idx[-1]}" if n else "-",
        "baskets": len(trades),
        "win_rate": round(len(wins) / max(1, len(trades)) * 100, 2),
        "net": round(net, 2),
        "final_equity": round(balance + net, 2),
        "max_dd": round(max_dd, 2),
        "max_dd_pct": round(max_dd / (balance + max(0, net)) * 100, 2) if balance else 0,
        "max_levels": max(level_hist) if level_hist else 0,
        "max_lot": round(max(lot_hist), 2) if lot_hist else 0,
        "min_margin_level": round(min_margin_level, 1) if min_margin_level < 1e17 else None,
        "worst_float": round(worst_float, 2),
        "blowup": blowup,
        "events": events[-10:],
        "trades": trade_rows,
    }


def load(path):
    df = pd.read_csv(path)
    tcol = "time" if "time" in df.columns else df.columns[0]
    df[tcol] = pd.to_datetime(df[tcol])
    df = df.set_index(tcol).sort_index()
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="?", default="backtest/data/XAUUSD_M1_100000.csv")
    ap.add_argument("--balance", type=float, default=10000.0)
    ap.add_argument("--lot", type=float, default=0.01)
    ap.add_argument("--init-dist", type=float, default=0.50)
    ap.add_argument("--step", type=float, default=1.00)
    ap.add_argument("--tp-initial", type=float, default=0.15)
    ap.add_argument("--tp-basket", type=float, default=0.05)
    ap.add_argument("--max-levels", type=int, default=0)
    ap.add_argument("--max-total-lot", type=float, default=0.0)
    ap.add_argument("--leverage", type=float, default=1000.0)
    ap.add_argument("--stopout", type=float, default=50.0)
    a = ap.parse_args()

    df = load(a.csv)
    print("=" * 70)
    print(f"BACKTEST Dynamic Basket DCA  |  {a.csv}")
    print(f"bars={len(df):,}  |  {df.index[0]} -> {df.index[-1]}")
    print(f"params: lot={a.lot} init={a.init_dist} step={a.step} tp_init={a.tp_initial} "
          f"tp_basket={a.tp_basket} max_levels={a.max_levels} balance={a.balance}")
    print("=" * 70)
    r = run_backtest(df, lot0=a.lot, init_dist=a.init_dist, step=a.step,
                     tp_initial=a.tp_initial, tp_basket=a.tp_basket,
                     max_levels=a.max_levels, max_total_lot=a.max_total_lot,
                     balance=a.balance, leverage=a.leverage, stopout_pct=a.stopout,
                     verbose=True)
    for k, v in r.items():
        if k == "events":
            continue
        print(f"  {k:16}: {v}")
    if r["events"]:
        print("  rổ sâu (>=5 cấp) cuối cùng:")
        for e in r["events"]:
            print(f"     {e}")


if __name__ == "__main__":
    main()
