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
import logging
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
        self._last_balance_log = 0.0  # lần cuối ghi log tỷ lệ BUY/SELL
        self._last_close_key = None   # (ngày, giờ) lần cuối đóng cuối phiên

    def _vn_now(self):
        """Giờ Việt Nam hiện tại (naive) = UTC + VN_UTC_OFFSET."""
        off = int(getattr(config, "VN_UTC_OFFSET", 7))
        return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=off)

    def reset(self):
        """Xóa trạng thái để lần chạy tới tiếp quản vị thế hiện có."""
        with self._lock:
            self._magic = None
            self._known = set()
            self._fresh = True

    # ------------------------------------------------------------------
    def process(self, strategy):
        magic = getattr(strategy, "magic", config.MAGIC_HEDGE)
        if not mt5h.connect():
            logger.error("[HEDGE] Không kết nối được MT5")
            return

        with self._lock:
            self._no_money = False
            if self._magic != magic:
                self._magic = magic
                self._known = set()
                self._fresh = True

            positions = mt5h.get_open_positions(config.SYMBOL, [magic])
            current = {p.ticket for p in positions}

            # --- Giới hạn giờ giao dịch (giờ VN) ---
            if getattr(config, "HEDGE_TRADING_HOURS_ENABLED", True):
                vn = self._vn_now()
                h = vn.hour
                skip = getattr(config, "HEDGE_SKIP_HOURS_VN", []) or []
                before = max(1, int(getattr(config, "HEDGE_CLOSE_BEFORE_HOURS", 1) or 1))
                in_skip = any(a <= h < b for a, b in skip)
                in_close = any((a - before) <= h < a for a, b in skip)

                if in_skip:
                    # Đã tới hạn mà còn lệnh -> CẮT TOÀN BỘ (dù chưa cân bằng)
                    if current:
                        self._close_all(strategy, "hết giờ theo dõi (chưa cân bằng)")
                    else:
                        self._known = set()
                        self._fresh = True
                    self._maybe_log_balance(strategy)
                    return

                if in_close:
                    # Cửa sổ theo dõi: đóng toàn bộ khi BUY = SELL; chưa cân bằng thì chờ
                    if current:
                        nb = sum(1 for p in positions if p.type == 0)
                        ns = sum(1 for p in positions if p.type == 1)
                        if nb == ns:
                            self._close_all(strategy, f"đã cân bằng {nb}BUY/{ns}SELL")
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
        """Chuẩn hóa TP của các vị thế đang mở: đặt lại TP = giá khớp ± tp_distance.
        Dùng khi bot tiếp quản (restart) các lệnh cũ có TP lệch do trượt giá."""
        info = mt5h.get_symbol_info(config.SYMBOL)
        if info is None:
            return
        d = info.digits
        dist = float(getattr(strategy, "tp_distance", config.HEDGE_TP_USD))
        fixed = 0
        for p in positions:
            if not p.tp or not p.price_open:
                continue
            want = round(
                p.price_open + dist if p.type == 0 else p.price_open - dist, d
            )
            if abs(p.tp - want) > 1e-6:
                if mt5h.modify_position(config.SYMBOL, p.ticket,
                                        sl=(p.sl or 0.0), tp=want):
                    fixed += 1
        if fixed:
            logger.info(f"[HEDGE] Chuẩn hóa TP cho {fixed} vị thế cũ (dist={dist})")

    def _open_pair(self, strategy) -> int:
        """Mở 1 BUY + 1 SELL tại giá hiện tại, mỗi lệnh TP cách giá vào tp_distance.
        Trả về số lệnh mở thành công (0, 1, hoặc 2).

        - Gửi BUY và SELL LIỀN NHAU (không chen bước chỉnh TP ở giữa) để 2 chân
          sát thời điểm nhất.
        - Có `deviation` giới hạn trượt; nếu sàn từ chối (giá chạy quá ngưỡng)
          thì thử lại chân còn thiếu vài lần.
        - Sau khi khớp mới neo TP vào GIÁ KHỚP THỰC TẾ (`position.price_open`).
        """
        dist = float(getattr(strategy, "tp_distance", config.HEDGE_TP_USD))
        lot = float(getattr(strategy, "lot", config.HEDGE_LOT))
        magic = getattr(strategy, "magic", config.MAGIC_HEDGE)
        comment = getattr(strategy, "comment", config.HEDGE_COMMENT)
        dev = int(getattr(config, "HEDGE_MAX_DEVIATION_PTS", 0) or 0)
        retries = max(1, int(getattr(config, "HEDGE_OPEN_RETRIES", 1) or 1))

        # --- Pha 1: mở 2 chân liền nhau (chân nào bị từ chối sẽ thử lại) ---
        opened = []          # [(typ, ticket), ...]
        pending = {"BUY": True, "SELL": True}
        for attempt in range(retries):
            for typ in ("BUY", "SELL"):
                if not pending[typ]:
                    continue
                info = mt5h.get_symbol_info(config.SYMBOL)
                tick = mt5h.get_tick(config.SYMBOL)
                if info is None or tick is None:
                    continue
                d = info.digits
                prov_tp = round(tick.ask + dist if typ == "BUY" else tick.bid - dist, d)
                # max_slippage_points=0: không tự hủy lệnh; giới hạn trượt qua `deviation`
                ticket = mt5h.open_position(
                    config.SYMBOL, typ, lot, 0.0, prov_tp, magic, comment,
                    max_slippage_points=0, deviation=dev,
                )
                if ticket:
                    opened.append((typ, ticket))
                    pending[typ] = False
            if not (pending["BUY"] or pending["SELL"]):
                break
            if attempt < retries - 1:
                time.sleep(0.3)  # chờ chút rồi đọc tick mới, thử lại

        # --- Pha 2: neo TP theo giá khớp thực tế ---
        for typ, ticket in opened:
            info = mt5h.get_symbol_info(config.SYMBOL)
            d = info.digits if info is not None else 2
            pos = mt5h.get_position_by_ticket(ticket)
            if pos is None or not pos.price_open:
                continue
            exact_tp = round(
                pos.price_open + dist if typ == "BUY" else pos.price_open - dist, d
            )
            if abs((pos.tp or 0.0) - exact_tp) > 1e-9:
                if mt5h.modify_position(config.SYMBOL, ticket, sl=0.0, tp=exact_tp):
                    logger.info(
                        f"[HEDGE] Chỉnh TP {typ} ticket={ticket}: {pos.tp} → {exact_tp} "
                        f"(giá khớp {pos.price_open})"
                    )

        ok = len(opened)
        if ok < 2:
            logger.warning(
                f"[HEDGE] Chỉ mở được {ok}/2 chân của cặp (dev={dev}pts, "
                f"thử {retries} lần)"
            )
            self._no_money = self._is_no_money(strategy)
        return ok


hedging_engine = HedgingEngine()
