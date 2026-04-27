"""
strategy_manager.py
Quản lý việc lựa chọn và truy xuất chiến lược.
"""

from .triple_ema import TripleEmaStrategy

class StrategyManager:
    def __init__(self):
        self._strategies = {
            "3ema": TripleEmaStrategy()
        }
        self._current_key = "3ema" # Mặc định dùng chiến lược mới

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
        return [{"id": k, "name": v.name} for k, v in self._strategies.items()]

strategy_manager = StrategyManager()
