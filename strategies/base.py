"""
strategies/base.py
Định nghĩa cấu trúc cơ bản cho các chiến lược giao dịch.
"""
from abc import ABC, abstractmethod
import pandas as pd

class BaseStrategy(ABC):
    def __init__(self, name="Base"):
        self.name = name

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
    def get_sl_tp(self, df: pd.DataFrame, entry_price: float, digits: int):
        """Tính toán SL và TP cho lệnh."""
        pass
