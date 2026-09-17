"""
backtest/engine.py
Lõi xử lý Back-test: giả lập giao dịch trên dữ liệu lịch sử.

Quy ước giá (khớp MetaTrader5 / bot thật):
  - Dữ liệu nến MT5 là giá BID (open/high/low/close).
  - BUY  : vào lệnh tại ASK (open + spread), đóng lệnh tại BID.
  - SELL : vào lệnh tại BID (open),        đóng lệnh tại ASK (giá + spread).
  - Sàn theo dõi SL/TP liên tục TRONG nến (dùng high/low).
  - Bot chỉ xét chốt một phần + dời SL hòa vốn MỘT LẦN mỗi nến, tại giá đóng nến
    (giống bot_engine._on_candle_tick), nên partial/BE dùng giá close chứ không
    dùng high/low.
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
        self.spread = spread  # Spread thật từ broker (đơn vị: price, ví dụ 0.22 cho XAUUSD)
        self.volume_min = volume_min
        self.volume_step = volume_step
        self.trades = []
        self.current_position = None  # None | {"type": "BUY/SELL", "entry": float, "sl": float, "tp": float, "time": datetime}
        self.pending = None           # lệnh chờ limit: {"type","level","sl","tp","expire_bar"}

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
        Mỗi vòng lặp xử lý 1 nến k theo đúng trình tự của bot thật:
          1. Sàn khớp SL/TP trong nến k (nếu đang có lệnh).
          2. Lúc nến k đóng: bot xét chốt một phần + dời SL hòa vốn.
          3. Lúc nến k đóng: bot xét tín hiệu và vào lệnh ở open nến k+1.
        """
        print(f"--- BẮT ĐẦU BACK-TEST: {self.strategy.name} ---")
        df = self.strategy.calculate_indicators(df)

        # Bắt đầu từ khi đủ dữ liệu cho các chỉ báo (ví dụ EMA 200)
        start_idx = 100
        if len(df) <= start_idx:
            print("Dữ liệu quá ngắn để Back-test")
            return []

        n = len(df)
        for k in range(start_idx, n):
            candle = df.iloc[k]

            # 0. Lệnh chờ limit (entry hồi giá): kiểm tra khớp trong nến k
            if self.current_position is None and self.pending is not None:
                self._try_fill_pending(candle, k)

            # 1. Sàn theo dõi SL/TP liên tục trong nến k (BUY khớp BID, SELL khớp ASK)
            if self.current_position is not None:
                self._check_sl_tp(candle)

            # 2. Lúc nến k đóng, bot xét chốt một phần + dời SL hòa vốn tại giá close
            if self.current_position is not None:
                self._manage_position(candle)

            # 3. Lúc nến k đóng, bot xét tín hiệu; vào lệnh tại open nến k+1
            if self.current_position is None and self.pending is None and (k + 1) < n:
                # [-1] = nến k+1 (vừa mở, chưa đóng), [-2] = nến k (đã đóng) — giống
                # df mà bot thật truyền vào check_signal/get_sl_tp.
                sub_df = df.iloc[:k + 2]

                # Chiến lược dùng entry hồi giá → sinh lệnh chờ limit
                setup = self.strategy.get_pending_setup(sub_df)
                if setup:
                    self.pending = {
                        "type": setup["type"],
                        "level": float(setup["level"]),
                        "sl": float(setup["sl"]),
                        "tp": float(setup["tp"]),
                        "expire_bar": (k + 1) + int(setup.get("wait_min", 60)),
                    }
                else:
                    signal = self.strategy.check_signal(sub_df)

                    if signal:
                        next_candle = df.iloc[k + 1]
                        open_price = next_candle["open"]
                        # BUY khớp tại ASK = open + spread; SELL khớp tại BID = open
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
                                "entry_time": next_candle.name if hasattr(next_candle, 'name') else (k + 1)
                            }

        self._print_summary()
        return self.trades

    def _try_fill_pending(self, candle, k):
        """Mô phỏng lệnh chờ limit: khớp khi giá hồi tới `level` trong nến k.
        BUY khớp tại ASK (= level + spread); SELL khớp tại BID (= level).
        Hủy nếu giá chạm SL trước, hết hạn, hoặc vượt killzone."""
        p = self.pending
        if p is None:
            return
        if k > p["expire_bar"]:
            self.pending = None
            return

        bid_high = candle["high"]
        bid_low = candle["low"]
        ask_high = candle["high"] + self.spread

        if p["type"] == "BUY":
            if bid_low <= p["sl"]:          # hỏng setup trước khi khớp
                self.pending = None
                return
            if bid_low <= p["level"]:
                entry = round(p["level"] + self.spread, self.digits)
                sl = round(p["sl"], self.digits)
                tp = round(p["tp"], self.digits)
                self.pending = None
                if entry - sl > 0:
                    self._open_from_pending("BUY", entry, sl, tp, candle)
        else:
            if ask_high >= p["sl"]:
                self.pending = None
                return
            if bid_high >= p["level"]:
                entry = round(p["level"], self.digits)
                sl = round(p["sl"], self.digits)
                tp = round(p["tp"], self.digits)
                self.pending = None
                if sl - entry > 0:
                    self._open_from_pending("SELL", entry, sl, tp, candle)

    def _open_from_pending(self, typ, entry, sl, tp, candle):
        self.current_position = {
            "type": typ,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "R": abs(entry - sl),
            "partial_frac": self._effective_partial_frac(),
            "partial_at_r": getattr(self.strategy, "partial_at_r", 0) or 0,
            "partial_done": False,
            "pnl_partial": 0.0,
            "entry_time": candle.name if hasattr(candle, 'name') else "N/A",
        }

    def _check_sl_tp(self, candle):
        """Sàn khớp SL/TP TRONG nến. BUY theo BID, SELL theo ASK.
        Ưu tiên xử lý gap ở giá open, rồi mới tới high/low. Nếu 1 nến chạm cả SL
        lẫn TP thì chọn SL (kịch bản bất lợi) cho an toàn."""
        pos = self.current_position
        d = self.digits

        # Nến là BID; ASK = BID + spread
        bid_open = candle["open"]
        bid_high = candle["high"]
        bid_low = candle["low"]
        ask_open = candle["open"] + self.spread
        ask_high = candle["high"] + self.spread
        ask_low = candle["low"] + self.spread

        if pos["type"] == "BUY":
            # BUY đóng ở BID: SL khi bid <= sl, TP khi bid >= tp
            if bid_open <= pos["sl"]:
                self._close_position(bid_open, candle)
            elif bid_open >= pos["tp"]:
                self._close_position(bid_open, candle)
            elif bid_low <= pos["sl"]:
                self._close_position(pos["sl"], candle)
            elif bid_high >= pos["tp"]:
                self._close_position(pos["tp"], candle)
        else:
            # SELL đóng ở ASK: SL khi ask >= sl, TP khi ask <= tp
            if ask_open >= pos["sl"]:
                self._close_position(ask_open, candle)
            elif ask_open <= pos["tp"]:
                self._close_position(ask_open, candle)
            elif ask_high >= pos["sl"]:
                self._close_position(pos["sl"], candle)
            elif ask_low <= pos["tp"]:
                self._close_position(pos["tp"], candle)

    def _manage_position(self, candle):
        """Bot xét chốt một phần + dời SL hòa vốn lúc nến đóng.
        Giá quan sát: BUY = BID close, SELL = ASK close (= close + spread) — giống
        bot_engine._on_candle_tick (price = tick.bid nếu BUY, tick.ask nếu SELL)."""
        pos = self.current_position
        d = self.digits
        if pos["type"] == "BUY":
            price = candle["close"]
        else:
            price = candle["close"] + self.spread

        R = pos.get("R", 0)
        if R <= 0:
            return

        if pos["type"] == "BUY":
            favorable = price - pos["entry"]
        else:
            favorable = pos["entry"] - price
        hw = favorable / R  # số R đã đi được

        # 1) Chốt một phần tại giá thị trường hiện tại (chỉ 1 lần)
        part_f = pos.get("partial_frac", 0) or 0
        part_r = pos.get("partial_at_r", 0) or 0
        if part_f > 0 and part_r > 0 and not pos["partial_done"] and hw >= part_r:
            if pos["type"] == "BUY":
                unit = price - pos["entry"]
            else:
                unit = pos["entry"] - price
            pos["pnl_partial"] = part_f * unit * self.lot_size * 100
            pos["partial_done"] = True

        # 2) Dời SL về hòa vốn khi đạt be_move_at_r (không tự dời khi partial)
        be_r = getattr(self.strategy, "be_move_at_r", 0) or 0
        if be_r > 0 and hw >= be_r:
            if pos["type"] == "BUY":
                pos["sl"] = round(max(pos["sl"], pos["entry"]), d)
            else:
                pos["sl"] = round(min(pos["sl"], pos["entry"]), d)

    def _close_position(self, exit_price, candle):
        """Đóng toàn bộ phần còn lại tại `exit_price` và ghi nhận lệnh."""
        pos = self.current_position
        exit_price = round(float(exit_price), self.digits)
        pnl_points = (exit_price - pos["entry"]) if pos["type"] == "BUY" else (pos["entry"] - exit_price)

        # Chỉ còn phần khối lượng chưa chốt + phần đã chốt sớm (nếu có)
        rem = 1.0 - (pos["partial_frac"] if pos["partial_done"] else 0.0)
        profit_value = pnl_points * self.lot_size * rem * 100 + pos["pnl_partial"]

        # Phân loại theo lãi/lỗ thực tế (chốt một phần rồi thoát hòa vốn vẫn là PROFIT)
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
            "exit_time": candle.name if hasattr(candle, 'name') else "N/A",
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
