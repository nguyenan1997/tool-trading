"""
strategy_manager.py
Quản lý việc lựa chọn và truy xuất chiến lược.
"""

import config
from .trend_momentum import TrendMomentumStrategy
from .asian_sweep import AsianSweepStrategy

class StrategyManager:
    def __init__(self):
        self._strategies = {
            "trend_momentum": TrendMomentumStrategy(),
            "asian_sweep": AsianSweepStrategy(),
        }
        # Hai hệ thống độc lập, chạy song song; asian_sweep có thể tắt qua config.
        self._enabled = {"trend_momentum": True, "asian_sweep": bool(config.AS_ENABLED)}
        self._current_key = "trend_momentum" # Mặc định khi khởi động

    def get_active_strategies(self):
        """Danh sách các chiến lược đang bật (bot chạy tất cả, mỗi cái magic riêng)."""
        return [v for k, v in self._strategies.items() if self._enabled.get(k, False)]

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
