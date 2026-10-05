"""
run_backtest.py
Chạy thử nghiệm chiến lược trên dữ liệu lịch sử (mô phỏng SÁT THỰC TẾ).

Cách dùng:
    python run_backtest.py                 # mặc định ict
    python run_backtest.py ict             # ICT (M5)
    python run_backtest.py ict 20000       # số nến
    python run_backtest.py ict 20000 noexec # tắt "đường đi nến nhỏ" (so sánh)

Mô phỏng thực tế gồm: dùng nến nhỏ hơn (M1) để biết thứ tự chạm SL/TP trong nến,
+ spread thật, + commission/slippage (cấu hình trong config.py, mục BACKTEST_*).
"""
import sys
import json
import logging

import pandas as pd

from backtest.engine import Backtester
from backtest.data_loader import get_with_warmup, get_historical_data, _BAR_MINUTES
from strategies.ict import ICTKillzoneFVGStrategy
from research.common import metrics
import config

logging.basicConfig(level=logging.INFO)

REGISTRY = {
    "ict": (ICTKillzoneFVGStrategy, "M5", 20000),
}


def main():
    print("=" * 40)
    print("      TRADING BOT BACK-TEST SYSTEM      ")
    print("=" * 40)

    key = (sys.argv[1] if len(sys.argv) > 1 else "ict").strip().lower()
    if key not in REGISTRY:
        print(f"❌ Chiến lược không hợp lệ: {key}. Chọn: {', '.join(REGISTRY)}")
        return

    cls, timeframe, default_count = REGISTRY[key]
    count = int(sys.argv[2]) if len(sys.argv) > 2 else default_count
    noexec = len(sys.argv) > 3 and sys.argv[3].strip().lower() in ("noexec", "none", "off")
    symbol = config.SYMBOL
    initial_balance = 1000.0

    strategy = cls()
    warmup = int(getattr(strategy, "warmup_bars", 0) or 0)
    df = get_with_warmup(symbol, timeframe, count=count, start_date=None, warmup_bars=warmup)
    if df is None or df.empty:
        print(f"❌ KHÔNG THỂ lấy dữ liệu cho {symbol} ({timeframe}).")
        return

    # Nến nhỏ hơn (đường đi) để khớp SL/TP đúng thứ tự
    exec_df = None
    exec_tf = "" if noexec else getattr(config, "BACKTEST_EXEC_TF", "")
    if exec_tf:
        ratio = max(1, _BAR_MINUTES.get(timeframe, 1) // _BAR_MINUTES.get(exec_tf, 1))
        m = get_historical_data(symbol, exec_tf, count=count * ratio + 2000)
        if m is not None and not m.empty:
            exec_df = m[m.index >= df.index[0]]

    print(f"📅 Dữ liệu từ: {df.index[0]} đến {df.index[-1]}  ({len(df):,} nến {timeframe})")
    if exec_df is not None:
        print(f"   Nến đường đi: {exec_tf} ({len(exec_df):,} nến)")
    else:
        print("   ⚠️ Không có nến nhỏ -> mô phỏng kém thực tế (chỉ OHLC nến tín hiệu)")

    tester = Backtester(
        strategy=strategy,
        initial_balance=initial_balance,
        lot_size=getattr(strategy, "lot", config.FIXED_LOT),
        digits=2,
        exec_df=exec_df,
        commission_per_lot=getattr(config, "BACKTEST_COMMISSION_PER_LOT", 0.0),
        slippage_points=getattr(config, "BACKTEST_SLIPPAGE_POINTS", 0),
        spread_mult=getattr(config, "BACKTEST_SPREAD_MULT", 1.0),
        spread_min=getattr(config, "BACKTEST_SPREAD_MIN", 0.0),
        realistic_fills=getattr(config, "BACKTEST_REALISTIC_FILLS", True),
    )
    trades = tester.run(df)

    m = metrics(trades)
    print("\n--- METRICS (realistic) ---")
    print(json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in m.items()}, ensure_ascii=False))

    if trades:
        print("\n--- CHI TIẾT 5 LỆNH CUỐI ---")
        trades_df = pd.DataFrame(trades).tail(5)
        print(trades_df[["type", "entry", "exit", "result", "pnl", "balance"]])
    else:
        print("\n⚠️ Không có lệnh nào được thực hiện trong khoảng thời gian này.")


if __name__ == "__main__":
    main()
