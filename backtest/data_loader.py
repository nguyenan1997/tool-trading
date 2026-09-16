"""
backtest/data_loader.py
Nạp dữ liệu lịch sử từ MT5 hoặc file CSV.
Cache được tự động làm mới khi dữ liệu đã cũ (nến cuối cách hiện tại quá lâu).
"""
import pandas as pd
import os
from pathlib import Path
from datetime import datetime, timezone
from core import mt5_handler as mt5h
import config
import logging

logger = logging.getLogger(__name__)

DATA_DIR = "backtest/data"


def _cache_is_fresh(df: pd.DataFrame, symbol: str, max_age_hours: float) -> bool:
    """Cache còn 'tươi' nếu nến cuối cách giờ broker hiện tại <= max_age_hours.
    Nếu không lấy được giờ broker (chưa kết nối MT5) thì coi như dùng được."""
    if df is None or len(df) == 0:
        return False
    try:
        last = pd.Timestamp(df.index[-1])
    except Exception:
        return False
    try:
        tick = mt5h.get_tick(symbol)
        if tick is None:
            return True
        broker_now = pd.Timestamp(
            datetime.fromtimestamp(tick.time, timezone.utc).replace(tzinfo=None)
        )
    except Exception:
        return True
    return (broker_now - last).total_seconds() / 3600.0 <= max_age_hours


def get_historical_data(symbol: str, timeframe: str, count: int = 1000,
                        start_date: str = None, use_cache: bool = True,
                        max_age_hours: float = None):
    """
    Lấy dữ liệu nến lịch sử.
    - Nếu start_date được cung cấp (YYYY-MM-DD), lấy từ ngày đó đến nay.
    - Ngược lại lấy theo count (số nến gần nhất).
    - Cache cũ hơn `max_age_hours` (mặc định config.BACKTEST_CACHE_HOURS) sẽ bị tải lại.
    """
    if max_age_hours is None:
        max_age_hours = getattr(config, "BACKTEST_CACHE_HOURS", 0.1)

    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)

    # Tên file cache chính xác
    if start_date:
        cache_name = f"{symbol}_{timeframe}_from_{start_date}.csv"
    else:
        cache_name = f"{symbol}_{timeframe}_{count}.csv"
    file_path = os.path.join(DATA_DIR, cache_name)

    if use_cache and os.path.exists(file_path):
        try:
            df = pd.read_csv(file_path, index_col="time", parse_dates=True)
            if _cache_is_fresh(df, symbol, max_age_hours):
                print(f"Lấy dữ liệu từ CACHE: {file_path}")
                return df
            print(f"Cache CŨ (nến cuối {df.index[-1]}) -> tải lại từ MT5")
        except Exception as e:
            logger.warning(f"Cache lỗi định dạng, bỏ qua để tạo lại: {file_path} ({e})")

    # File cache chính xác chưa có/cũ -> tìm file cache lớn hơn đủ rows VÀ còn tươi
    # (chỉ áp dụng cho chế độ theo `count`, không áp dụng cho chế độ theo `start_date`)
    if not start_date:
        csvs = sorted(Path(DATA_DIR).glob(f"{symbol}_{timeframe}_*.csv"),
                      key=lambda p: p.stat().st_size, reverse=True)
        for p in csvs:
            if str(p) == file_path or not p.is_file():
                continue
            try:
                df = pd.read_csv(p, index_col="time", parse_dates=True)
                if len(df) >= count and _cache_is_fresh(df, symbol, max_age_hours):
                    df = df.iloc[-count:].copy()
                    print(f"Dùng cache lớn hơn ({p.name}, {len(df)} nến) thay cho {count}")
                    # Giữ index 'time' khi ghi để lần sau đọc lại được
                    df.to_csv(file_path)
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

    # Không gọi mt5h.disconnect() ở đây: MT5 dùng chung với bot đang chạy,
    # shutdown sẽ ngắt kết nối của bot. Cứ để MT5 kết nối sẵn.
    return None
