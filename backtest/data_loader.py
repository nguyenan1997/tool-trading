"""
backtest/data_loader.py
Nạp dữ liệu lịch sử từ MT5 hoặc file CSV.
"""
import pandas as pd
import os
from core import mt5_handler as mt5h
import logging

logger = logging.getLogger(__name__)

DATA_DIR = "backtest/data"

def get_historical_data(symbol: str, timeframe: str, count: int = 1000, use_cache=True):
    """
    Lấy dữ liệu nến lịch sử. Nếu có cache CSV thì dùng, không thì tải từ MT5.
    """
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
        
    file_path = f"{DATA_DIR}/{symbol}_{timeframe}_{count}.csv"
    
    if use_cache and os.path.exists(file_path):
        print(f"Lấy dữ liệu từ CACHE: {file_path}")
        df = pd.read_csv(file_path, index_col="time", parse_dates=True)
        return df

    print(f"Đang tải dữ liệu MỚI từ MT5 cho {symbol} ({timeframe})...")
    if not mt5h.connect():
        return None
        
    try:
        df = mt5h.get_candles(symbol, timeframe, count)
        if df is not None:
            # Lưu cache
            df.to_csv(file_path, index=False)
            # Chuyển index về time để giống định dạng cache
            df.set_index("time", inplace=True)
            print(f"Đã lưu dữ liệu vào: {file_path}")
            return df
    finally:
        mt5h.disconnect()
        
    return None
