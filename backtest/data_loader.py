"""
backtest/data_loader.py
Nạp dữ liệu lịch sử từ MT5 hoặc file CSV.
"""
import pandas as pd
import os
from pathlib import Path
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
        
    # Tên file cache chính xác
    if start_date:
        cache_name = f"{symbol}_{timeframe}_from_{start_date}.csv"
    else:
        cache_name = f"{symbol}_{timeframe}_{count}.csv"
        
    file_path = os.path.join(DATA_DIR, cache_name)
    
    if use_cache and os.path.exists(file_path):
        print(f"Lấy dữ liệu từ CACHE: {file_path}")
        df = pd.read_csv(file_path, index_col="time", parse_dates=True)
        return df

    # Nếu file cache chính xác chưa có -> tìm file cache lớn hơn đủ rows
    csvs = sorted(Path(DATA_DIR).glob(f"{symbol}_{timeframe}_*.csv"),
                  key=lambda p: p.stat().st_size, reverse=True)
    for p in csvs:
        if str(p) == file_path or not p.is_file():
            continue
        try:
            df = pd.read_csv(p, index_col="time", parse_dates=True)
            if len(df) >= count:
                df = df.iloc[-count:].copy()
                print(f"Dùng cache lớn hơn ({p.name}, {len(df)} nến) thay cho {count}")
                df.to_csv(file_path, index=False)
                return df
        except Exception:
            continue

    # Tải mới từ MT5
    print(f"Đang tải dữ liệu MỚI từ MT5 cho {symbol} ({timeframe})...")
    if not mt5h.connect():
        return None
        
    try:
        if start_date:
            date_from = datetime.strptime(start_date, "%Y-%m-%d")
            date_to = datetime.now()
            df = mt5h.get_candles_range(symbol, timeframe, date_from, date_to)
        else:
            df = mt5h.get_candles(symbol, timeframe, count)

        if df is not None:
            df.to_csv(file_path, index=False)
            df.set_index("time", inplace=True)
            print(f"Đã lưu dữ liệu vào: {file_path}")
            return df
    except Exception as e:
        logger.error(f"Error loading historical data: {e}")
    finally:
        mt5h.disconnect()
        
    return None
