"""
backtest/visualizer.py
Vẽ biểu đồ lợi nhuận (Equity Curve) của Back-test.
"""
import matplotlib.pyplot as plt
import pandas as pd

def plot_results(trades, initial_balance, symbol, timeframe):
    """
    Vẽ biểu đồ kết quả từ danh sách các lệnh trades.
    """
    if not trades:
        print("Không có dữ liệu lệnh để vẽ biểu đồ.")
        return

    df_trades = pd.DataFrame(trades)
    
    # Tạo danh sách số dư theo thời gian
    # Ta chèn giá trị khởi đầu vào để đường nét bắt đầu từ mốc 0
    balances = [initial_balance] + df_trades["balance"].tolist()
    trade_indices = list(range(len(balances)))

    plt.figure(figsize=(12, 6))
    
    # Vẽ đường Equity (Số dư)
    plt.plot(trade_indices, balances, marker='o', linestyle='-', color='#1f77b4', label='Equity Curve')
    
    # Đánh dấu các lệnh thắng/thua bằng màu sắc
    for i, trade in enumerate(trades):
        color = 'green' if trade["result"] == "PROFIT" else 'red'
        plt.scatter(i + 1, trade["balance"], color=color, zorder=5)

    plt.title(f"Backtest Results: {symbol} ({timeframe}) - {len(trades)} trades")
    plt.xlabel("Trade Number")
    plt.ylabel("Balance (USD)")
    plt.grid(True, linestyle='--', alpha=0.7)
    plt.legend()
    
    # Lưu biểu đồ ra file
    chart_path = f"backtest/equity_{symbol}_{timeframe}.png"
    plt.savefig(chart_path)
    print(f"📈 Đã lưu biểu đồ lợi nhuận vào: {chart_path}")
    
    # Hiển thị cửa sổ (Nếu chạy local có GUI)
    plt.show()

def plot_pnl_distribution(trades):
    """
    Vẽ phân bổ Lợi nhuận/Thua lỗ (Histogram).
    """
    if not trades: return
    df_trades = pd.DataFrame(trades)
    
    plt.figure(figsize=(8, 5))
    df_trades['pnl'].hist(bins=20, color='skyblue', edgecolor='black')
    plt.axvline(0, color='red', linestyle='dashed', linewidth=2)
    plt.title("Phân bổ Lợi nhuận/Thua lỗ của các lệnh")
    plt.xlabel("Profit/Loss Amount")
    plt.ylabel("Frequency")
    plt.show()
