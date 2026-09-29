"""
research/ict.py
Nghiên cứu chiến lược ICT (Killzone → Sweep → Displacement/FVG) trên XAUUSD M5.
Chỉ backtest/phân tích trên dữ liệu lịch sử — KHÔNG đụng bot live.

Dữ liệu: gộp các file M1 cache → M5, tách đoạn liên tục (gap > 4h).
Đánh giá: WR, PF, Expectancy, Max DD, train/OOS + walk-forward.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research.common import (  # noqa: E402
    load_m5,
    run_segmented,
    metrics,
    show,
    header,
    split_trades,
    fold_report,
    by_month,
    extremes,
)
from strategies.ict import ICTKillzoneFVGStrategy  # noqa: E402


def evaluate(name, strategy, m5, split_ts, print_row=True):
    trades = run_segmented(strategy, m5)
    tr, oos = split_trades(trades, split_ts)
    m_tr, m_oos, m_all = metrics(tr), metrics(oos), metrics(trades)
    if print_row:
        show(name, m_tr)
    robust = (min(m_tr["pf"], m_oos["pf"])
              if np.isfinite(m_tr["pf"]) and np.isfinite(m_oos["pf"]) else 0.0)
    return dict(name=name, strat=strategy, trades=trades,
                m_tr=m_tr, m_oos=m_oos, m_all=m_all, robust=robust)


def main():
    m5 = load_m5()
    print(f"Dữ liệu M5: {len(m5):,} nến | {m5.index[0]} → {m5.index[-1]}")
    split = int(len(m5) * 0.7)
    split_ts = m5.index[split]
    print(f"Mốc train/OOS: {split_ts}\n")

    # Vùng killzone/risk đã kiểm chứng
    KZ = [(7, 11), (12, 16)]
    common = dict(killzones=KZ, min_r_atr=0.0, max_r_atr=6.0)

    variants = [
        ("baseline", ICTKillzoneFVGStrategy(bias_mode="prevday", **common)),
        ("size>=0.3", ICTKillzoneFVGStrategy(bias_mode="prevday", min_fvg_atr=0.3, **common)),
        ("size>=0.5", ICTKillzoneFVGStrategy(bias_mode="prevday", min_fvg_atr=0.5, **common)),
        ("size>=0.7", ICTKillzoneFVGStrategy(bias_mode="prevday", min_fvg_atr=0.7, **common)),
        ("size>=1.0", ICTKillzoneFVGStrategy(bias_mode="prevday", min_fvg_atr=1.0, **common)),
        ("size0.5+vol1.2", ICTKillzoneFVGStrategy(bias_mode="prevday", min_fvg_atr=0.5, vol_min=1.2, **common)),
        ("size0.5+score", ICTKillzoneFVGStrategy(bias_mode="prevday", min_fvg_atr=0.5, fvg_select="score", **common)),
        ("size0.5+vol1.2+score", ICTKillzoneFVGStrategy(bias_mode="prevday", min_fvg_atr=0.5, vol_min=1.2, fvg_select="score", **common)),
    ]

    print("=== TRAIN (70% đầu) ===")
    header()
    scored = []
    for name, strat in variants:
        scored.append(evaluate(name, strat, m5, split_ts))

    print("\n=== OOS (30% cuối) — tất cả biến thể ===")
    header()
    for r in sorted(scored, key=lambda x: x["m_oos"]["pf"], reverse=True):
        show(f"{r['name']} [OOS]", r["m_oos"])

    print("\n=== BỀN VỮNG: sắp theo min(PF_train, PF_oos) ===")
    header()
    eligible = [r for r in scored if r["m_tr"]["n"] >= 10 and r["m_oos"]["n"] >= 8]
    eligible.sort(key=lambda r: r["robust"], reverse=True)
    for r in eligible:
        show(f"{r['name']} [tr]", r["m_tr"])
        show(f"{r['name']} [oos]", r["m_oos"])

    if not eligible:
        print("Không có cấu hình đủ số lệnh.")
        return

    best = eligible[0]
    print(f"\n=== Toàn bộ dữ liệu — best bền vững: {best['name']} ===")
    header()
    show("ALL", best["m_all"])
    fold_report(best["strat"], m5, k=4)
    by_month(best["trades"])
    extremes(best["trades"])


if __name__ == "__main__":
    main()
