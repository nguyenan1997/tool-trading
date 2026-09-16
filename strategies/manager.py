"""
strategy_manager.py
Quản lý việc lựa chọn và truy xuất chiến lược.
"""

from .triple_ema import TripleEmaStrategy
from .trend_momentum import TrendMomentumStrategy

class StrategyManager:
    def __init__(self):
        self._strategies = {
            "3ema": TripleEmaStrategy(),
            "trend_momentum": TrendMomentumStrategy(),
        }
        self._current_key = "3ema" # Mặc định: giữ nguyên chiến lược cũ

    def set_strategy(self, key: str):
        if key in self._strategies:
            self._current_key = key
            return True
        return False

    def get_current_strategy(self):
        return self._strategies[self._current_key]
    
    def get_current_key(self):
        return self._current_key

    def get_all_strategies(self):
        return [{"id": k, "name": v.name, "magic": v.magic} for k, v in self._strategies.items()]

    def get_magics(self):
        return [v.magic for v in self._strategies.values()]

    def get_name_by_magic(self, magic: int):
        for v in self._strategies.values():
            if v.magic == magic:
                return v.name
        return None

strategy_manager = StrategyManager()
