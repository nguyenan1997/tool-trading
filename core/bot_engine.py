"""
core/bot_engine.py
Hệ điều hành của Bot (Trading Loop).
"""
import time
import threading
import logging
from datetime import datetime, timezone, timedelta

import config
from . import mt5_handler as mt5h
from strategies.manager import strategy_manager

logger = logging.getLogger(__name__)

class BotEngine:
    def __init__(self):
        self.is_running = False
        self._thread: threading.Thread = None # type: ignore
        self.last_candle_time = None
        self.status = "Stopped"
        self._lock = threading.Lock()
        self._pos_r = {}          # ticket -> R ban đầu (khoảng cách entry→SL)
        self._partial_done = set()  # các ticket đã chốt một phần
        self._last_session_log = 0.0  # lần cuối ghi log phiên

    def start(self):
        with self._lock:
            if self.is_running:
                return
            # Chờ thread cũ thoát hẳn để tránh 2 vòng lặp chạy song song
            if self._thread is not None and self._thread.is_alive():
                return
            self.is_running = True
            self.status = "Running"
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            logger.info("Bot Engine STARTED")

    def stop(self):
        with self._lock:
            self.is_running = False
            self.status = "Stopped"
            t = self._thread
            if t is not None and t.is_alive() and t is not threading.current_thread():
                t.join(timeout=10)
            logger.info("Bot Engine STOPPED")

    def _server_now(self) -> datetime:
        """Giờ broker/server (naive) lấy từ tick mới nhất."""
        tick = mt5h.get_tick(config.SYMBOL)
        if tick is not None:
            return datetime.fromtimestamp(tick.time, timezone.utc).replace(tzinfo=None)
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def get_session_status(self) -> dict:
        """Trạng thái phiên giao dịch + đếm ngược tới lúc mở phiên (giờ broker)."""
        start, end = getattr(config, "TM_SESSION", (12, 21))
        now = self._server_now()
        h = now.hour
        label_range = f"{start:02d}–{end:02d}h"
        if start <= h <= end:
            return {
                "in_session": True, "now": now.strftime("%H:%M"),
                "session": label_range, "seconds_to_open": 0, "open_at": None,
                "label": f"Đang trong phiên vào lệnh ({label_range} giờ broker)",
            }
        target = now.replace(hour=start, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        secs = int((target - now).total_seconds())
        hh, mm = divmod(secs // 60, 60)
        return {
            "in_session": False, "now": now.strftime("%H:%M"),
            "session": label_range, "seconds_to_open": secs,
            "open_at": target.strftime("%H:%M"),
            "label": f"Còn {hh}h{mm:02d}m nữa tới phiên vào lệnh ({start:02d}:00 giờ broker)",
        }

    def _maybe_log_session(self, interval_sec: int = 300):
        """Ghi log trạng thái phiên định kỳ (mặc định mỗi 5 phút)."""
        if time.time() - self._last_session_log < interval_sec:
            return
        self._last_session_log = time.time()
        try:
            s = self.get_session_status()
            logger.info(f"⏳ PHIÊN  |  {s['label']}  |  giờ broker {s['now']}")
        except Exception as e:
            logger.error(f"session log error: {e}")

    def _run_loop(self):
        if not mt5h.connect():
            self.status = "Error: MT5 Connect Failed"
            self.is_running = False
            return

        try:
            while self.is_running:
                # 1. Chờ nến mới
                tf_seconds = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600}.get(config.TIMEFRAME, 60)
                now_sec = datetime.now(timezone.utc).timestamp()
                wait = tf_seconds - (now_sec % tf_seconds) + 0.5
                
                time.sleep(min(wait, 5)) 
                if wait > 5: continue

                try:
                    self._maybe_log_session()
                    self._on_candle_tick()
                except Exception as e:
                    logger.error(f"Error in tick: {e}")
                    time.sleep(10)

        finally:
            mt5h.disconnect()

    def _on_candle_tick(self):
        strategy = strategy_manager.get_current_strategy()
        magic = getattr(strategy, "magic", config.MAGIC_NUMBER)
        count = getattr(strategy, "history_bars", 200)
        df = mt5h.get_candles(config.SYMBOL, config.TIMEFRAME, count=count)
        if df is None or len(df) < 50:
            return

        df = strategy.calculate_indicators(df)
        signal = strategy.check_signal(df)
        # Chỉ quản lý lệnh của ĐÚNG chiến lược hiện tại (magic riêng)
        position = mt5h.get_open_position(config.SYMBOL, magic)

        # ── Quản lý lệnh đang mở ──
        #   1) Chốt một phần (partial TP) khi đạt partial_at_r × R
        #   2) Dời SL về hòa vốn (BE) khi đạt be_move_at_r × R
        be_r = getattr(strategy, "be_move_at_r", 0)
        part_r = getattr(strategy, "partial_at_r", 0)
        part_f = getattr(strategy, "partial_frac", 0)

        if position is not None and position.sl:
            info = mt5h.get_symbol_info(config.SYMBOL)
            tick = mt5h.get_tick(config.SYMBOL)
            if info and tick:
                entry = position.price_open
                stop = position.sl
                digits = info.digits
                not_yet_be = round(stop, digits) != round(entry, digits)

                # Ghi nhớ R ban đầu khi SL còn ở mức gốc (chưa dời BE)
                if position.ticket not in self._pos_r and not_yet_be:
                    self._pos_r[position.ticket] = abs(entry - stop)
                R = self._pos_r.get(position.ticket, 0.0)

                price = tick.bid if position.type == 0 else tick.ask

                # 1) Chốt một phần (chỉ 1 lần cho mỗi ticket)
                if part_f > 0 and part_r > 0 and R > 0 and position.ticket not in self._partial_done:
                    hit_part = (
                        (position.type == 0 and price >= entry + part_r * R) or
                        (position.type == 1 and price <= entry - part_r * R)
                    )
                    if hit_part:
                        if mt5h.split_volume(position.volume, part_f, info) <= 0:
                            # Khối lượng quá nhỏ để chia → bỏ qua vĩnh viễn cho ticket này
                            self._partial_done.add(position.ticket)
                        elif mt5h.close_position_partial(position, part_f, magic, "partial TP"):
                            self._partial_done.add(position.ticket)

                # 2) Dời SL về hòa vốn
                if be_r > 0 and R > 0 and not_yet_be:
                    hit_be = (
                        (position.type == 0 and price >= entry + be_r * R) or
                        (position.type == 1 and price <= entry - be_r * R)
                    )
                    if hit_be:
                        logger.info(
                            f"⚡ BE-MOVE  |  ticket={position.ticket}  |  "
                            f"SL {stop:.{digits}f} → {entry:.{digits}f}"
                        )
                        mt5h.modify_position(
                            config.SYMBOL, position.ticket,
                            sl=round(entry, digits), tp=position.tp,
                        )

        # Dọn bộ nhớ theo dõi các ticket đã đóng
        open_tickets = {
            p.ticket for p in mt5h.get_open_positions(config.SYMBOL, strategy_manager.get_magics())
        }
        for t in list(self._pos_r.keys()):
            if t not in open_tickets:
                self._pos_r.pop(t, None)
                self._partial_done.discard(t)

        if position is None and signal:
            info = mt5h.get_symbol_info(config.SYMBOL)
            tick = mt5h.get_tick(config.SYMBOL)
            if not info or not tick: return
            
            price = tick.ask if signal == "BUY" else tick.bid
            sl, tp = strategy.get_sl_tp(df, price, info.digits, signal)
            
            if sl and tp:
                logger.info(f"⚡ EXECUTE {signal} | Strategy: {strategy.name} | Price: {price} | SL: {sl} | TP: {tp}")
                mt5h.open_position(config.SYMBOL, signal, config.FIXED_LOT, sl, tp, magic, config.ORDER_COMMENT)

bot_engine = BotEngine()
