"""
core/hedging_engine.py
Engine cho chiến lược Hedging Grid (XAUUSD).

Nhiệm vụ (gọi mỗi HEDGE_POLL_SEC từ BotEngine khi chiến lược "hedging" đang chọn):
  1. Lần đầu: nếu CHƯA có vị thế nào của magic → mở 1 cặp BUY + SELL.
     Nếu đã có (bot restart / chạy lại) → "tiếp quản", không mở thêm.
  2. Các lần sau: phát hiện ticket đã biến mất (chạm TP do broker tự đóng)
     → mở thêm 1 cặp BUY + SELL mới tại giá hiện tại cho MỖI ticket đã đóng.

Trạng thái lưu trong bộ nhớ; reset() mỗi khi bot khởi động để "tiếp quản"
đúng tập vị thế đang mở (bỏ qua các TP xảy ra lúc bot tắt).
"""
import json
import logging
import os
import time
import threading
from datetime import datetime, timezone, timedelta

import config
from . import mt5_handler as mt5h

logger = logging.getLogger(__name__)


class HedgingEngine:
    def __init__(self):
        self._lock = threading.RLock()
        self._magic = None
        self._known = set()   # các ticket đang được theo dõi
        self._fresh = True    # True = cần khởi tạo/tiếp quản ở lần xử lý kế tiếp
        self._no_money = False  # lần mở gần nhất thất bại vì hết margin
        self._owed_legs = []    # các chân còn THIẾU cần mở bù (BUY/SELL) — mở bằng được mới thôi
        self._last_balance_log = 0.0  # lần cuối ghi log tỷ lệ BUY/SELL
        self._last_close_key = None   # (ngày, giờ) lần cuối đóng cuối phiên
        self._session_start_equity = None  # equity đầu phiên (mốc tính lãi)
        self._session_start_balance = None # balance đầu phiên (để đối chiếu)
        self._session_start_time = None    # thời điểm bắt đầu phiên (giờ VN)
        self._session_orders = 0      # số lệnh đã mở trong phiên (để xét cân bằng BUY/SELL)
        self.stop_requested = False   # engine yêu cầu dừng bot (HEDGE_STOP_AFTER_TARGET)

    # ---- Lưu/đọc mốc phiên (để khởi động lại tiếp tục) ----
    def _state_path(self):
        p = getattr(config, "HEDGE_STATE_FILE", "logs/hedge_session.json")
        if not os.path.isabs(p):
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            p = os.path.join(base, p)
        return p

    def _save_state(self):
        try:
            path = self._state_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            data = {
                "session_start_time_vn": self._session_start_time.isoformat()
                if self._session_start_time else None,
                "session_start_equity": self._session_start_equity,
                "session_start_balance": self._session_start_balance,
                "target_usd": float(getattr(config, "HEDGE_TAKE_PROFIT_USD", 0) or 0),
                "magic": self._magic,
                "saved_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[HEDGE] Không ghi được state phiên: {e}")

    def _load_state(self) -> bool:
        try:
            path = self._state_path()
            if not os.path.exists(path):
                return False
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            eq = data.get("session_start_equity")
            if eq is None:
                return False
            self._session_start_equity = float(eq)
            self._session_start_balance = data.get("session_start_balance")
            t = data.get("session_start_time_vn")
            self._session_start_time = datetime.fromisoformat(t) if t else None
            logger.info(
                f"[HEDGE] ♻️ Tiếp tục phiên cũ: bắt đầu "
                f"{self._session_start_time.strftime('%Y-%m-%d %H:%M') if self._session_start_time else '?'} (VN) "
                f"| equity mốc {self._session_start_equity:.2f}$ "
                f"| mục tiêu +{data.get('target_usd', '?')}$"
            )
            return True
        except Exception as e:
            logger.error(f"[HEDGE] Không đọc được state phiên: {e}")
            return False

    def _equity(self):
        acct = mt5h.get_account_info()
        return acct.equity if acct is not None else None

    def _reset_session(self):
        """Bắt đầu phiên mới: ghi mốc thời gian + equity hiện tại rồi lưu ra file."""
        acct = mt5h.get_account_info()
        self._session_start_equity = acct.equity if acct is not None else None
        self._session_start_balance = acct.balance if acct is not None else None
        self._session_start_time = self._vn_now()
        self._session_orders = 0
        self._save_state()
        target = float(getattr(config, "HEDGE_TAKE_PROFIT_USD", 0) or 0)
        logger.info(
            f"[HEDGE] 🏁 PHIÊN MỚI: bắt đầu "
            f"{self._session_start_time.strftime('%Y-%m-%d %H:%M')} (VN) | "
            f"equity mốc {self._session_start_equity:.2f}$ "
            f"| mục tiêu +{target:.0f}$"
        )

    def _vn_now(self):
        """Giờ Việt Nam hiện tại (naive) = UTC + VN_UTC_OFFSET."""
        off = int(getattr(config, "VN_UTC_OFFSET", 7))
        return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=off)

    def _in_week_block(self) -> bool:
        """True nếu đang trong khung CHẶN mở lệnh mới cuối tuần (giờ VN).

        Mặc định: từ 00:00 thứ 5 đến 07:00 thứ 2 → không mở cặp mới.
        Chỉ chặn MỞ; vị thế đang mở vẫn giữ (broker tự đóng theo TP).
        """
        if not getattr(config, "HEDGE_WEEKEND_BLOCK_ENABLED", True):
            return False
        vn = self._vn_now()
        start_wd = int(getattr(config, "HEDGE_BLOCK_FROM_WEEKDAY", 3)) % 7
        start_h = int(getattr(config, "HEDGE_BLOCK_FROM_HOUR", 0)) % 24
        end_wd = int(getattr(config, "HEDGE_RESUME_WEEKDAY", 0)) % 7
        end_h = int(getattr(config, "HEDGE_RESUME_HOUR", 7)) % 24
        now = vn.weekday() * 1440 + vn.hour * 60 + vn.minute
        start = start_wd * 1440 + start_h * 60
        end = end_wd * 1440 + end_h * 60
        if start <= end:
            return start <= now < end
        return now >= start or now < end

    def reset(self):
        """Xóa trạng thái để lần chạy tới tiếp quản vị thế hiện có."""
        with self._lock:
            self._magic = None
            self._known = set()
            self._fresh = True
            self.stop_requested = False
            self._session_start_equity = None
            self._session_start_balance = None
            self._session_start_time = None
            self._session_orders = 0
            self._owed_legs = []
            self._load_state()   # có mốc phiên cũ -> tiếp tục; không thì mở phiên mới ở tick sau

    # ------------------------------------------------------------------
    def process(self, strategy):
        magic = getattr(strategy, "magic", config.MAGIC_HEDGE)
        if self.stop_requested:
            return
        if not mt5h.connect():
            logger.error("[HEDGE] Không kết nối được MT5")
            return
        # Sàn đóng -> không tiếp quản/sửa lệnh (tránh spam lỗi 10018)
        if not mt5h.is_market_open(config.SYMBOL):
            return

        with self._lock:
            self._no_money = False
            if self._magic != magic:
                self._magic = magic
                self._known = set()
                self._fresh = True

            # --- TỰ HÀN GẮN (chạy MỖI vòng): TP + chân thiếu ---
            self._reconcile(strategy)

            positions = mt5h.get_open_positions(config.SYMBOL, [magic])
            current = {p.ticket for p in positions}

            # --- Chốt theo TỔNG LÃI PHIÊN (equity - đầu phiên) ---
            target = float(getattr(config, "HEDGE_TAKE_PROFIT_USD", 0) or 0)
            if target > 0:
                acct = mt5h.get_account_info()
                if acct is not None:
                    eq = acct.equity
                    if self._session_start_equity is None:
                        self._session_start_equity = eq
                        self._session_start_balance = acct.balance
                        self._session_start_time = self._vn_now()
                        self._save_state()
                        logger.info(
                            f"[HEDGE] 🏁 Mốc lãi phiên: "
                            f"{self._session_start_time.strftime('%Y-%m-%d %H:%M')} (VN) "
                            f"| equity đầu = {eq:.2f}$ | mục tiêu +{target:.0f}$"
                        )
                    elif eq - self._session_start_equity >= target:
                        self._close_all(
                            strategy,
                            f"ĐẠT MỤC TIÊU lãi {target:.0f}$ "
                            f"(equity {eq:.2f} vs mốc {self._session_start_equity:.2f})"
                        )
                        self._reset_session()
                        self._maybe_log_balance(strategy)
                        if getattr(config, "HEDGE_STOP_AFTER_TARGET", False):
                            self.stop_requested = True
                        return

            # --- Đóng phiên khi số BUY ≈ số SELL (từ HEDGE_BALANCE_MIN_ORDERS lệnh) ---
            min_ord = int(getattr(config, "HEDGE_BALANCE_MIN_ORDERS", 0) or 0)
            bal_pct = float(getattr(config, "HEDGE_BALANCE_PCT", 0) or 0)
            if min_ord > 0 and self._session_orders >= min_ord and positions:
                nb = sum(1 for p in positions if p.type == 0)
                ns = sum(1 for p in positions if p.type == 1)
                mx = max(nb, ns)
                if mx > 0 and abs(nb - ns) <= bal_pct * mx:
                    logger.warning(
                        f"[HEDGE] ⚖️ BUY={nb} ≈ SELL={ns} (sau {self._session_orders} lệnh) "
                        f"→ đóng cả phiên, bắt đầu phiên mới"
                    )
                    self._close_all(strategy, f"cân bằng BUY={nb}/SELL={ns}")
                    self._reset_session()
                    self._maybe_log_balance(strategy)
                    return

            # --- Giới hạn giờ giao dịch (giờ VN): chỉ chặn MỞ lệnh mới ---
            # Gồm khung giờ bị chặn trong ngày (HEDGE_SKIP_HOURS_VN) và khung
            # cuối tuần (thứ 5 00:00 → thứ 2 07:00).
            skip = getattr(config, "HEDGE_SKIP_HOURS_VN", []) or []
            h = self._vn_now().hour
            in_skip_hours = (getattr(config, "HEDGE_TRADING_HOURS_ENABLED", True)
                             and any(a <= h < b for a, b in skip))
            if in_skip_hours or self._in_week_block():
                # Ngoài giờ: không mở mới; giữ nguyên vị thế (broker tự đóng theo TP)
                if current:
                    self._known = set(current)
                    self._fresh = False
                else:
                    self._known = set()
                    self._fresh = True
                self._maybe_log_balance(strategy)
                return

            # --- Lần đầu của magic này: mở cặp đầu HOẶC tiếp quản ---
            if self._fresh:
                if current:
                    self._known = set(current)
                    self._fresh = False
                    logger.info(
                        f"[HEDGE] Tiếp quản {len(current)} vị thế đang mở (magic={magic})"
                    )
                    self._normalize_tp(positions, strategy)
                else:
                    if self._open_pair(strategy):
                        self._known = self._tickets(magic)
                        self._fresh = False
                        logger.info("[HEDGE] Đã mở cặp BUY+SELL đầu tiên")
                    # nếu thất bại: giữ _fresh=True để thử lại vòng sau
                self._maybe_log_balance(strategy)
                return

            # --- Các vòng sau: ticket biến mất = đã chạm TP ---
            closed = self._known - current
            if closed:
                logger.info(
                    f"[HEDGE] {len(closed)} lệnh chạm TP → mở {len(closed)} cặp BUY+SELL mới"
                )
                for _ in closed:
                    self._open_pair(strategy)
                current = self._tickets(magic)

            self._known = set(current)

            # --- Hết margin: đóng toàn bộ và bắt đầu chu kỳ mới ---
            if self._no_money and getattr(config, "HEDGE_RESET_ON_NO_MARGIN", True):
                self._reset_cycle(strategy)

            # Có lệnh thoát -> log lại tỷ lệ BUY/SELL ngay
            self._maybe_log_balance(strategy, force=bool(closed))

    # ------------------------------------------------------------------
    def _maybe_log_balance(self, strategy, force=False):
        """Ghi log tỷ lệ BUY/SELL. `force=True` để log ngay khi có lệnh thoát."""
        sec = int(getattr(config, "HEDGE_LOG_BALANCE_SEC", 0) or 0)
        if not force:
            if sec <= 0:
                return
            now = time.time()
            if now - self._last_balance_log < sec:
                return
        self._last_balance_log = time.time()

        magic = getattr(strategy, "magic", config.MAGIC_HEDGE)
        positions = mt5h.get_open_positions(config.SYMBOL, [magic])
        buys = [p for p in positions if p.type == 0]
        sells = [p for p in positions if p.type == 1]
        nb, ns = len(buys), len(sells)
        vol_b = sum(p.volume for p in buys)
        vol_s = sum(p.volume for p in sells)
        net = vol_b - vol_s
        floating = sum(p.profit for p in positions)
        ratio = (nb / ns) if ns else float("inf")
        acct = mt5h.get_account_info()
        eq = acct.equity if acct is not None else 0.0
        side = "CÂN BẰNG" if abs(net) < 1e-9 else ("LỆCH BUY" if net > 0 else "LỆCH SELL")
        logger.info(
            f"[HEDGE] ⚖️ {side}: BUY={nb} ({vol_b:.2f} lot) | SELL={ns} ({vol_s:.2f} lot) "
            f"| tỷ lệ B/S={ratio:.2f} | net={net:+.2f} lot | lỗ nổi={floating:+.2f}$ | equity={eq:.2f}$"
        )

    # ------------------------------------------------------------------
    def _close_all(self, strategy, reason="đóng toàn bộ"):
        """Đóng toàn bộ vị thế hedging (dùng cho cuối phiên / hết giờ theo dõi)."""
        magic = getattr(strategy, "magic", config.MAGIC_HEDGE)
        total = len(mt5h.get_open_positions(config.SYMBOL, [magic]))
        if total == 0:
            self._magic = None
            self._known = set()
            self._fresh = True
            return
        logger.warning(f"[HEDGE] ⏹️ {reason} → đóng toàn bộ {total} vị thế")
        for _ in range(60):
            rest = mt5h.get_open_positions(config.SYMBOL, [magic])
            if not rest:
                break
            for p in rest:
                mt5h.close_position(p, magic, "close all")
            time.sleep(0.2)
        self._magic = None
        self._known = set()
        self._fresh = True
        self._owed_legs = []
        logger.info("[HEDGE] ✅ Đã đóng sạch; chờ phiên giao dịch mới")

    # ------------------------------------------------------------------
    def _reset_cycle(self, strategy):
        """Đóng toàn bộ vị thế hedging rồi reset để mở chu kỳ mới."""
        magic = getattr(strategy, "magic", config.MAGIC_HEDGE)
        positions = mt5h.get_open_positions(config.SYMBOL, [magic])
        if not positions:
            self._fresh = True
            return
        logger.warning(
            f"[HEDGE] ⛔ HẾT MARGIN → ĐÓNG TOÀN BỘ {len(positions)} vị thế, "
            f"bắt đầu chu kỳ giao dịch mới"
        )
        for _ in range(30):  # đóng lặp tới khi sạch (tối đa 30 vòng)
            positions = mt5h.get_open_positions(config.SYMBOL, [magic])
            if not positions:
                break
            for p in positions:
                mt5h.close_position(p, magic, "hedge cycle reset")
            time.sleep(0.2)
        self._magic = None
        self._known = set()
        self._fresh = True
        self._owed_legs = []
        self._reset_session()
        logger.info("[HEDGE] ✅ Đã đóng hết, chu kỳ mới sẽ bắt đầu ở vòng sau")

    # ------------------------------------------------------------------
    def _is_no_money(self, strategy) -> bool:
        """True nếu free margin không đủ mở thêm 1 chân (0.01 lot)."""
        acct = mt5h.get_account_info()
        if acct is None:
            return False
        lot = float(getattr(strategy, "lot", config.HEDGE_LOT))
        need = mt5h.get_margin_required(config.SYMBOL, lot, "BUY")
        if need is None:
            tick = mt5h.get_tick(config.SYMBOL)
            need = (tick.ask / 1000.0) if tick is not None else 0.0
        return acct.margin_free < (need or 0.0) + 0.01

    def _tickets(self, magic) -> set:
        positions = mt5h.get_open_positions(config.SYMBOL, [magic])
        return {p.ticket for p in positions}

    def _normalize_tp(self, positions, strategy):
        """Đảm bảo mọi vị thế cũ có ĐÚNG TP (kể cả TP=0) — dùng khi tiếp quản."""
        info = mt5h.get_symbol_info(config.SYMBOL)
        if info is None:
            return
        d = info.digits
        dist = float(getattr(strategy, "tp_distance", config.HEDGE_TP_USD))
        magic = getattr(strategy, "magic", config.MAGIC_HEDGE)
        fixed = 0
        for p in positions:
            if not p.price_open:
                continue
            want = round(p.price_open + dist if p.type == 0 else p.price_open - dist, d)
            if mt5h.ensure_tp(config.SYMBOL, p, want, magic) == "OK":
                fixed += 1
        if fixed:
            logger.info(f"[HEDGE] Chuẩn hóa TP cho {fixed} vị thế cũ (dist={dist})")

    def _open_single(self, strategy, typ: str) -> int:
        """Mở DUY NHẤT 1 chân (BUY/SELL) và đảm bảo có TP. Trả về ticket hoặc 0."""
        dist = float(getattr(strategy, "tp_distance", config.HEDGE_TP_USD))
        lot = float(getattr(strategy, "lot", config.HEDGE_LOT))
        magic = getattr(strategy, "magic", config.MAGIC_HEDGE)
        comment = getattr(strategy, "comment", config.HEDGE_COMMENT)
        dev = int(getattr(config, "HEDGE_MAX_DEVIATION_PTS", 0) or 0)
        info = mt5h.get_symbol_info(config.SYMBOL)
        tick = mt5h.get_tick(config.SYMBOL)
        if info is None or tick is None:
            return 0
        d = info.digits
        prov_tp = round(tick.ask + dist if typ == "BUY" else tick.bid - dist, d)
        ticket = mt5h.open_position(config.SYMBOL, typ, lot, 0.0, prov_tp, magic, comment,
                                    max_slippage_points=0, deviation=dev)
        if not ticket:
            return 0
        # Đảm bảo TP ngay (nếu chưa đặt được, reconcile vòng sau lo tiếp)
        pos = mt5h.get_position_by_ticket(ticket)
        if pos is not None and pos.price_open:
            want = round(pos.price_open + dist if pos.type == 0 else pos.price_open - dist, d)
            mt5h.ensure_tp(config.SYMBOL, pos, want, magic)
        return int(ticket)

    def _reconcile(self, strategy):
        """TỰ HÀN GẮN: (1) mọi vị thế có đúng TP (hoặc đóng nếu giá đã chạm TP);
        (2) mở bù mọi chân còn thiếu. Chạy mỗi vòng cho tới khi xong."""
        magic = getattr(strategy, "magic", config.MAGIC_HEDGE)
        dist = float(getattr(strategy, "tp_distance", config.HEDGE_TP_USD))
        info = mt5h.get_symbol_info(config.SYMBOL)
        if info is None:
            return
        d = info.digits

        # 1) Audit TP cho TẤT CẢ vị thế
        for p in mt5h.get_open_positions(config.SYMBOL, [magic]):
            want = round(p.price_open + dist if p.type == 0 else p.price_open - dist, d)
            r = mt5h.ensure_tp(config.SYMBOL, p, want, magic)
            if r == "CLOSE":
                logger.info(f"[HEDGE] TP self-heal: ticket={p.ticket} giá đã chạm TP -> đóng")
                mt5h.close_position(p, magic, "self-heal TP")

        # 2) Mở bù các chân còn thiếu (mở bằng được mới thôi)
        for typ in list(self._owed_legs):
            if self._open_single(strategy, typ):
                self._owed_legs.remove(typ)
            else:
                break   # thử lại vòng sau

    def _open_pair(self, strategy) -> int:
        """Mở 1 cặp BUY+SELL. Chân nào không mở được -> ghi vào _owed_legs để mở bù."""
        retries = max(1, int(getattr(config, "HEDGE_OPEN_RETRIES", 1) or 1))
        ok = 0
        for typ in ("BUY", "SELL"):
            ticket = 0
            for _ in range(retries):
                ticket = self._open_single(strategy, typ)
                if ticket:
                    break
                time.sleep(0.2)
            if ticket:
                ok += 1
            elif typ not in self._owed_legs:
                self._owed_legs.append(typ)

        if ok < 2 and self._is_no_money(strategy):
            self._no_money = True
        self._session_orders += ok
        if ok < 2:
            logger.warning(f"[HEDGE] Chỉ mở được {ok}/2 chân -> sẽ mở bù ở vòng sau")
        return ok


hedging_engine = HedgingEngine()
