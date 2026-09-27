"""
strategies/master_xau_tp.py
MASTER_XAU_TP — Hedge + Grid + Martingale + Basket TP (XAUUSD).
Engine do MasterEngine quản lý, không qua check_signal.

Class này chỉ mang tham số + đánh dấu `is_master` để BotEngine chạy vòng lặp riêng.
"""
import pandas as pd

import config
from .base import BaseStrategy


class MasterXAUTPStrategy(BaseStrategy):
    is_master = True    # BotEngine nhận biết để chạy vòng lặp riêng
    no_session = True   # Không giới hạn phiên (24/7)

    def __init__(
        self,
        lot_start=config.MASTER_LOT_START,
        mart=config.MASTER_MART,
        max_lot=config.MASTER_MAX_LOT,
        grid_step=config.MASTER_GRID_STEP,
        max_level=config.MASTER_MAX_LEVEL,
        init_hedge=config.MASTER_INIT_HEDGE,
        trail_start=config.MASTER_TRAIL_START,
        trail_step=config.MASTER_TRAIL_STEP,
        init_sl=config.MASTER_INIT_SL,
        tp_usd=config.MASTER_TP_USD,
        tp_pct=config.MASTER_TP_PCT,
        trail_tp_start=config.MASTER_TRAIL_TP_START,
        trail_tp_step=config.MASTER_TRAIL_TP_STEP,
        magic=config.MAGIC_MASTER,
    ):
        super().__init__("MASTER_XAU_TP", magic=magic)
        self.lot_start = lot_start
        self.mart = mart
        self.max_lot = max_lot
        self.grid_step = grid_step
        self.max_level = int(max_level)
        self.init_hedge = bool(init_hedge)
        self.trail_start = trail_start
        self.trail_step = trail_step
        self.init_sl = init_sl
        self.tp_usd = tp_usd
        self.tp_pct = tp_pct
        self.trail_tp_start = trail_tp_start
        self.trail_tp_step = trail_tp_step
        self.comment = config.MASTER_COMMENT
        self.history_bars = 50

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        return df

    def check_signal(self, df: pd.DataFrame):
        return None

    def get_sl_tp(self, df, entry_price, digits, order_type):
        return None, None
