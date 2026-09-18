"""
run_backtest.py
Chạy thử nghiệm chiến lược trên dữ liệu lịch sử.

Cách dùng:
    python run_backtest.py                 # mặc định trend_momentum
    python run_backtest.py smc             # SMC (M5)
    python run_backtest.py asian_sweep     # Asian Sweep (M1)
    python run_backtest.py trend_momentum 20000
"""
import sys
import logging

import pandas as pd

from backtest.engine import Backtester
from backtest.data_loader import get_historical_data
from strategies.trend_momentum import TrendMomentumStrategy
from strategies.asian_sweep import AsianSweepStrategy
from strategies.smc import SMCSweepChochStrategy
import config

logging.basicConfig(level=logging.INFO)

# key -> (class, timeframe, số nến mặc định)
REGISTRY = {
    "trend_momentum": (TrendMomentumStrategy, "M1", 20000),
    "asian_sweep": (AsianSweepStrategy, "M1", 20000),
    "smc": (SMCSweepChochStrategy, "M5", 20000),
}


def main():
    print("=" * 40)
    print("      TRADING BOT BACK-TEST SYSTEM      ")
    print("=" * 40)

    key = (sys.argv[1] if len(sys.argv) > 1 else "trend_momentum").strip().lower()
    if key not in REGISTRY:
        print(f"❌ Chiến lược không hợp lệ: {key}. Chọn: {', '.join(REGISTRY)}")
        return

    cls, timeframe, default_count = REGISTRY[key]
    count = int(sys.argv[2]) if len(sys.argv) > 2 else default_count
    symbol = config.SYMBOL
    initial_balance = 200.0

    df = get_historical_data(symbol, timeframe, count=count, use_cache=True)
    if df is None or df.empty:
        print(f"❌ KHÔNG THỂ lấy dữ liệu cho {symbol} ({timeframe}).")
        return

    print(f"📅 Dữ liệu từ: {df.index[0]} đến {df.index[-1]}  ({len(df):,} nến {timeframe})")

    strategy = cls()
    tester = Backtester(
        strategy=strategy,
        initial_balance=initial_balance,
        lot_size=getattr(strategy, "lot", config.FIXED_LOT),
        digits=2,
    )
    trades = tester.run(df)

    if trades:
        print("\n--- CHI TIẾT 5 LỆNH CUỐI ---")
        trades_df = pd.DataFrame(trades).tail(5)
        print(trades_df[["type", "entry", "exit", "result", "pnl", "balance"]])
    else:
        print("\n⚠️ Không có lệnh nào được thực hiện trong khoảng thời gian này.")


if __name__ == "__main__":
    main()
