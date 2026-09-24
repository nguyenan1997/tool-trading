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

import config
from . import mt5_handler as mt5h

logger = logging.getLogger(__name__)


class HedgingEngine:
    def __init__(self):
        self._lock = threading.Lock()
        self._magic = None
        self._known = set()   # các ticket đang được theo dõi
        self._fresh = True    # True = cần khởi tạo/tiếp quản ở lần xử lý kế tiếp

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
            if self._magic != magic:
                self._magic = magic
                self._known = set()
                self._fresh = True

            positions = mt5h.get_open_positions(config.SYMBOL, [magic])
            current = {p.ticket for p in positions}

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

    # ------------------------------------------------------------------
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
        return ok


hedging_engine = HedgingEngine()
