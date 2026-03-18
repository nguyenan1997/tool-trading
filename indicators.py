"""
indicators.py
Tính toán các chỉ báo kỹ thuật: EMA, SuperTrend.
"""

import pandas as pd
import numpy as np

def calculate_ema(df: pd.DataFrame, period: int) -> pd.DataFrame:
    """Thêm cột `ema{period}` vào df."""
    col = f"ema{period}"
    df[col] = df["close"].ewm(span=period, adjust=False).mean()
    return df

def _ema_smoothing(series: pd.Series, period: int) -> pd.Series:
    """Sử dụng EMA cho quá trình làm mượt ATR (nhanh hơn RMA)."""
    return series.ewm(span=period, adjust=False).mean()

def calculate_supertrend(df: pd.DataFrame, period: int, multiplier: float) -> pd.DataFrame:
    """Tính toán chỉ báo SuperTrend."""
    # HL2
    df["hl2"] = (df["high"] + df["low"]) / 2
    
    # ATR
    df["tr"] = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            abs(df["high"] - df["close"].shift(1)),
            abs(df["low"] - df["close"].shift(1))
        )
    )
    df["atr"] = _ema_smoothing(df["tr"], period)
    
    # Bands
    df["upper_band"] = df["hl2"] + (multiplier * df["atr"])
    df["lower_band"] = df["hl2"] - (multiplier * df["atr"])
    
    # SuperTrend
    st = [True] * len(df)
    final_upper = [0.0] * len(df)
    final_lower = [0.0] * len(df)
    
    for i in range(1, len(df)):
        # Final Upper
        if df["upper_band"][i] < final_upper[i-1] or df["close"][i-1] > final_upper[i-1]:
            final_upper[i] = df["upper_band"][i]
        else:
            final_upper[i] = final_upper[i-1]
            
        # Final Lower
        if df["lower_band"][i] > final_lower[i-1] or df["close"][i-1] < final_lower[i-1]:
            final_lower[i] = df["lower_band"][i]
        else:
            final_lower[i] = final_lower[i-1]
            
        # Trend
        if st[i-1]:
            st[i] = True if df["close"][i] > final_upper[i] else False
        else:
            st[i] = False if df["close"][i] < final_lower[i] else True
            
    df["st_trend"] = st
    df["st_val"] = [final_lower[i] if st[i] else final_upper[i] for i in range(len(df))]
    return df
