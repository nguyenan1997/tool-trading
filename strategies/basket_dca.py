"""
strategies/basket_dca.py
HỆ THỐNG — Dynamic Basket DCA (XAUUSD). Chạy liên tục, poll theo giây.

Luật (do BasketDCAEngine thực thi):
  - Đặt straddle BUY STOP + SELL STOP quanh giá; bên nào khớp trước thì vào lệnh,
    hủy bên còn lại.
  - Lệnh đơn: TP = giá vào ± BASKET_TP_INITIAL.
  - Giá đi ngược BASKET_STEP → nhồi DCA (lot Fibonacci). TP động = bình quân ± TP_BASKET.
  - Không SL.

Class chỉ mang tham số + cờ `is_basket` để BotEngine chạy đúng vòng lặp.
"""
import pandas as pd

import config
from .base import BaseStrategy


class BasketDCAStrategy(BaseStrategy):
    is_basket = True    # BotEngine nhận biết để chạy vòng lặp riêng
    no_session = True   # 24/7

    def __init__(
        self,
        lot0=config.BASKET_LOT0,
        init_dist=config.BASKET_INIT_DIST,
        step=config.BASKET_STEP,
        be_currency=config.BASKET_BE_CURRENCY,
        extra_safety=config.BASKET_EXTRA_SAFETY,
        max_levels=config.BASKET_MAX_LEVELS,
        max_total_lot=config.BASKET_MAX_TOTAL_LOT,
        magic=config.MAGIC_BASKET,
    ):
        super().__init__("Dynamic Basket DCA", magic=magic)
        self.lot = lot0
        self.init_dist = init_dist
        self.step = step
        self.be_currency = be_currency
        self.extra_safety = extra_safety
        self.profit_target = be_currency + extra_safety
        self.close_by_live_pl = True     # "Also close by live total P/L"
        self.max_levels = max_levels
        self.max_total_lot = max_total_lot
        self.comment = config.BASKET_COMMENT
        self.history_bars = 200

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        return df

    def check_signal(self, df: pd.DataFrame):
        return None

    def get_sl_tp(self, df, entry_price, digits, order_type):
        return None, None
