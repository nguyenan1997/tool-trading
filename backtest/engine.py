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

MÔ PHỎNG SÁT THỰC TẾ (các tham số mới):
  - exec_df : nến NHỎ HƠN (vd M1) để xác định THỨ TỰ chạm SL/TP trong nến tín hiệu.
    Không có exec_df thì chỉ dùng OHLC nến tín hiệu (kém chính xác hơn).
  - commission_per_lot : phí mỗi lot mỗi chiều (USD) — trừ vào PnL khi đóng.
  - slippage_points    : trượt giá (points) áp BẤT LỢI cho mọi lần khớp.
  - spread_mult / spread_min : nhân/đặt sàn cho spread của nến (mô phỏng spread dãn).
  - realistic_fills    : nến khớp lệnh chờ KHÔNG tính TP trong cùng nến (SL trước).
"""
import math
import numpy as np
import pandas as pd
import logging
from datetime import datetime
from strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class Backtester:
    def __init__(self, strategy: BaseStrategy, initial_balance=1000, lot_size=0.1, digits=5, spread=0.30,
                 volume_min=0.01, volume_step=0.01, realistic_fills=False,
                 exec_df=None, commission_per_lot=0.0, slippage_points=0.0,
                 spread_mult=1.0, spread_min=0.0):
        self.strategy = strategy
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.lot_size = lot_size
        self.digits = digits
        self.spread = spread  # Spread dự phòng (price) nếu nến không có cột 'spread'
        self.volume_min = volume_min
        self.volume_step = volume_step
        # True = mô phỏng thực tế hơn: nến khớp lệnh chờ KHÔNG được tính TP trong
        # cùng nến đó (tránh lợi thế ảo do OHLC không biết thứ tự tick trong nến).
        self.realistic_fills = realistic_fills
        # Mô phỏng chi phí / đường đi
        self.exec_df = exec_df
        self.commission_per_lot = commission_per_lot
        self.slippage_points = slippage_points
        self.spread_mult = spread_mult
        self.spread_min = spread_min
        self._slip = float(slippage_points or 0) * (10.0 ** -self.digits)
        self.trades = []
        self.current_position = None  # None | {"type","entry","sl","tp",...}
        self.pending = None           # lệnh chờ limit: {"type","level","sl","tp","expire_bar"}
        self._df = None
        self._exec_time = None
        self._bar_ns = 0

    def _bar_spread(self, candle) -> float:
        """Spread THẬT của nến (price) nếu dữ liệu có cột 'spread' (points), ngược lại dùng spread cố định.
        Sau đó nhân `spread_mult` và áp sàn `spread_min` để phản ánh spread dãn lúc biến động."""
        sp = self.spread
        try:
            s = candle.get("spread") if hasattr(candle, "get") else None
            if s is not None and np.isfinite(s) and s >= 0:
                sp = float(s) * (10.0 ** -self.digits)
        except Exception:
            pass
        sp = sp * self.spread_mult
        if sp < self.spread_min:
            sp = self.spread_min
        return sp

    def _setup_exec(self):
        """Chuẩn bị mảng nến nhỏ (exec_df) để tra cứu nhanh đường đi trong từng nến tín hiệu."""
        ed = self.exec_df
        self._exec_time = None
        if ed is None or len(ed) == 0:
            return
        try:
            idx = pd.to_datetime(ed.index).values.astype("datetime64[ns]").astype("int64")
            self._exec_time = idx
            self._eo = ed["open"].to_numpy(dtype=float)
            self._eh = ed["high"].to_numpy(dtype=float)
            self._el = ed["low"].to_numpy(dtype=float)
            self._ec = ed["close"].to_numpy(dtype=float)
            if "spread" in ed.columns:
                self._esp = ed["spread"].to_numpy(dtype=float) * (10.0 ** -self.digits)
            else:
                self._esp = np.full(len(ed), float(self.spread))
        except Exception as e:
            logger.warning(f"exec_df lỗi, bỏ qua: {e}")
            self._exec_time = None

    def _sub_bars(self, k):
        """Danh sách nến nhỏ [(o,h,l,c,spread_price)] trong nến tín hiệu thứ k.
        Không có exec_df -> trả đúng 1 'nến nhỏ' = nến tín hiệu (hành vi cũ)."""
        c = self._df.iloc[k]
        if self._exec_time is None:
            return [(float(c["open"]), float(c["high"]), float(c["low"]),
                     float(c["close"]), self._bar_spread(c))]
        t = np.datetime64(pd.Timestamp(self._df.index[k])).astype("datetime64[ns]").astype("int64")
        lo = int(np.searchsorted(self._exec_time, t, "left"))
        hi = int(np.searchsorted(self._exec_time, t + self._bar_ns, "left"))
        if hi <= lo:
            return [(float(c["open"]), float(c["high"]), float(c["low"]),
                     float(c["close"]), self._bar_spread(c))]
        out = []
        for i in range(lo, hi):
            sp = float(self._esp[i]) * self.spread_mult
            if sp < self.spread_min:
                sp = self.spread_min
            out.append((float(self._eo[i]), float(self._eh[i]), float(self._el[i]),
                        float(self._ec[i]), sp))
        return out

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
          1. Sàn khớp SL/TP trong nến k (nếu đang có lệnh) — dùng nến nhỏ nếu có.
          2. Lúc nến k đóng: bot xét chốt một phần + dời SL hòa vốn.
          3. Lúc nến k đóng: bot xét tín hiệu và vào lệnh ở open nến k+1.
        """
        print(f"--- BẮT ĐẦU BACK-TEST: {self.strategy.name} ---")
        df = self.strategy.calculate_indicators(df)
        self._df = df

        # Số phút mỗi nến (để quy đổi thời gian chờ lệnh limit từ phút → nến)
        self._bar_minutes = 1
        try:
            idx = pd.to_datetime(df.index)
            diffs = pd.Series(idx).diff().dt.total_seconds().dropna()
            if len(diffs):
                med = float(diffs.median())
                if med > 0:
                    self._bar_minutes = max(1, int(round(med / 60.0)))
        except Exception:
            self._bar_minutes = 1
        self._bar_ns = int(self._bar_minutes * 60 * 1_000_000_000)
        self._setup_exec()
        if self._exec_time is not None:
            print(f"Đường đi thực tế: dùng nến nhỏ ({len(self._exec_time):,} nến) để khớp SL/TP")
        if self._slip or self.commission_per_lot:
            print(f"Chi phí: commission={self.commission_per_lot}/lot/chiều, "
                  f"slippage={self.slippage_points} pts, spread×{self.spread_mult}"
                  + (f" (sàn {self.spread_min})" if self.spread_min else ""))

        # Bắt đầu SAU giai đoạn warmup để chỉ báo (EMA/ADX/ATR…) hội tụ.
        warmup = int(getattr(self.strategy, "warmup_bars", 100) or 100)
        start_idx = max(100, warmup)
        if len(df) <= start_idx + 1:
            print(f"Dữ liệu quá ngắn để Back-test (cần > {start_idx + 1} nến warmup, đang có {len(df)})")
            return []
        print(f"Warmup: bỏ qua {start_idx} nến đầu  |  giao dịch từ {df.index[start_idx]}")

        n = len(df)
        for k in range(start_idx, n):
            candle = df.iloc[k]
            subs = self._sub_bars(k)

            # 0. Lệnh chờ limit (entry hồi giá): kiểm tra khớp trong nến k (theo nến nhỏ)
            filled_at = -1
            if self.current_position is None and self.pending is not None:
                filled_at = self._try_fill_pending(subs, k, candle)

            # 1. Sàn theo dõi SL/TP trong nến k (BUY khớp BID, SELL khớp ASK)
            if self.current_position is not None:
                start = filled_at if filled_at >= 0 else 0
                self._check_sl_tp(subs, start=start, just_opened=(filled_at >= 0), candle=candle)

            # 2. Lúc nến k đóng, bot xét chốt một phần + dời SL hòa vốn tại giá close
            if self.current_position is not None:
                self._manage_position(candle)

            # 3. Lúc nến k đóng, bot xét tín hiệu; vào lệnh tại open nến k+1
            if self.current_position is None and self.pending is None and (k + 1) < n:
                sub_df = df.iloc[:k + 2]
                setup = self.strategy.get_pending_setup(sub_df)
                if setup:
                    wait_bars = max(1, int(round(float(setup.get("wait_min", 60)) / self._bar_minutes)))
                    self.pending = {
                        "type": setup["type"],
                        "level": float(setup["level"]),
                        "sl": float(setup["sl"]),
                        "tp": float(setup["tp"]),
                        "expire_bar": (k + 1) + wait_bars,
                    }
                else:
                    signal = self.strategy.check_signal(sub_df)
                    if signal:
                        next_candle = df.iloc[k + 1]
                        open_price = next_candle["open"]
                        sp = self._bar_spread(next_candle)
                        if signal == "BUY":
                            entry_price = round(open_price + sp + self._slip, self.digits)
                        else:
                            entry_price = round(open_price - self._slip, self.digits)
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

    def _try_fill_pending(self, subs, k, candle):
        """Mô phỏng lệnh chờ limit: khớp khi giá hồi tới `level` (duyệt nến nhỏ theo thứ tự).
        BUY khớp tại ASK (= level + spread + slippage); SELL khớp tại BID (= level − slippage).
        Trả về chỉ số nến nhỏ đã khớp (>=0) hoặc -1 nếu chưa khớp."""
        p = self.pending
        if p is None:
            return -1
        if k > p["expire_bar"]:
            self.pending = None
            return -1

        for i, (o, h, l, c, sp) in enumerate(subs):
            if p["type"] == "BUY":
                if l <= p["level"]:
                    entry = round(p["level"] + sp + self._slip, self.digits)
                    sl = round(p["sl"], self.digits)
                    tp = round(p["tp"], self.digits)
                    self.pending = None
                    if entry - sl > 0:
                        self._open_from_pending("BUY", entry, sl, tp, candle)
                        return i
                    return -1
            else:
                if h >= p["level"]:
                    entry = round(p["level"] - self._slip, self.digits)
                    sl = round(p["sl"], self.digits)
                    tp = round(p["tp"], self.digits)
                    self.pending = None
                    if sl - entry > 0:
                        self._open_from_pending("SELL", entry, sl, tp, candle)
                        return i
                    return -1
        return -1

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

    def _check_sl_tp(self, subs, start=0, just_opened=False, candle=None):
        """Sàn khớp SL/TP theo ĐƯỜNG ĐI (duyệt từng nến nhỏ theo thứ tự thời gian).
        BUY theo BID, SELL theo ASK. Trong 1 nến nhỏ nếu chạm cả SL lẫn TP -> chọn SL
        (bất lợi). `just_opened=True`: nến nhỏ đầu (lúc khớp lệnh) KHÔNG tính TP
        (tránh lợi thế ảo do không biết high/low đến trước hay sau lúc khớp)."""
        pos = self.current_position
        d = self.digits
        for i in range(start, len(subs)):
            o, h, l, c, sp = subs[i]
            allow_tp = not (self.realistic_fills and just_opened and i == start)
            bid_open, bid_high, bid_low = o, h, l
            ask_open, ask_high, ask_low = o + sp, h + sp, l + sp
            if pos["type"] == "BUY":
                if bid_open <= pos["sl"]:
                    self._close_position(bid_open, candle)
                elif allow_tp and bid_open >= pos["tp"]:
                    self._close_position(bid_open, candle)
                elif bid_low <= pos["sl"]:
                    self._close_position(pos["sl"], candle)
                elif allow_tp and bid_high >= pos["tp"]:
                    self._close_position(pos["tp"], candle)
            else:
                if ask_open >= pos["sl"]:
                    self._close_position(ask_open, candle)
                elif allow_tp and ask_open <= pos["tp"]:
                    self._close_position(ask_open, candle)
                elif ask_high >= pos["sl"]:
                    self._close_position(pos["sl"], candle)
                elif allow_tp and ask_low <= pos["tp"]:
                    self._close_position(pos["tp"], candle)
            if self.current_position is None:
                return

    def _manage_position(self, candle):
        """Bot xét chốt một phần + dời SL hòa vốn lúc nến đóng (giá close)."""
        pos = self.current_position
        d = self.digits
        if pos["type"] == "BUY":
            price = candle["close"]
        else:
            price = candle["close"] + self._bar_spread(candle)

        R = pos.get("R", 0)
        if R <= 0:
            return

        if pos["type"] == "BUY":
            favorable = price - pos["entry"]
        else:
            favorable = pos["entry"] - price
        hw = favorable / R

        part_f = pos.get("partial_frac", 0) or 0
        part_r = pos.get("partial_at_r", 0) or 0
        if part_f > 0 and part_r > 0 and not pos["partial_done"] and hw >= part_r:
            if pos["type"] == "BUY":
                unit = price - pos["entry"]
            else:
                unit = pos["entry"] - price
            pos["pnl_partial"] = part_f * unit * self.lot_size * 100
            pos["partial_done"] = True

        be_r = getattr(self.strategy, "be_move_at_r", 0) or 0
        if be_r > 0 and hw >= be_r:
            if pos["type"] == "BUY":
                pos["sl"] = round(max(pos["sl"], pos["entry"]), d)
            else:
                pos["sl"] = round(min(pos["sl"], pos["entry"]), d)

        trail_r = getattr(self.strategy, "trail_at_r", 0) or 0
        trail_gap = getattr(self.strategy, "trail_gap_r", 1.0) or 0
        if trail_r > 0 and trail_gap > 0 and hw >= trail_r:
            if pos["type"] == "BUY":
                new_sl = price - trail_gap * R
                pos["sl"] = round(max(pos["sl"], new_sl), d)
            else:
                new_sl = price + trail_gap * R
                pos["sl"] = round(min(pos["sl"], new_sl), d)

    def _close_position(self, exit_price, candle):
        """Đóng toàn bộ phần còn lại tại `exit_price` (đã áp slippage bất lợi) và ghi nhận lệnh."""
        pos = self.current_position
        # Slippage bất lợi khi thoát: BUY bán ra giá thấp hơn, SELL mua vào giá cao hơn
        raw = float(exit_price) - self._slip if pos["type"] == "BUY" else float(exit_price) + self._slip
        exit_price = round(raw, self.digits)
        pnl_points = (exit_price - pos["entry"]) if pos["type"] == "BUY" else (pos["entry"] - exit_price)

        rem = 1.0 - (pos["partial_frac"] if pos["partial_done"] else 0.0)
        profit_value = pnl_points * self.lot_size * rem * 100 + pos["pnl_partial"]
        # Phí: commission mỗi lot mỗi chiều (vào + ra)
        profit_value -= self.commission_per_lot * self.lot_size * 2

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
