"""
strategies/triple_ema.py
Chiến lược 3 EMA (9, 21, 50).
"""
import config
from indicators import calculate_ema
import pandas as pd
from .base import BaseStrategy

class TripleEmaStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("3 EMA Crossover")
        self.fast    = config.EMA_FAST
        self.medium  = config.EMA_MEDIUM
        self.slow    = config.EMA_SLOW
        self.rr      = config.RR_RATIO

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = calculate_ema(df, self.fast)
        df = calculate_ema(df, self.medium)
        df = calculate_ema(df, self.slow)
        return df

    def check_signal(self, df: pd.DataFrame) -> str | None:
        if len(df) < self.slow + 2: return None

        sig_candle  = df.iloc[-2]
        prev_candle = df.iloc[-3]

        ema9_curr  = sig_candle[f"ema{self.fast}"]
        ema21_curr = sig_candle[f"ema{self.medium}"]
        ema50_curr = sig_candle[f"ema{self.slow}"]

        ema9_prev  = prev_candle[f"ema{self.fast}"]
        ema21_prev = prev_candle[f"ema{self.medium}"]

        price_close = sig_candle["close"]

        # 🟢 BUY
        cross_up = (ema9_prev <= ema21_prev) and (ema9_curr > ema21_curr)
        if cross_up and price_close > ema9_curr and price_close > ema21_curr and price_close > ema50_curr:
            return "BUY"

        # 🔴 SELL
        cross_down = (ema9_prev >= ema21_prev) and (ema9_curr < ema21_curr)
        if cross_down and price_close < ema9_curr and price_close < ema21_curr and price_close < ema50_curr:
            return "SELL"

        return None

    def get_sl_tp(self, df: pd.DataFrame, entry_price: float, digits: int, order_type: str):
        sig_candle = df.iloc[-2]
        ema21_curr = sig_candle[f"ema{self.medium}"]
        
        sl = round(float(ema21_curr), int(digits))
        
        if order_type == "BUY":
            dist = entry_price - sl
            if dist <= 0: return None, None
            tp = round(float(entry_price + (dist * self.rr)), int(digits))
        else: # SELL
            dist = sl - entry_price
            if dist <= 0: return None, None
            tp = round(float(entry_price - (dist * self.rr)), int(digits))
            
        return sl, tp
