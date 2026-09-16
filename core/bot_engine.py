"""
core/bot_engine.py
Hệ điều hành của Bot (Trading Loop).
"""
import time
import threading
import logging
from datetime import datetime, timezone

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

        # ── BE-move: dời SL về giá mở lệnh khi đã lãi >= R lần ──
        be_r = getattr(strategy, "be_move_at_r", 0)
        if position is not None and be_r > 0 and position.sl:
            info = mt5h.get_symbol_info(config.SYMBOL)
            tick = mt5h.get_tick(config.SYMBOL)
            if info and tick:
                entry = position.price_open
                stop = position.sl
                dist = abs(entry - stop)
                if dist > 0:
                    buy_reach = (position.type == 0 and tick.bid >= entry + dist)
                    sell_reach = (position.type == 1 and tick.ask <= entry - dist)
                    digits = info.digits
                    # Chưa BE: SL vẫn khác giá mở lệnh
                    not_yet_be = round(stop, digits) != round(entry, digits)
                    if (buy_reach or sell_reach) and not_yet_be:
                        logger.info(
                            f"⚡ BE-MOVE  |  ticket={position.ticket}  |  "
                            f"SL {stop:.{digits}f} → {entry:.{digits}f}"
                        )
                        mt5h.modify_position(
                            config.SYMBOL, position.ticket,
                            sl=round(entry, digits), tp=position.tp,
                        )

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
