"""
strategy_manager.py
Quản lý việc lựa chọn và truy xuất chiến lược.
"""

from .trend_momentum import TrendMomentumStrategy
from .asian_sweep import AsianSweepStrategy
from .smc import SMCSweepChochStrategy
from .hedging import HedgingStrategy

class StrategyManager:
    def __init__(self):
        self._strategies = {
            "trend_momentum": TrendMomentumStrategy(),
            "asian_sweep": AsianSweepStrategy(),
            "smc": SMCSweepChochStrategy(),
            "hedging": HedgingStrategy(),
        }
        self._current_key = "trend_momentum" # Mặc định khi khởi động
        # Chọn PP nào thì CHỈ chạy PP đó (loại trừ nhau).
        self._enabled = {k: (k == self._current_key) for k in self._strategies}

    def get_active_strategies(self):
        """Chỉ trả về chiến lược đang được chọn trên UI."""
        if self._enabled.get(self._current_key, False):
            return [self._strategies[self._current_key]]
        return []

    def set_strategy(self, key: str):
        if key in self._strategies:
            self._current_key = key
            # Bật đúng PP vừa chọn, tắt các PP còn lại.
            for k in self._enabled:
                self._enabled[k] = (k == key)
            return True
        return False

    def get_current_strategy(self):
        return self._strategies[self._current_key]
    
    def get_current_key(self):
        return self._current_key

    def get_all_strategies(self):
        return [{"id": k, "name": v.name, "magic": v.magic} for k, v in self._strategies.items()]

    def get_all_strategy_objects(self):
        return list(self._strategies.values())

    def get_magics(self):
        return [v.magic for v in self._strategies.values()]

    def get_name_by_magic(self, magic: int):
        for v in self._strategies.values():
            if v.magic == magic:
                return v.name
        return None

strategy_manager = StrategyManager()
