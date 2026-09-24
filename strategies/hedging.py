"""
strategies/hedging.py
HỆ THỐNG 4 — Hedging Grid (XAUUSD). Chạy liên tục 24/7, không phụ thuộc khung nến.

Luật (do HedgingEngine thực thi, không qua check_signal):
  - Mở đồng thời 1 BUY + 1 SELL; mỗi lệnh TP = HEDGE_TP_USD so với giá vào.
  - Giá chạm TP lệnh nào → đóng lệnh đó (lệnh đối diện tiếp tục gồng),
    đồng thời mở 1 cặp BUY+SELL mới tại giá hiện tại.
  - Không SL.

Class này chỉ mang tham số + đánh dấu `is_hedging` để BotEngine chạy
đúng vòng lặp hedging. Các hàm của BaseStrategy được hiện thực rỗng.
"""
import pandas as pd

import config
from .base import BaseStrategy


class HedgingStrategy(BaseStrategy):
    is_hedging = True   # BotEngine nhận biết để chạy vòng lặp riêng
    no_session = True   # Không giới hạn phiên (24/7)

    def __init__(
        self,
        tp_usd=config.HEDGE_TP_USD,
        lot=config.HEDGE_LOT,
        magic=config.MAGIC_HEDGE,
    ):
        super().__init__("Hedging Grid", magic=magic)
        self.tp_distance = tp_usd
        self.lot = lot
        self.comment = config.HEDGE_COMMENT
        self.history_bars = 200

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        # Hedging không cần chỉ báo — chỉ quản lý vị thế theo giá.
        return df

    def check_signal(self, df: pd.DataFrame):
        # Đặt lệnh do HedgingEngine quản lý, không dùng tín hiệu market.
        return None

    def get_sl_tp(self, df, entry_price, digits, order_type):
        return None, None
