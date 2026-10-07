"""
core/bigmouse_engine.py
Engine cho chiến lược BigMouse Hedging (mỏ neo + hedge stop + martingale).

Vòng lặp (gọi mỗi BIGMOUSE_POLL_SEC từ BotEngine khi chiến lược "bigmouse" đang chọn):
  1. CHU KỲ MỚI: mở 1 lệnh MARKET "mỏ neo" (mặc định BUY) lot L, TP cách giá vào.
     Đặt kèm 1 lệnh CHỜ STOP ngược chiều (SELL STOP) lot = L × MULT,
     cách giá vào một khoảng hedge-trigger, có TP riêng.
  2. THEO DÕI:
     - Mỏ neo chạm TP (biến mất, không có vị thế hedge) → hủy STOP còn treo,
       kết thúc chu kỳ THẮNG → về lot gốc.
     - STOP khớp → có vị thế hedge → basket gồm mỏ neo + hedge.
     - Tổng lãi nổi của basket đạt BIGMOUSE_BASKET_TP_USD → đóng toàn bộ, kết thúc chu kỳ.
     - Tất cả vị thế đóng (broker tự TP hoặc đóng tay) → kết thúc chu kỳ.
  3. MARTINGALE: cuối mỗi chu kỳ so balance với mốc đầu chu kỳ; LỖ → tăng step,
     LÃI → về step 0. Lot = lot_gốc × MULT^step (giới hạn MAX_STEPS).

Trạng thái bước martingale lưu trong BIGMOUSE_STATE_FILE để tiếp quản khi restart.
"""
import json
import logging
import os
import time
import threading
from datetime import datetime, timezone

import config
from . import mt5_handler as mt5h

logger = logging.getLogger(__name__)


class BigMouseEngine:
    def __init__(self):
        self._lock = threading.RLock()
        self._magic = None
        self._fresh = True            # True = cần bắt đầu/tiếp quản chu kỳ ở vòng kế tiếp
        self._known_pos = set()       # ticket các vị thế đang theo dõi
        self._known_ord = set()       # ticket các lệnh chờ đang theo dõi
        self._anchor_ticket = None    # ticket lệnh mỏ neo
        self._cycle_start_balance = None
        self._step = 0                # bước martingale hiện tại
        self.stop_requested = False
        self._load_state()

    # ---- Lưu/đọc bước martingale (tiếp quản khi restart) ----
    def _state_path(self):
        p = getattr(config, "BIGMOUSE_STATE_FILE", "logs/bigmouse_state.json")
        if not os.path.isabs(p):
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            p = os.path.join(base, p)
        return p

    def _save_state(self):
        try:
            path = self._state_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            data = {
                "martingale_step": int(self._step),
                "magic": self._magic,
                "saved_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[BIGMOUSE] Không ghi được state: {e}")

    def _load_state(self):
        try:
            path = self._state_path()
            if not os.path.exists(path):
                return
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self._step = int(data.get("martingale_step", 0) or 0)
            if self._step:
                logger.info(f"[BIGMOUSE] ♻️ Tiếp tục bước martingale = {self._step}")
        except Exception as e:
            logger.error(f"[BIGMOUSE] Không đọc được state: {e}")

    def reset(self):
        """Xóa trạng thái để lần chạy tới tiếp quản vị thế hiện có."""
        with self._lock:
            self._magic = None
            self._fresh = True
            self._known_pos = set()
            self._known_ord = set()
            self._anchor_ticket = None
            self._cycle_start_balance = None
            self.stop_requested = False
            self._load_state()

    # ------------------------------------------------------------------
    def process(self, strategy):
        magic = getattr(strategy, "magic", config.MAGIC_BIGMOUSE)
        if self.stop_requested:
            return
        if not mt5h.connect():
            logger.error("[BIGMOUSE] Không kết nối được MT5")
            return
        if not mt5h.is_market_open(config.SYMBOL):
            return

        with self._lock:
            if self._magic != magic:
                self._magic = magic
                self._fresh = True
                self._known_pos = set()
                self._known_ord = set()
                self._anchor_ticket = None

            positions = mt5h.get_open_positions(config.SYMBOL, [magic])
            orders = mt5h.get_pending_orders(config.SYMBOL, magic)
            cur_pos = {p.ticket for p in positions}
            cur_ord = {o.ticket for o in orders}

            # --- Lần đầu: bắt đầu chu kỳ HOẶC tiếp quản ---
            if self._fresh:
                if cur_pos or cur_ord:
                    self._adopt(positions, orders)
                    self._fresh = False
                    return
                self._start_cycle(strategy)
                return

            # --- Đóng basket khi tổng lãi nổi đạt mục tiêu ---
            if positions:
                floating = sum(p.profit for p in positions)
                basket_tp = float(getattr(strategy, "basket_tp", 0) or 0)
                if basket_tp > 0 and floating >= basket_tp:
                    self._close_all(strategy, f"BASKET TP {basket_tp:.0f}$ (lãi nổi {floating:.2f}$)")
                    self._end_cycle(strategy, won=True)
                    return

            # --- Mỏ neo biến mất mà KHÔNG có vị thế hedge (chạm TP) ---
            if self._anchor_ticket and self._anchor_ticket not in cur_pos:
                has_hedge = bool(cur_pos - {self._anchor_ticket})
                if not has_hedge and cur_ord:
                    for o in orders:
                        mt5h.cancel_pending_order(o.ticket)
                    cur_ord = set()
                if not cur_pos and not cur_ord:
                    logger.info("[BIGMOUSE] ✅ Mỏ neo chạm TP → kết thúc chu kỳ THẮNG")
                    self._end_cycle(strategy, won=True)
                    return
                # còn vị thế hedge → giao quyền cho vòng sau quản lý

            # --- Mọi thứ đã đóng (broker tự khớp TP) → kết thúc chu kỳ ---
            if not cur_pos and not cur_ord:
                won = (self._anchor_ticket is None) or (self._anchor_ticket not in self._known_pos)
                logger.info("[BIGMOUSE] 🔁 Chu kỳ đã đóng sạch → kết thúc")
                self._end_cycle(strategy, won=won)
                return

            # --- STOP khớp: lệnh chờ biến mất và có vị thế mới ---
            triggered = self._known_ord - cur_ord
            new_pos = cur_pos - self._known_pos
            if triggered and new_pos:
                for t in new_pos:
                    pos = mt5h.get_position_by_ticket(t)
                    side = "BUY" if pos and pos.type == 0 else "SELL"
                    logger.warning(
                        f"[BIGMOUSE] 🛡️ HEDGE KÍCH HOẠT | ticket={t} | {side} "
                        f"lot={pos.volume if pos else '?'} → basket đang được hedge"
                    )

            # --- Một chân basket đã đóng (vd hedge chạm TP) còn chân kia → chốt net ---
            gone = self._known_pos - cur_pos
            if gone and cur_pos and not cur_ord:
                self._close_all(strategy, "một chân basket đã đóng → chốt net toàn basket")
                self._end_cycle(strategy, won=True)
                return

            self._known_pos = set(cur_pos)
            self._known_ord = set(cur_ord)

    # ------------------------------------------------------------------
    def _adopt(self, positions, orders):
        """Tiếp quản vị thế/lệnh chờ đang mở khi bot khởi động lại."""
        self._known_pos = {p.ticket for p in positions}
        self._known_ord = {o.ticket for o in orders}
        for p in positions:
            if p.type == 0 and self._anchor_is_buy():
                self._anchor_ticket = p.ticket
            elif p.type == 1 and not self._anchor_is_buy():
                self._anchor_ticket = p.ticket
            elif self._anchor_ticket is None:
                self._anchor_ticket = p.ticket
        if self._cycle_start_balance is None:
            acct = mt5h.get_account_info()
            self._cycle_start_balance = acct.balance if acct else None
        logger.info(
            f"[BIGMOUSE] ♻️ Tiếp quản {len(positions)} vị thế + {len(orders)} lệnh chờ "
            f"(magic={self._magic}, bước martingale={self._step})"
        )

    def _anchor_is_buy(self):
        return getattr(self, "_dir", "BUY") == "BUY"

    # ------------------------------------------------------------------
    def _lot(self, strategy) -> float:
        base = float(getattr(strategy, "lot", config.BIGMOUSE_LOT))
        if not getattr(strategy, "martingale", False):
            return base
        max_steps = int(getattr(strategy, "martingale_max_steps", 0) or 0)
        step = max(0, min(self._step, max_steps))
        mult = float(getattr(strategy, "martingale_mult", 1.0) or 1.0)
        return round(base * (mult ** step), 2)

    def _start_cycle(self, strategy) -> bool:
        """Mở lệnh mỏ neo + đặt lệnh STOP hedge. Trả về True nếu mở được mỏ neo."""
        direction = str(getattr(strategy, "direction", "BUY")).upper()
        self._dir = direction
        lot = self._lot(strategy)
        magic = getattr(strategy, "magic", config.MAGIC_BIGMOUSE)
        comment = getattr(strategy, "comment", config.BIGMOUSE_COMMENT)
        tp_dist = float(getattr(strategy, "tp_distance", config.BIGMOUSE_TP_USD))
        trig_dist = float(getattr(strategy, "hedge_trigger_distance", config.BIGMOUSE_HEDGE_TRIGGER_USD))
        hedge_tp_dist = float(getattr(strategy, "hedge_tp_distance", config.BIGMOUSE_HEDGE_TP_USD))
        hedge_mult = float(getattr(strategy, "hedge_lot_mult", config.BIGMOUSE_HEDGE_LOT_MULT))
        dev = int(getattr(config, "BIGMOUSE_MAX_DEVIATION_PTS", 0) or 0)
        retries = max(1, int(getattr(config, "BIGMOUSE_OPEN_RETRIES", 1) or 1))

        info = mt5h.get_symbol_info(config.SYMBOL)
        tick = mt5h.get_tick(config.SYMBOL)
        if info is None or tick is None:
            return False
        d = info.digits

        # --- 1) Lệnh MARKET mỏ neo ---
        anchor_tp = round(tick.ask + tp_dist, d) if direction == "BUY" else round(tick.bid - tp_dist, d)
        anchor_ticket = None
        for _ in range(retries):
            anchor_ticket = mt5h.open_position(
                config.SYMBOL, direction, lot, 0.0, anchor_tp, magic, comment,
                max_slippage_points=0, deviation=dev,
            )
            if anchor_ticket:
                break
            time.sleep(0.3)
        if not anchor_ticket:
            logger.warning("[BIGMOUSE] Không mở được lệnh mỏ neo, thử lại vòng sau")
            return False

        # --- 2) Lệnh CHỜ STOP ngược chiều (hedge) ---
        pos = mt5h.get_position_by_ticket(anchor_ticket)
        entry = pos.price_open if pos else (tick.ask if direction == "BUY" else tick.bid)
        hedge_side = "SELL" if direction == "BUY" else "BUY"
        hedge_lot = round(lot * hedge_mult, 2)
        if direction == "BUY":
            trigger = round(entry - trig_dist, d)
            hedge_tp = round(trigger - hedge_tp_dist, d)
        else:
            trigger = round(entry + trig_dist, d)
            hedge_tp = round(trigger + hedge_tp_dist, d)
        ord_ticket = mt5h.place_stop_order(
            config.SYMBOL, hedge_side, hedge_lot, trigger, 0.0, hedge_tp, magic, comment,
        )

        acct = mt5h.get_account_info()
        self._cycle_start_balance = acct.balance if acct else None
        self._anchor_ticket = anchor_ticket
        self._known_pos = {anchor_ticket}
        self._known_ord = {ord_ticket} if ord_ticket else set()
        self._fresh = False

        logger.info(
            f"[BIGMOUSE] 🚀 CHU KỲ MỚI | mỏ neo {direction} lot={lot} "
            f"entry={entry:.{d}f} TP={anchor_tp:.{d}f} | hedge {hedge_side} STOP "
            f"lot={hedge_lot} @ {trigger:.{d}f} TP={hedge_tp:.{d}f} | bước martingale={self._step}"
        )
        if not ord_ticket:
            logger.warning(
                "[BIGMOUSE] ⚠️ Không đặt được lệnh STOP hedge (giá đã vượt mức?) — "
                "mỏ neo chạy KHÔNG có hedge, rủi ro cao!"
            )
        return True

    # ------------------------------------------------------------------
    def _end_cycle(self, strategy, won):
        """Kết thúc chu kỳ: cập nhật martingale rồi sẵn sàng mở chu kỳ mới."""
        acct = mt5h.get_account_info()
        pnl = None
        if acct is not None and self._cycle_start_balance is not None:
            pnl = acct.balance - self._cycle_start_balance

        if getattr(strategy, "martingale", False):
            # Ưu tiên PnL thực tế (balance cuối - đầu chu kỳ); nếu không có thì dùng cờ `won`.
            lost = (pnl < 0) if pnl is not None else (not won)
            if not lost:
                if self._step:
                    logger.info(f"[BIGMOUSE] 🔄 THẮNG → về lot gốc (bước {self._step}→0, PnL={pnl})")
                self._step = 0
            else:
                max_steps = int(getattr(strategy, "martingale_max_steps", 0) or 0)
                if self._step < max_steps:
                    self._step += 1
                next_lot = round(
                    float(getattr(strategy, "lot", config.BIGMOUSE_LOT))
                    * (float(getattr(strategy, "martingale_mult", 1.0)) ** self._step), 2
                )
                logger.warning(
                    f"[BIGMOUSE] 📉 LỖ {pnl if pnl is not None else '?'}$ → tăng bước martingale "
                    f"lên {self._step} (lot tiếp theo = {next_lot})"
                )
        self._save_state()

        self._known_pos = set()
        self._known_ord = set()
        self._anchor_ticket = None
        self._cycle_start_balance = None
        self._fresh = True

        if getattr(config, "BIGMOUSE_ONE_CYCLE_ONLY", False):
            logger.info("[BIGMOUSE] Đã hoàn tất 1 chu kỳ (cấu hình ONE_CYCLE_ONLY) → dừng bot")
            self.stop_requested = True
        elif getattr(config, "BIGMOUSE_STOP_AFTER_BASKET", False) and won:
            logger.info("[BIGMOUSE] Đã đóng basket có lãi (cấu hình STOP_AFTER_BASKET) → dừng bot")
            self.stop_requested = True

    # ------------------------------------------------------------------
    def _close_all(self, strategy, reason="đóng toàn bộ"):
        magic = getattr(strategy, "magic", config.MAGIC_BIGMOUSE)
        total = len(mt5h.get_open_positions(config.SYMBOL, [magic]))
        if total:
            logger.warning(f"[BIGMOUSE] ⏹️ {reason} → đóng {total} vị thế")
            for _ in range(60):
                rest = mt5h.get_open_positions(config.SYMBOL, [magic])
                if not rest:
                    break
                for p in rest:
                    mt5h.close_position(p, magic, "bigmouse close all")
                time.sleep(0.2)
        for o in mt5h.get_pending_orders(config.SYMBOL, magic):
            mt5h.cancel_pending_order(o.ticket)


bigmouse_engine = BigMouseEngine()
