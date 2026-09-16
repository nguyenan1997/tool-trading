"""
backtest/engine.py
Lõi xử lý Back-test: giả lập giao dịch trên dữ liệu lịch sử.
"""
import math
import pandas as pd
import logging
from datetime import datetime
from strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

class Backtester:
    def __init__(self, strategy: BaseStrategy, initial_balance=1000, lot_size=0.1, digits=5, spread=0.30,
                 volume_min=0.01, volume_step=0.01):
        self.strategy = strategy
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.lot_size = lot_size
        self.digits = digits
        self.spread = spread  # Spread thật từ broker (đơn vị: price, ví dụ 0.30 cho XAUUSD)
        self.volume_min = volume_min
        self.volume_step = volume_step
        self.trades = []
        self.current_position = None  # None | {"type": "BUY/SELL", "entry": float, "sl": float, "tp": float, "time": datetime}

    def _effective_partial_frac(self) -> float:
        """Tỷ lệ khối lượng chốt sớm thực tế (làm tròn theo volume_step).
        Trả về 0 nếu không thể chia (lot quá nhỏ) — khớp với mt5_handler.split_volume()."""
        frac = getattr(self.strategy, "partial_frac", 0) or 0
        if frac <= 0 or frac >= 1:
            return 0.0
        step = self.volume_step or 0.01
        vol = round(math.floor((self.lot_size * frac) / step + 1e-9) * step, 2)
        remaining = round(self.lot_size - vol, 2)
        if vol < self.volume_min - 1e-9 or remaining < self.volume_min - 1e-9:
            return 0.0
        return vol / self.lot_size

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

            just_closed_this_iter = False  # Lệnh có vừa đóng ở iteration này không?

            # 1. Kiểm tra SL/TP trên NEXT_CANDLE
            if self.current_position:
                self._check_exit(next_candle)
                if not self.current_position:
                    just_closed_this_iter = True  # Lệnh vừa đóng ngay trong iteration này

            # 2. Kiểm tra tín hiệu mới - NHƯNG PHẢI BỎ QUA nếu lệnh vừa đóng iteration này
            # ─────────────────────────────────────────────────────────────────────────────
            # Real bot khi lệnh đóng giữa nến K (do TP/SL tick):
            #   → Bot chờ đến cuối nến K mới check signal (sig_candle = K)
            #   → Vào lệnh mới tại open nến K+1
            #
            # Backtest không dùng just_closed_this_iter:
            #   → Sẽ check signal NGAY trong cùng iteration với candle K = next_candle
            #   → sig_candle = K-1 (SAI), entry tại K open (SAI = quá khứ!)
            #
            # Với just_closed_this_iter = True:
            #   → Bỏ qua signal check ở iteration này
            #   → Iteration tiếp theo: current=K, next=K+1
            #     sub_df[-2] = K ✅  entry = K+1 open ✅ (khớp real bot)
            # ─────────────────────────────────────────────────────────────────────────────
            if not self.current_position and not just_closed_this_iter:
                sub_df = df.iloc[:i+2]
                signal = self.strategy.check_signal(sub_df)

                if signal:
                    # Vào lệnh tại giá OPEN của nến TIẾP THEO (nến i+1)
                    # Mô phỏng spread thật của broker:
                    #   BUY  → khớp tại ASK = open + spread
                    #   SELL → khớp tại BID = open (dữ liệu nến MT5 luôn là Bid)
                    # Round cả 2 loại để tránh float64 precision artifact (4388.3900000001)
                    open_price = next_candle["open"]
                    if signal == "BUY":
                        entry_price = round(open_price + self.spread, self.digits)
                    else:
                        entry_price = round(open_price, self.digits)

                    sl, tp = self.strategy.get_sl_tp(sub_df, entry_price, self.digits, signal)
                    
                    if sl and tp:
                        self.current_position = {
                            "type": signal,
                            "entry": entry_price,
                            "sl": sl,
                            "tp": tp,
                            "R": abs(entry_price - sl),
                            "partial_frac": self._effective_partial_frac(),
                            "partial_at_r": getattr(self.strategy, "partial_at_r", 0) or 0,
                            "partial_done": False,
                            "pnl_partial": 0.0,
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

        # ── Chốt một phần (partial TP) + BE-move ──
        # Thứ tự giống bot live: tính biên độ thuận lợi từ nến này → chốt phần → dời BE.
        be_r = getattr(self.strategy, "be_move_at_r", 0)
        part_r = pos.get("partial_at_r", 0)
        part_f = pos.get("partial_frac", 0)
        R = pos.get("R", 0)

        if R > 0:
            if pos["type"] == "BUY":
                hw = (candle["high"] - pos["entry"]) / R
                if part_f > 0 and part_r > 0 and not pos["partial_done"] and hw >= part_r:
                    pos["pnl_partial"] = part_f * part_r * R * self.lot_size * 100
                    pos["partial_done"] = True
                    pos["sl"] = round(max(pos["sl"], pos["entry"]), self.digits)
                if be_r and hw >= be_r:
                    pos["sl"] = round(max(pos["sl"], pos["entry"]), self.digits)
            else:
                hw = (pos["entry"] - candle["low"]) / R
                if part_f > 0 and part_r > 0 and not pos["partial_done"] and hw >= part_r:
                    pos["pnl_partial"] = part_f * part_r * R * self.lot_size * 100
                    pos["partial_done"] = True
                    pos["sl"] = round(min(pos["sl"], pos["entry"]), self.digits)
                if be_r and hw >= be_r:
                    pos["sl"] = round(min(pos["sl"], pos["entry"]), self.digits)

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
            # Chỉ còn phần khối lượng chưa chốt + phần đã chốt sớm (nếu có)
            rem = 1.0 - (pos["partial_frac"] if pos["partial_done"] else 0.0)
            profit_value = pnl_points * self.lot_size * rem * 100 + pos["pnl_partial"]

            # Phân loại theo lãi/lỗ thực tế (lệnh chốt một phần rồi thoát hòa vốn vẫn là PROFIT)
            if profit_value > 1e-9:
                outcome = "PROFIT"
            elif profit_value < -1e-9:
                outcome = "LOSS"
            else:
                outcome = "BREAKEVEN"

            self.balance += profit_value
            self.trades.append({
                "type": pos["type"],
                "entry": pos["entry"],
                "exit": exit_price,
                "entry_time": pos["entry_time"],
                "exit_time": exit_time,
                "result": outcome,
                "partial": pos["partial_done"],
                "pnl": profit_value,
                "balance": self.balance
            })
            self.current_position = None

    def _print_summary(self):
        total_trades = len(self.trades)
        wins = len([t for t in self.trades if t["result"] == "PROFIT"])
        losses = len([t for t in self.trades if t["result"] == "LOSS"])
        be = total_trades - wins - losses
        partials = len([t for t in self.trades if t.get("partial")])
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

        print(f"--- KẾT QUẢ BACK-TEST ---")
        print(f"Tổng số lệnh: {total_trades}")
        print(f"Thắng: {wins} | Thua: {losses} | Hòa vốn: {be}")
        print(f"Lệnh chốt một phần: {partials}")
        print(f"Tỉ lệ thắng: {win_rate:.2f}%")
        print(f"Số dư cuối: {self.balance:.2f}")
        print(f"------------------------")
