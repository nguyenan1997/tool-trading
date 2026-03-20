"""
backtest/data_loader.py
Nạp dữ liệu lịch sử từ MT5 hoặc file CSV.
"""
import pandas as pd
import os
from core import mt5_handler as mt5h
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

DATA_DIR = "backtest/data"

def get_historical_data(symbol: str, timeframe: str, count: int = 1000, start_date: str = None, use_cache=True):
    """
    Lấy dữ liệu nến lịch sử. 
    - Nếu start_date được cung cấp (YYYY-MM-DD), lấy từ ngày đó đến nay.
    - Ngược lại lấy theo count (số nến gần nhất).
    """
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
        
    # Tạo tên file cache riêng biệt
    if start_date:
        cache_name = f"{symbol}_{timeframe}_from_{start_date}.csv"
    else:
        cache_name = f"{symbol}_{timeframe}_{count}.csv"
        
    file_path = os.path.join(DATA_DIR, cache_name)
    
    if use_cache and os.path.exists(file_path):
        print(f"Lấy dữ liệu từ CACHE: {file_path}")
        df = pd.read_csv(file_path, index_col="time", parse_dates=True)
        return df

    print(f"Đang tải dữ liệu MỚI từ MT5 cho {symbol} ({timeframe})...")
    if not mt5h.connect():
        return None
        
    try:
        if start_date:
            # Chuyển string thành datetime object
            date_from = datetime.strptime(start_date, "%Y-%m-%d")
            date_to = datetime.now()
            df = mt5h.get_candles_range(symbol, timeframe, date_from, date_to)
        else:
            df = mt5h.get_candles(symbol, timeframe, count)

        if df is not None:
            # Lưu cache
            df.to_csv(file_path, index=False)
            # Chuyển index về time
            df.set_index("time", inplace=True)
            print(f"Đã lưu dữ liệu vào: {file_path}")
            return df
    except Exception as e:
        logger.error(f"Error loading historical data: {e}")
    finally:
        mt5h.disconnect()
        
    return None
