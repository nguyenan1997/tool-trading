"""
strategies/st_ema.py
Chiến lược SuperTrend + EMA 100.
"""
import config
from indicators import calculate_supertrend, calculate_ema
import pandas as pd
from .base import BaseStrategy

class STEmaStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("SuperTrend + EMA 100")
        self.period     = 10     # Mặc định cũ
        self.multiplier = 3.0    # Mặc định cũ
        self.ema_filter = 100

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = calculate_ema(df, self.ema_filter)
        df = calculate_supertrend(df, self.period, self.multiplier)
        return df

    def check_signal(self, df: pd.DataFrame) -> str | None:
        if len(df) < self.ema_filter + 2: return None

        sig_candle  = df.iloc[-2]
        prev_candle = df.iloc[-3]
        
        # Tín hiệu Flip: prev_trend != curr_trend
        flip_up = (not prev_candle["st_trend"]) and sig_candle["st_trend"]
        flip_down = prev_candle["st_trend"] and (not sig_candle["st_trend"])

        price_close = sig_candle["close"]
        ema_val     = sig_candle[f"ema{self.ema_filter}"]

        # 🟢 BUY: Flip UP + Close > EMA 100
        if flip_up and price_close > ema_val:
            return "BUY"

        # 🔴 SELL: Flip DOWN + Close < EMA 100
        if flip_down and price_close < ema_val:
            return "SELL"

        return None

    def get_sl_tp(self, df: pd.DataFrame, entry_price: float, digits: int, order_type: str):
        sig_candle = df.iloc[-2]
        st_val = sig_candle["st_val"] # Giá trị SuperTrend làm SL
        
        sl = round(st_val, digits)
        
        if order_type == "BUY":
            dist = entry_price - sl
            if dist <= 0: return None, None
            # TP mặc định cũ (ví dụ dùng 2:1 RR hoặc tương đương)
            tp = round(entry_price + (dist * 2.0), digits)
        else: # SELL
            dist = sl - entry_price
            if dist <= 0: return None, None
            tp = round(entry_price - (dist * 2.0), digits)
            
        return sl, tp
