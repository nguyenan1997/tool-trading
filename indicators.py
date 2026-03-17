"""
indicators.py
Tính toán SuperTrend và EMA từ DataFrame OHLCV.
"""

import numpy as np
import pandas as pd


# ────────────────────────────────────────────────
#  EMA Smoothing for ATR
# ────────────────────────────────────────────────
def _ema_smoothing(series: pd.Series, period: int) -> pd.Series:
    """Sử dụng EMA chuẩn (Exponential Moving Average)."""
    return series.ewm(span=period, adjust=False).mean()


def _true_range(df: pd.DataFrame) -> pd.Series:
    high  = df["high"]
    low   = df["low"]
    prev_close = df["close"].shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low  - prev_close).abs()

    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


# ────────────────────────────────────────────────
#  SuperTrend
# ────────────────────────────────────────────────
def calculate_supertrend(
    df: pd.DataFrame,
    period: int = 10,
    multiplier: float = 3.0
) -> pd.DataFrame:
    """
    Thêm các cột vào df:
        supertrend  – giá trị đường ST (điểm SL)
        st_dir      – 1 = bullish (xanh), -1 = bearish (đỏ)
        st_upper    – upper band
        st_lower    – lower band

    Trả về df (đã modify inplace).
    """
    close = df["close"]
    hl2   = (df["high"] + df["low"]) / 2

    tr  = _true_range(df)
    atr = _ema_smoothing(tr, period)

    # Basic bands
    raw_upper = hl2 + multiplier * atr
    raw_lower = hl2 - multiplier * atr

    upper  = raw_upper.copy()
    lower  = raw_lower.copy()
    st     = pd.Series(np.nan, index=df.index)
    st_dir = pd.Series(0,      index=df.index)

    # Khởi tạo row đầu tiên có đủ ATR
    start = period - 1
    upper.iloc[start] = raw_upper.iloc[start]
    lower.iloc[start] = raw_lower.iloc[start]
    st_dir.iloc[start] = 1
    st.iloc[start]     = lower.iloc[start]

    for i in range(start + 1, len(df)):
        # ----- Final upper band -----
        if raw_upper.iloc[i] < upper.iloc[i - 1] or close.iloc[i - 1] > upper.iloc[i - 1]:
            upper.iloc[i] = raw_upper.iloc[i]
        else:
            upper.iloc[i] = upper.iloc[i - 1]

        # ----- Final lower band -----
        if raw_lower.iloc[i] > lower.iloc[i - 1] or close.iloc[i - 1] < lower.iloc[i - 1]:
            lower.iloc[i] = raw_lower.iloc[i]
        else:
            lower.iloc[i] = lower.iloc[i - 1]

        # ----- Direction & SuperTrend value -----
        prev_st = st.iloc[i - 1]

        if prev_st == upper.iloc[i - 1]:           # trước đó đang Bear
            if close.iloc[i] > upper.iloc[i]:       # flip → Bull
                st_dir.iloc[i] = 1
                st.iloc[i]     = lower.iloc[i]
            else:
                st_dir.iloc[i] = -1
                st.iloc[i]     = upper.iloc[i]
        else:                                       # trước đó đang Bull
            if close.iloc[i] < lower.iloc[i]:       # flip → Bear
                st_dir.iloc[i] = -1
                st.iloc[i]     = upper.iloc[i]
            else:
                st_dir.iloc[i] = 1
                st.iloc[i]     = lower.iloc[i]

    df["st_upper"] = upper
    df["st_lower"] = lower
    df["supertrend"] = st
    df["st_dir"]     = st_dir

    return df


# ────────────────────────────────────────────────
#  EMA
# ────────────────────────────────────────────────
def calculate_ema(df: pd.DataFrame, period: int = 100) -> pd.DataFrame:
    """Thêm cột `ema{period}` vào df."""
    col = f"ema{period}"
    df[col] = df["close"].ewm(span=period, adjust=False).mean()
    return df
