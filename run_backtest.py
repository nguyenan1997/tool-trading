"""
run_backtest.py
Chạy thử nghiệm chiến lược trên dữ liệu lịch sử.
"""
from backtest.engine import Backtester
from backtest.data_loader import get_historical_data
# from backtest.visualizer import plot_results # Đã loại bỏ
from strategies.triple_ema import TripleEmaStrategy
import config
import logging
import pandas as pd

# Thiết lập log cơ bản cho backtest
logging.basicConfig(level=logging.INFO)

def main():
    print("="*40)
    print("      TRADING BOT BACK-TEST SYSTEM      ")
    print("="*40)

    # 1. Cấu hình
    symbol = config.SYMBOL
    timeframe = config.TIMEFRAME
    count = 32000  # Khoảng 1 tháng giao dịch
    initial_balance = 100
    
    # 2. Lấy dữ liệu (Sử dụng Cache đã tải lúc nãy sẽ rất nhanh)
    df = get_historical_data(symbol, timeframe, count=count, use_cache=True)
    
    if df is None or df.empty:
        print(f"❌ KHÔNG THỂ lấy dữ liệu cho {symbol} ({timeframe}).")
        return

    # In thông tin thời gian dữ liệu
    start_date = df.index[0]
    end_date = df.index[-1]
    print(f"📅 Dữ liệu từ: {start_date} đến {end_date}")
    
    # 3. Khởi tạo Chiến lược
    strategy = TripleEmaStrategy()
    
    # 4. Chạy Back-test
    tester = Backtester(
        strategy=strategy, 
        initial_balance=initial_balance, 
        lot_size=0.01, 
        digits=2 # Vàng XAUUSD thường có 2-3 chữ số thập phân
    )
    
    trades = tester.run(df)

    # 5. Hiển thị kết quả tóm tắt
    if trades:
        print("\n--- CHI TIẾT 5 LỆNH CUỐI ---")
        trades_df = pd.DataFrame(trades).tail(5)
        # Chỉ hiển thị các cột quan trọng
        print(trades_df[["type", "entry", "exit", "result", "pnl", "balance"]])
    else:
        print("\n⚠️ Không có lệnh nào được thực hiện trong khoảng thời gian này.")

if __name__ == "__main__":
    main()
