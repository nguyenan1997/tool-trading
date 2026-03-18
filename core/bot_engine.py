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

    def start(self):
        if not self.is_running:
            self.is_running = True
            self.status = "Running"
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            logger.info("Bot Engine STARTED")

    def stop(self):
        self.is_running = False
        self.status = "Stopped"
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
        df = mt5h.get_candles(config.SYMBOL, config.TIMEFRAME, count=200)
        if df is None or len(df) < 50: return

        df = strategy.calculate_indicators(df)
        signal = strategy.check_signal(df)
        position = mt5h.get_open_position(config.SYMBOL, config.MAGIC_NUMBER)
        
        if position is None and signal:
            info = mt5h.get_symbol_info(config.SYMBOL)
            tick = mt5h.get_tick(config.SYMBOL)
            if not info or not tick: return
            
            price = tick.ask if signal == "BUY" else tick.bid
            sl, tp = strategy.get_sl_tp(df, price, info.digits, signal)
            
            if sl and tp:
                logger.info(f"⚡ EXECUTE {signal} | Strategy: {strategy.name} | Price: {price} | SL: {sl} | TP: {tp}")
                mt5h.open_position(config.SYMBOL, signal, config.FIXED_LOT, sl, tp, config.MAGIC_NUMBER, config.ORDER_COMMENT)

bot_engine = BotEngine()
