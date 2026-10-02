"""
core/basket_dca_engine.py
Engine cho "Dynamic Basket DCA" (port từ EA Dynamic_Basket_DCA_V1).

Vòng lặp (gọi mỗi BASKET_POLL_SEC khi chiến lược "basket_dca" đang chọn):
  1. Không có vị thế, không có lệnh chờ → đặt straddle BUY STOP + SELL STOP.
  2. Đang có vị thế → set TP động (bình quân ± TP) cho cả rổ; nếu giá đi ngược
     BASKET_STEP so với lệnh cùng chiều gần nhất → nhồi thêm, lot Fibonacci.
  3. Bên stop còn lại (chưa khớp) bị HỦY khi bên kia đã vào.
  4. Rổ đóng (broker khớp TP) → về trạng thái flat → mở chu kỳ mới.

KHÔNG Stop Loss. ⚠️ Martingale → rủi ro cháy. Chỉ demo.
"""
import logging
import threading

import config
from . import mt5_handler as mt5h

logger = logging.getLogger(__name__)

FIB = [1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610, 987, 1597, 2584, 4181]


def fib_lot(level: int, lot0: float, vol_step: float, vol_min: float, vol_max: float) -> float:
    f = FIB[level] if level < len(FIB) else FIB[-1] * (2 ** (level - len(FIB) + 1))
    lot = f * lot0
    step = vol_step or 0.01
    lot = round(round(lot / step) * step, 2)
    return max(vol_min, min(lot, vol_max))


class BasketDCAEngine:
    def __init__(self):
        self._lock = threading.RLock()
        self._magic = None
        self._ref_mid = None      # gia tham chieu khi dat straddle (reposition)
        self._last_target = None

    def reset(self):
        with self._lock:
            self._magic = None
            self._ref_mid = None
            self._last_target = None

    # ------------------------------------------------------------------
    def process(self, strategy):
        magic = getattr(strategy, "magic", config.MAGIC_BASKET)
        if not mt5h.connect():
            return
        if not mt5h.is_market_open(config.SYMBOL):
            return
        with self._lock:
            if self._magic != magic:
                self._magic = magic
                self._ref_mid = None
                self._last_target = None

            positions = mt5h.get_open_positions(config.SYMBOL, [magic])
            pendings = mt5h.get_pending_orders(config.SYMBOL, magic)

            # --- Có rổ ---
            if positions:
                if pendings:
                    for o in pendings:
                        mt5h.cancel_pending_order(o.ticket)
                self._set_basket_tp(strategy, positions)
                self._maybe_add_dca(strategy, positions)
                return

            # --- Chưa có rổ, có lệnh chờ (straddle) ---
            if pendings:
                # Reposition CHỈ khi giá chạy >= BASKET_MOVE_FRAMEWORK (giống EA gốc)
                if getattr(config, "BASKET_REPOSITION", True) and self._ref_mid is not None:
                    tick = mt5h.get_tick(config.SYMBOL)
                    if tick is not None:
                        mid = (tick.ask + tick.bid) / 2.0
                        if abs(mid - self._ref_mid) >= float(getattr(config, "BASKET_MOVE_FRAMEWORK", 0.5)):
                            for o in pendings:
                                mt5h.cancel_pending_order(o.ticket)
                            self._place_straddle(strategy)
                return

            # --- Flat -> mở straddle mới ---
            self._last_target = None
            self._place_straddle(strategy)

    # ------------------------------------------------------------------
    def _place_straddle(self, strategy):
        info = mt5h.get_symbol_info(config.SYMBOL)
        tick = mt5h.get_tick(config.SYMBOL)
        if info is None or tick is None:
            return
        d = info.digits
        magic = getattr(strategy, "magic", config.MAGIC_BASKET)
        comment = getattr(strategy, "comment", config.BASKET_COMMENT)
        dist = float(getattr(strategy, "init_dist", config.BASKET_INIT_DIST))
        lot = float(getattr(strategy, "lot", config.BASKET_LOT0))
        mid = (tick.ask + tick.bid) / 2.0
        min_gap = (info.trade_stops_level or 0) * info.point
        buy_price = round(max(mid + dist, tick.ask + min_gap), d)
        sell_price = round(min(mid - dist, tick.bid - min_gap), d)
        dev = int(getattr(config, "BASKET_DEVIATION_PTS", 30))
        b = mt5h.place_stop_order(config.SYMBOL, "BUY", lot, buy_price, magic,
                                  f"{comment}_BSTOP", deviation=dev)
        s = mt5h.place_stop_order(config.SYMBOL, "SELL", lot, sell_price, magic,
                                  f"{comment}_SSTOP", deviation=dev)
        if b or s:
            self._ref_mid = mid
            logger.info(f"[BASKET] Straddle BUY STOP {buy_price:.{d}f} / SELL STOP {sell_price:.{d}f} | lot {lot}")

    # ------------------------------------------------------------------
    def _set_basket_tp(self, strategy, positions):
        info = mt5h.get_symbol_info(config.SYMBOL)
        if info is None:
            return
        d = info.digits
        total = sum(p.volume for p in positions)
        if total <= 0:
            return
        wavg = sum(p.price_open * p.volume for p in positions) / total
        side = positions[0].type
        contract = info.trade_contract_size or 100.0
        profit_usd = float(getattr(strategy, "profit_target", config.BASKET_PROFIT_USD))
        off = profit_usd / (total * contract) if total > 0 else 0.0
        target = round(wavg + off if side == 0 else wavg - off, d)
        if self._last_target is not None and abs(target - self._last_target) < 1e-9:
            return
        changed = 0
        for p in positions:
            if abs((p.tp or 0.0) - target) < 1e-9:
                continue
            if mt5h.modify_position(config.SYMBOL, p.ticket, sl=0.0, tp=target):
                changed += 1
        self._last_target = target
        logger.info(f"[BASKET] TP rổ = {target:.{d}f} (wavg {wavg:.{d}f}, {len(positions)} lệnh, {total:.2f} lot) đổi {changed}")

    # ------------------------------------------------------------------
    def _maybe_add_dca(self, strategy, positions):
        info = mt5h.get_symbol_info(config.SYMBOL)
        tick = mt5h.get_tick(config.SYMBOL)
        if info is None or tick is None:
            return
        side = positions[0].type  # 0 BUY, 1 SELL
        step = float(getattr(strategy, "step", config.BASKET_STEP))
        max_levels = int(getattr(strategy, "max_levels", config.BASKET_MAX_LEVELS) or 0)
        max_total = float(getattr(strategy, "max_total_lot", config.BASKET_MAX_TOTAL_LOT) or 0.0)
        if max_levels > 0 and len(positions) >= max_levels:
            return
        total = sum(p.volume for p in positions)
        if side == 0:
            last = min(p.price_open for p in positions)
            if tick.bid > last - step:
                return
        else:
            last = max(p.price_open for p in positions)
            if tick.ask < last + step:
                return

        lot = fib_lot(len(positions), float(getattr(strategy, "lot", config.BASKET_LOT0)),
                      info.volume_step, info.volume_min, info.volume_max)
        if max_total > 0 and total + lot > max_total:
            logger.warning(f"[BASKET] BỎ DCA: tổng lot vượt giới hạn {max_total}")
            return
        acct = mt5h.get_account_info()
        if acct is not None:
            m_need = mt5h.get_margin_required(config.SYMBOL, lot, "BUY")
            if m_need is not None and acct.margin_free < m_need:
                logger.warning(f"[BASKET] BỎ DCA: không đủ margin (cần {m_need:.2f}, free {acct.margin_free:.2f})")
                return
        magic = getattr(strategy, "magic", config.MAGIC_BASKET)
        comment = getattr(strategy, "comment", config.BASKET_COMMENT)
        dev = int(getattr(config, "BASKET_DEVIATION_PTS", 30))
        typ = "BUY" if side == 0 else "SELL"
        if mt5h.open_position(config.SYMBOL, typ, lot, 0.0, 0.0, magic,
                              f"{comment}_DCA", max_slippage_points=0, deviation=dev):
            self._last_target = None   # buộc tính lại TP
            logger.info(f"[BASKET] DCA #{len(positions)+1} {typ} | lot {lot}")

    # ------------------------------------------------------------------
    def close_all(self, strategy, reason="close all"):
        magic = getattr(strategy, "magic", config.MAGIC_BASKET)
        for o in mt5h.get_pending_orders(config.SYMBOL, magic):
            mt5h.cancel_pending_order(o.ticket)
        for p in mt5h.get_open_positions(config.SYMBOL, [magic]):
            mt5h.close_position(p, magic, reason)
        self.reset()


basket_dca_engine = BasketDCAEngine()
