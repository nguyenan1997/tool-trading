"""
strategies/bigmouse.py
HỆ THỐNG 5 — BigMouse Hedging (XAUUSD). Chạy liên tục 24/7, không phụ thuộc khung nến.

Mô phỏng lại EA "BigMouse Hedging" (MT5):
  - Mở 1 lệnh MARKET "mỏ neo" (mặc định BUY), TP cách giá vào BIGMOUSE_TP_USD.
  - Đặt kèm 1 lệnh CHỜ STOP ngược chiều (SELL STOP) lot lớn hơn (× MULT),
    cách giá vào BIGMOUSE_HEDGE_TRIGGER_USD để hedge khi giá đi ngược.
  - Giá lên chạm TP mỏ neo → hủy SELL STOP (thắng) → mở chu kỳ mới.
  - Giá xuống chạm mức stop → SELL STOP khớp thành hedge (basket).
  - Đóng cả basket khi tổng lãi nổi đạt BIGMOUSE_BASKET_TP_USD.
  - Martingale: sau chu kỳ LỖ → nhân lot; sau chu kỳ LÃI → về lot gốc.

Class này chỉ mang tham số + đánh dấu `is_bigmouse` để BotEngine chạy
đúng vòng lặp riêng (BigMouseEngine). Các hàm của BaseStrategy rỗng.
"""
import pandas as pd

import config
from .base import BaseStrategy


class BigMouseStrategy(BaseStrategy):
    is_bigmouse = True   # BotEngine nhận biết để chạy vòng lặp riêng
    no_session = True    # Không giới hạn phiên (24/7)

    def __init__(
        self,
        direction=config.BIGMOUSE_DIRECTION,
        lot=config.BIGMOUSE_LOT,
        tp_usd=config.BIGMOUSE_TP_USD,
        hedge_trigger_usd=config.BIGMOUSE_HEDGE_TRIGGER_USD,
        hedge_tp_usd=config.BIGMOUSE_HEDGE_TP_USD,
        hedge_lot_mult=config.BIGMOUSE_HEDGE_LOT_MULT,
        basket_tp_usd=config.BIGMOUSE_BASKET_TP_USD,
        martingale=config.BIGMOUSE_MARTINGALE,
        martingale_mult=config.BIGMOUSE_MARTINGALE_MULT,
        martingale_max_steps=config.BIGMOUSE_MARTINGALE_MAX_STEPS,
        magic=config.MAGIC_BIGMOUSE,
    ):
        super().__init__("BigMouse Hedging", magic=magic)
        self.direction = (direction or "BUY").upper()
        self.lot = lot
        self.tp_distance = tp_usd
        self.hedge_trigger_distance = hedge_trigger_usd
        self.hedge_tp_distance = hedge_tp_usd
        self.hedge_lot_mult = hedge_lot_mult
        self.basket_tp = basket_tp_usd
        self.martingale = martingale
        self.martingale_mult = martingale_mult
        self.martingale_max_steps = martingale_max_steps
        self.comment = config.BIGMOUSE_COMMENT
        self.history_bars = 200

    # ---- BaseStrategy (không dùng tín hiệu nến) ----
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        return df

    def check_signal(self, df: pd.DataFrame):
        return None

    def get_sl_tp(self, df, entry_price, digits, order_type):
        return None, None
