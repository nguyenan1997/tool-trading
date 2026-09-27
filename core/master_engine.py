"""
core/master_engine.py
Engine cho PP MASTER_XAU_TP (XAUUSD), gọi định kỳ từ BotEngine.

Luật:
  1. Init: mở BUY + SELL market (hedge), lot MASTER_LOT_START.
  2. Grid động: duy trì 1 Buy Stop phía trên & 1 Sell Stop phía dưới (cách
     MASTER_GRID_STEP), dời theo giá; tối đa MASTER_MAX_LEVEL lệnh mỗi bên.
  3. Martingale: lot = MASTER_LOT_START × MASTER_MART^(số lệnh đang mở), cap MASTER_MAX_LOT.
  4. Trailing từng lệnh: lãi ≥ MASTER_TRAIL_START (giá) → dời SL khóa lãi.
  5. TP tổng: basket ≥ MASTER_TP_USD hoặc ≥ MASTER_TP_PCT% số dư → đóng ALL + xóa pending.
  6. Trailing TP tổng: basket ≥ MASTER_TRAIL_TP_START → khóa đỉnh; tụt MASTER_TRAIL_TP_STEP → đóng ALL.
  7. 1 lệnh chạm SL → xóa ALL pending, reset lưới, dựng lại từ giá hiện tại.
  8. Theo dõi max DD tháng (log; KHÔNG tự đóng).
"""
import logging
import math
import threading
import time
from datetime import datetime, timezone

import config
from . import mt5_handler as mt5h

logger = logging.getLogger(__name__)


class MasterEngine:
    def __init__(self):
        self._lock = threading.RLock()
        self._magic = None
        self._known = set()          # ticket các vị thế đang theo dõi
        self._peak = None            # đỉnh basket profit cho trailing TP tổng
        self._closing_all = False
        self._month_key = None
        self._dd_peak = 0.0
        self._dd_max = 0.0

    def reset(self):
        with self._lock:
            self._magic = None
            self._known = set()
            self._peak = None
            self._closing_all = False

    # ------------------------------------------------------------------
    def process(self, strategy):
        magic = getattr(strategy, "magic", config.MAGIC_MASTER)
        if not mt5h.connect():
            logger.error("[MASTER] Không kết nối được MT5")
            return
        with self._lock:
            if self._magic != magic:
                self._magic = magic
                self._known = set()
                self._peak = None
                self._closing_all = False

            positions = mt5h.get_open_positions(config.SYMBOL, [magic])
            pendings = mt5h.get_pending_orders(config.SYMBOL, magic)

            # --- Phẳng → bắt đầu chu kỳ ---
            if not positions and not pendings:
                self._start_cycle(strategy)
                return

            current = {p.ticket for p in positions}

            # --- Phát hiện 1 lệnh bị SL (ticket biến mất, không phải do ta đóng) ---
            gone = self._known - current
            if gone and not self._closing_all:
                logger.warning(
                    f"[MASTER] ⚠️ {len(gone)} lệnh biến mất (SL?) → xóa pending, reset lưới"
                )
                self._cancel_all_pending(magic)
                positions = mt5h.get_open_positions(config.SYMBOL, [magic])
                current = {p.ticket for p in positions}
                self._peak = None
                self._place_ladder(strategy)   # dựng lại dải từ giá hiện tại (mục 7)

            basket = sum(p.profit for p in positions)
            acct = mt5h.get_account_info()
            balance = acct.balance if acct is not None else 0.0

            # --- TP tổng ---
            tp_usd = float(getattr(strategy, "tp_usd", config.MASTER_TP_USD) or 0)
            tp_pct = float(getattr(strategy, "tp_pct", config.MASTER_TP_PCT) or 0)
            if (tp_usd > 0 and basket >= tp_usd) or \
               (tp_pct > 0 and balance > 0 and basket >= tp_pct / 100.0 * balance):
                self._close_all(strategy, f"TP tổng (basket={basket:.2f}$)")
                return

            # --- Trailing TP tổng ---
            ts = float(getattr(strategy, "trail_tp_start", config.MASTER_TRAIL_TP_START) or 0)
            tstep = float(getattr(strategy, "trail_tp_step", config.MASTER_TRAIL_TP_STEP) or 0)
            if ts > 0 and basket >= ts:
                self._peak = basket if self._peak is None else max(self._peak, basket)
                if basket <= self._peak - tstep:
                    self._close_all(
                        strategy,
                        f"trailing TP tổng (basket {basket:.2f} tụt khỏi đỉnh {self._peak:.2f})",
                    )
                    return

            # --- Trailing SL từng lệnh ---
            self._trail_orders(strategy, positions)

            # --- Theo dõi DD tháng ---
            self._monthly_dd(acct)

            self._known = {p.ticket for p in positions}

    # ------------------------------------------------------------------
    def _start_cycle(self, strategy):
        self._peak = None
        if getattr(strategy, "init_hedge", True):
            lot = float(getattr(strategy, "lot_start", config.MASTER_LOT_START))
            ok_b = self._open_market(strategy, "BUY", lot)
            ok_s = self._open_market(strategy, "SELL", lot)
            logger.info(f"[MASTER] 🏁 Chu kỳ mới: hedge BUY={bool(ok_b)} SELL={bool(ok_s)} lot={lot}")
        self._place_ladder(strategy)
        self._known = {p.ticket for p in mt5h.get_open_positions(config.SYMBOL, [self._magic])}

    # ------------------------------------------------------------------
    def _open_market(self, strategy, typ, lot):
        info = mt5h.get_symbol_info(config.SYMBOL)
        tick = mt5h.get_tick(config.SYMBOL)
        if info is None or tick is None:
            return None
        d = info.digits
        init_sl = float(getattr(strategy, "init_sl", config.MASTER_INIT_SL) or 0)
        sl = 0.0
        if init_sl > 0:
            sl = round(tick.ask - init_sl if typ == "BUY" else tick.bid + init_sl, d)
        return mt5h.open_position(
            config.SYMBOL, typ, lot, sl, 0.0,
            getattr(strategy, "magic", config.MAGIC_MASTER),
            getattr(strategy, "comment", config.MASTER_COMMENT),
        )

    # ------------------------------------------------------------------
    def _lot_for_level(self, strategy, n):
        """Lot cho cấp grid thứ n (1..max_level): LOT_START + LOT_INC×(n−1), cap MAX_LOT."""
        base = float(getattr(strategy, "lot_start", config.MASTER_LOT_START))
        inc = float(getattr(strategy, "lot_inc", config.MASTER_LOT_INC))
        max_lot = float(getattr(strategy, "max_lot", config.MASTER_MAX_LOT))
        info = mt5h.get_symbol_info(config.SYMBOL)
        step = getattr(info, "volume_step", 0.01) if info is not None else 0.01
        vmin = getattr(info, "volume_min", 0.01) if info is not None else 0.01
        step = step or 0.01
        raw = base + inc * max(0, n - 1)
        raw = min(raw, max_lot)
        lot = math.floor(raw / step + 1e-9) * step
        lot = max(lot, vmin)
        lot = min(lot, max_lot)
        return round(lot, 2)

    # ------------------------------------------------------------------
    def _place_ladder(self, strategy):
        """Rải cả dải: max_level Buy Stop phía trên + max_level Sell Stop phía dưới
        giá hiện tại, cách nhau grid_step. Lot cộng tiến theo cấp."""
        info = mt5h.get_symbol_info(config.SYMBOL)
        tick = mt5h.get_tick(config.SYMBOL)
        if info is None or tick is None:
            return
        d = info.digits
        step = float(getattr(strategy, "grid_step", config.MASTER_GRID_STEP))
        n = int(getattr(strategy, "max_level", config.MASTER_MAX_LEVEL))
        magic = getattr(strategy, "magic", config.MAGIC_MASTER)
        comment = getattr(strategy, "comment", config.MASTER_COMMENT)
        init_sl = float(getattr(strategy, "init_sl", config.MASTER_INIT_SL) or 0)
        mid = (tick.ask + tick.bid) / 2.0
        placed = 0
        for i in range(1, n + 1):
            lot = self._lot_for_level(strategy, i)
            price = round(mid + i * step, d)
            sl = round(price - init_sl, d) if init_sl > 0 else 0.0
            if price > tick.ask and mt5h.place_stop_order(
                    config.SYMBOL, "BUY", lot, price, magic, comment, sl=sl, tp=0.0):
                placed += 1
        for i in range(1, n + 1):
            lot = self._lot_for_level(strategy, i)
            price = round(mid - i * step, d)
            sl = round(price + init_sl, d) if init_sl > 0 else 0.0
            if price < tick.bid and mt5h.place_stop_order(
                    config.SYMBOL, "SELL", lot, price, magic, comment, sl=sl, tp=0.0):
                placed += 1
        logger.info(f"[MASTER] 🪜 Dải lưới quanh {mid:.{d}f}: đặt {placed}/{2*n} lệnh stop")

    # ------------------------------------------------------------------
    def _trail_orders(self, strategy, positions):
        tick = mt5h.get_tick(config.SYMBOL)
        info = mt5h.get_symbol_info(config.SYMBOL)
        if tick is None or info is None:
            return
        d = info.digits
        magic = getattr(strategy, "magic", config.MAGIC_MASTER)
        start = float(getattr(strategy, "trail_start", config.MASTER_TRAIL_START))
        step = float(getattr(strategy, "trail_step", config.MASTER_TRAIL_STEP))
        for p in positions:
            if p.type == 0:  # BUY
                fav = tick.bid - p.price_open
                if fav >= start:
                    new_sl = round(p.price_open + (fav - step), d)
                    if new_sl < tick.bid and (not p.sl or new_sl > p.sl + 1e-9):
                        if mt5h.modify_position(config.SYMBOL, p.ticket, sl=new_sl, tp=(p.tp or 0.0), magic=magic):
                            logger.info(f"[MASTER] ↕️ Dời SL BUY {p.ticket} → {new_sl}")
            else:            # SELL
                fav = p.price_open - tick.ask
                if fav >= start:
                    new_sl = round(p.price_open - (fav - step), d)
                    if new_sl > tick.ask and (not p.sl or new_sl < p.sl - 1e-9):
                        if mt5h.modify_position(config.SYMBOL, p.ticket, sl=new_sl, tp=(p.tp or 0.0), magic=magic):
                            logger.info(f"[MASTER] ↕️ Dời SL SELL {p.ticket} → {new_sl}")

    # ------------------------------------------------------------------
    def _monthly_dd(self, acct):
        if acct is None:
            return
        key = datetime.now(timezone.utc).strftime("%Y-%m")
        if self._month_key != key:
            self._month_key = key
            self._dd_peak = acct.equity
            self._dd_max = 0.0
            logger.info(f"[MASTER] 📅 Tháng mới {key}: reset DD (equity {acct.equity:.2f})")
        self._dd_peak = max(self._dd_peak, acct.equity)
        dd = self._dd_peak - acct.equity
        if dd > self._dd_max:
            self._dd_max = dd
            logger.info(f"[MASTER] 📉 DD tháng {key}: {dd:.2f}$ (đỉnh {self._dd_peak:.2f})")

    # ------------------------------------------------------------------
    def _cancel_all_pending(self, magic):
        for _ in range(5):
            pend = mt5h.get_pending_orders(config.SYMBOL, magic)
            if not pend:
                break
            for o in pend:
                mt5h.cancel_pending_order(o.ticket)
            time.sleep(0.2)

    def _close_all(self, strategy, reason="đóng toàn bộ"):
        magic = getattr(strategy, "magic", config.MAGIC_MASTER)
        self._closing_all = True
        try:
            self._cancel_all_pending(magic)
            total = len(mt5h.get_open_positions(config.SYMBOL, [magic]))
            logger.warning(f"[MASTER] ⏹️ {reason} → đóng {total} vị thế + xóa pending")
            for _ in range(60):
                rest = mt5h.get_open_positions(config.SYMBOL, [magic])
                if not rest:
                    break
                for p in rest:
                    mt5h.close_position(p, magic, "master close all")
                time.sleep(0.2)
        finally:
            self._magic = None
            self._known = set()
            self._peak = None
            self._closing_all = False
        logger.info("[MASTER] ✅ Đã đóng sạch; chờ chu kỳ mới")


master_engine = MasterEngine()
