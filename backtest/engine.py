"""
backtest/engine.py
Lõi xử lý Back-test: giả lập giao dịch trên dữ liệu lịch sử.
"""
import pandas as pd
import logging
from datetime import datetime
from strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

class Backtester:
    def __init__(self, strategy: BaseStrategy, initial_balance=1000, lot_size=0.1, digits=5, spread=0.30):
        self.strategy = strategy
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.lot_size = lot_size
        self.digits = digits
        self.spread = spread  # Spread thật từ broker (đơn vị: price, ví dụ 0.30 cho XAUUSD)
        self.trades = []
        self.current_position = None  # None | {"type": "BUY/SELL", "entry": float, "sl": float, "tp": float, "time": datetime}

    def run(self, df: pd.DataFrame):
        """
        Chạy Back-test trên một DataFrame nến.
        """
        print(f"--- BẮT ĐẦU BACK-TEST: {self.strategy.name} ---")
        df = self.strategy.calculate_indicators(df)
        
        # Bắt đầu từ khi đủ dữ liệu cho các chỉ báo (ví dụ EMA 200)
        start_idx = 100 
        if len(df) <= start_idx:
            print("Dữ liệu quá ngắn để Back-test")
            return []

        for i in range(start_idx, len(df) - 1):
            current_candle = df.iloc[i]
            next_candle = df.iloc[i+1]
            
            # 1. Kiểm tra xem có lệnh nào đang mở bị chạm SL/TP không
            if self.current_position:
                self._check_exit(current_candle)

            # 2. Nếu không có lệnh, kiểm tra tín hiệu mới
            if not self.current_position:
                # Tính tín hiệu dựa trên nến đã đóng (nến i)
                sub_df = df.iloc[:i+1]
                signal = self.strategy.check_signal(sub_df)
                
                if signal:
                    # Vào lệnh tại giá OPEN của nến TIẾP THEO (nến i+1)
                    # Mô phỏng spread thật của broker:
                    #   BUY  → khớp tại ASK = open + spread
                    #   SELL → khớp tại BID = open (dữ liệu nến MT5 luôn là Bid)
                    open_price = next_candle["open"]
                    entry_price = round(open_price + self.spread, self.digits) if signal == "BUY" else open_price
                    sl, tp = self.strategy.get_sl_tp(sub_df, entry_price, self.digits, signal)
                    
                    if sl and tp:
                        self.current_position = {
                            "type": signal,
                            "entry": entry_price,
                            "sl": sl,
                            "tp": tp,
                            "entry_time": next_candle.name if hasattr(next_candle, 'name') else (i+1)
                        }

        self._print_summary()
        return self.trades

    def _check_exit(self, candle):
        pos = self.current_position
        # Dữ liệu nến là giá BID. Khi SELL chạm SL (giá tăng), thực tế sẽ chạm tại Ask = high + spread
        low  = candle["low"]
        high = candle["high"] + self.spread
        exit_time = candle.name if hasattr(candle, 'name') else "N/A"

        result = None
        exit_price = 0

        if pos["type"] == "BUY":
            if low <= pos["sl"]: # Chạm SL
                result = "LOSS"
                exit_price = pos["sl"]
            elif high >= pos["tp"]: # Chạm TP
                result = "PROFIT"
                exit_price = pos["tp"]
        
        elif pos["type"] == "SELL":
            if high >= pos["sl"]: # Chạm SL
                result = "LOSS"
                exit_price = pos["sl"]
            elif low <= pos["tp"]: # Chạm TP
                result = "PROFIT"
                exit_price = pos["tp"]

        if result:
            # Tính toán P/L thực tế cho XAUUSD (1 lot = 100 ounces)
            pnl_points = (exit_price - pos["entry"]) if pos["type"] == "BUY" else (pos["entry"] - exit_price)
            # Profit = chênh lệch giá * khối lựợng * 100 (contract size)
            profit_value = pnl_points * self.lot_size * 100
            
            self.balance += profit_value
            self.trades.append({
                "type": pos["type"],
                "entry": pos["entry"],
                "exit": exit_price,
                "entry_time": pos["entry_time"],
                "exit_time": exit_time,
                "result": result,
                "pnl": profit_value,
                "balance": self.balance
            })
            self.current_position = None

    def _print_summary(self):
        total_trades = len(self.trades)
        wins = len([t for t in self.trades if t["result"] == "PROFIT"])
        losses = total_trades - wins
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        
        print(f"--- KẾT QUẢ BACK-TEST ---")
        print(f"Tổng số lệnh: {total_trades}")
        print(f"Thắng: {wins} | Thua: {losses}")
        print(f"Tỉ lệ thắng: {win_rate:.2f}%")
        print(f"Số dư cuối: {self.balance:.2f}")
        print(f"------------------------")
