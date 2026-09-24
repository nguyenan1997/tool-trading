"""
strategies/base.py
Định nghĩa cấu trúc cơ bản cho các chiến lược giao dịch.
"""
from abc import ABC, abstractmethod
import pandas as pd

class BaseStrategy(ABC):
    # Số nến đầu bỏ qua khi back-test để chỉ báo hội tụ (warmup).
    # Các chiến lược nên ghi đè giá trị này cho phù hợp (xem config.*_WARMUP_BARS).
    warmup_bars = 100

    def __init__(self, name="Base", magic=0):
        self.name = name
        self.magic = magic

    @abstractmethod
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Tính toán các chỉ báo kỹ thuật cho nến."""
        pass

    @abstractmethod
    def check_signal(self, df: pd.DataFrame) -> str | None: # type: ignore
        """
        Kiểm tra tín hiệu giao dịch.
        Trả về "BUY", "SELL", hoặc None.
        """
        return None

    @abstractmethod
    def get_sl_tp(self, df: pd.DataFrame, entry_price: float, digits: int, order_type: str):
        """Tính toán SL và TP cho lệnh."""
        pass

    def get_pending_setup(self, df: pd.DataFrame):
        """
        (Tùy chọn) Trả về lệnh chờ limit nếu chiến lược dùng entry hồi giá.
        Dict: {"type": "BUY"/"SELL", "level": float, "sl": float, "tp": float, "wait_min": int}
        Trả về None nếu không có setup. Mặc định: không dùng.
        """
        return None
