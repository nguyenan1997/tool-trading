"""
indicators.py
Tính toán các chỉ báo kỹ thuật: EMA.
"""

import pandas as pd
import numpy as np

def calculate_ema(df: pd.DataFrame, period: int) -> pd.DataFrame:
    """Thêm cột `ema{period}` vào df."""
    col = f"ema{period}"
    df[col] = df["close"].ewm(span=period, adjust=False).mean()
    return df
