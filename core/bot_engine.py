"""
core/bot_engine.py
Hệ điều hành của Bot (Trading Loop).
"""
import json
import os
import time
import threading
import logging
from datetime import datetime, timezone, timedelta

import config
from . import mt5_handler as mt5h
from .hedging_engine import hedging_engine
from .bigmouse_engine import bigmouse_engine
from strategies.manager import strategy_manager

logger = logging.getLogger(__name__)

class BotEngine:
    def __init__(self):
        self.is_running = False
        self._thread: threading.Thread = None # type: ignore
        self.last_candle_time = None
        self.status = "Stopped"
        self._lock = threading.Lock()
        self._pos_r = {}          # ticket -> R ban đầu (khoảng cách entry→SL)
        self._partial_done = set()  # các ticket đã chốt một phần
        self._last_session_log = 0.0  # lần cuối ghi log phiên
        self._last_session_key = None # PP đã ghi log phiên lần cuối (đổi PP → log ngay)
        self._pending = {}        # magic -> meta lệnh CHỜ LIMIT thật {ticket, expire_ts}
        self._hedge_cleared_key = None  # đã dọn lệnh chờ của PP khác khi vào hedging chưa
        self._guard_thread: threading.Thread = None # type: ignore  # luồng chặn lệnh ngoài bot
        self._manual = {}          # magic -> {"side","sl","tp"} — SL/TP ẩn khỏi sàn
        self._manual_warned = set()  # magic đã cảnh báo "có vị thế nhưng thiếu SL/TP"
        self._manual_thread: threading.Thread = None # type: ignore
        self._load_manual()

    def start(self):
        with self._lock:
            if self.is_running:
                return
            # Chờ thread cũ thoát hẳn để tránh 2 vòng lặp chạy song song
            if self._thread is not None and self._thread.is_alive():
                return
            self.is_running = True
            self.status = "Running"
            hedging_engine.reset()   # chạy lại -> tiếp quản vị thế hiện có
            bigmouse_engine.reset()  # chạy lại -> tiếp quản vị thế hiện có
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            self._guard_thread = threading.Thread(target=self._guard_loop, daemon=True)
            self._guard_thread.start()
            self._manual_thread = threading.Thread(target=self._manual_sltp_loop, daemon=True)
            self._manual_thread.start()
            logger.info("Bot Engine STARTED")

    def stop(self):
        with self._lock:
            self.is_running = False
            self.status = "Stopped"
            t = self._thread
            if t is not None and t.is_alive() and t is not threading.current_thread():
                t.join(timeout=10)
            g = self._guard_thread
            if g is not None and g.is_alive() and g is not threading.current_thread():
                g.join(timeout=5)
            m = self._manual_thread
            if m is not None and m.is_alive() and m is not threading.current_thread():
                m.join(timeout=5)
            logger.info("Bot Engine STOPPED")

    def _server_now(self) -> datetime:
        """Giờ broker/server (naive) lấy từ tick mới nhất."""
        tick = mt5h.get_tick(config.SYMBOL)
        if tick is not None:
            return datetime.fromtimestamp(tick.time, timezone.utc).replace(tzinfo=None)
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def _broker_offset(self) -> int:
        """Chênh lệch giờ broker so với UTC (tự nhận diện từ tick, fallback config)."""
        tick = mt5h.get_tick(config.SYMBOL)
        if tick is None:
            return int(getattr(config, "BROKER_UTC_OFFSET", 0))
        server_wall = datetime.fromtimestamp(tick.time, timezone.utc).replace(tzinfo=None)
        utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
        return int(round((server_wall - utc_now).total_seconds() / 3600.0))

    def _active_timeframe(self) -> str:
        """Khung thời gian của chiến lược đang chọn (ICT chạy M5, còn lại M1)."""
        try:
            return getattr(strategy_manager.get_current_strategy(), "timeframe", config.TIMEFRAME)
        except Exception:
            return config.TIMEFRAME

    def _session_window(self, strategy):
        """(start, end) giờ broker của PHIÊN VÀO LỆNH theo chiến lược đang chọn."""
        sess = getattr(strategy, "session", None)
        if sess:
            return sess
        kzs = getattr(strategy, "killzones", None)
        if kzs:
            return (min(s for s, _ in kzs), max(e for _, e in kzs))
        kz_start = getattr(strategy, "kz_start", None)
        kz_end = getattr(strategy, "kz_end", None)
        if kz_start is not None and kz_end is not None:
            return (kz_start, kz_end)
        return (0, 23)

    def get_session_status(self) -> dict:
        """Trạng thái phiên + đếm ngược, hiển thị theo GIỜ VIỆT NAM (UTC+7).
        Phiên định nghĩa theo giờ broker của CHIẾN LƯỢC ĐANG CHỌN; ở đây quy đổi để hiển thị."""
        strategy = strategy_manager.get_current_strategy()
        if strategy is None:
            vn_off = int(getattr(config, "VN_UTC_OFFSET", 7))
            vn_now = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=vn_off)
            return {
                "in_session": False, "now": vn_now.strftime("%H:%M"),
                "session": "-", "seconds_to_open": 0, "open_at": None,
                "strategy": "(chưa chọn)",
                "label": "Chưa chọn phương pháp — hãy chọn phương pháp rồi bấm Start Bot.",
            }
        if getattr(strategy, "is_hedging", False) or getattr(strategy, "is_bigmouse", False):
            vn_off = int(getattr(config, "VN_UTC_OFFSET", 7))
            vn_now = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=vn_off)
            return {
                "in_session": True, "now": vn_now.strftime("%H:%M"),
                "session": "24/7", "seconds_to_open": 0, "open_at": None,
                "strategy": getattr(strategy, "name", "?"),
                "label": f"{getattr(strategy, 'name', '?')} chạy liên tục 24/7 (không giới hạn phiên)",
            }
        start, end = self._session_window(strategy)
        sname = getattr(strategy, "name", "?")
        server_now = self._server_now()                      # giờ broker (naive)
        off = self._broker_offset()                          # broker so với UTC
        vn_off = int(getattr(config, "VN_UTC_OFFSET", 7))
        vn = vn_off - off                                    # giờ broker -> giờ VN
        # "Bây giờ" luôn tính từ đồng hồ UTC thật -> đúng giờ VN
        vn_now = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=vn_off)

        # Phiên bao gồm cả giờ `end` (xét start <= h <= end) nên kết thúc thật là end+1 giờ
        vn_start = (start + vn) % 24
        vn_end = (end + 1 + vn) % 24
        label_range = f"{vn_start:02d}:00–{vn_end:02d}:00"
        h = server_now.hour
        if start <= h <= end:
            return {
                "in_session": True, "now": vn_now.strftime("%H:%M"),
                "session": label_range, "seconds_to_open": 0, "open_at": None,
                "strategy": sname,
                "label": f"Đang trong phiên vào lệnh ({label_range} giờ Việt Nam)",
            }
        target = server_now.replace(hour=start, minute=0, second=0, microsecond=0)
        if target <= server_now:
            target += timedelta(days=1)
        secs = int((target - server_now).total_seconds())
        target_vn = target + timedelta(hours=vn)
        hh, mm = divmod(secs // 60, 60)
        return {
            "in_session": False, "now": vn_now.strftime("%H:%M"),
            "session": label_range, "seconds_to_open": secs,
            "open_at": target_vn.strftime("%H:%M"),
            "strategy": sname,
            "label": f"Còn {hh}h{mm:02d}m nữa tới phiên vào lệnh ({vn_start:02d}:00 giờ Việt Nam)",
        }

    def _maybe_log_session(self, interval_sec: int = 300):
        """Ghi log trạng thái phiên định kỳ (mặc định mỗi 5 phút).
        Ghi ngay lập tức khi người dùng đổi PP để log khớp với PP đang chạy."""
        cur_key = strategy_manager.get_current_key()
        if cur_key == self._last_session_key and time.time() - self._last_session_log < interval_sec:
            return
        self._last_session_log = time.time()
        self._last_session_key = cur_key
        try:
            s = self.get_session_status()
            logger.info(f"⏳ PHIÊN  |  {s['strategy']}  |  {s['label']}  |  giờ VN {s['now']}")
        except Exception as e:
            logger.error(f"session log error: {e}")

    def _run_loop(self):
        if not mt5h.connect():
            self.status = "Error: MT5 Connect Failed"
            self.is_running = False
            return

        try:
            while self.is_running:
                # Chưa chọn phương pháp -> không làm gì, chờ người dùng chọn + bấm Start
                _cur = strategy_manager.get_current_strategy()
                if _cur is None:
                    self.status = "Running (chưa chọn PP)"
                    time.sleep(1)
                    continue
                self.status = "Running"
                # Chiến lược chạy liên tục (hedging / bigmouse) có vòng lặp riêng, poll theo giây
                if getattr(_cur, "is_hedging", False) or getattr(_cur, "is_bigmouse", False):
                    is_bm = getattr(_cur, "is_bigmouse", False)
                    try:
                        cur_key = strategy_manager.get_current_key()
                        if self._hedge_cleared_key != cur_key:
                            self._hedge_cleared_key = cur_key
                            self._cancel_other_pending()
                        self._maybe_log_session()
                        if is_bm:
                            bigmouse_engine.process(strategy_manager.get_current_strategy())
                            stop_req = bigmouse_engine.stop_requested
                        else:
                            hedging_engine.process(strategy_manager.get_current_strategy())
                            stop_req = hedging_engine.stop_requested
                        if stop_req:
                            logger.info(
                                f"[{'BIGMOUSE' if is_bm else 'HEDGE'}] Đã đạt mục tiêu & cấu hình dừng bot"
                            )
                            self.stop()
                            break
                    except Exception as e:
                        logger.error(f"Error in {'bigmouse' if is_bm else 'hedging'}: {e}")
                        time.sleep(5)
                    poll = getattr(
                        config,
                        "BIGMOUSE_POLL_SEC" if is_bm else "HEDGE_POLL_SEC",
                        1,
                    )
                    time.sleep(max(0.2, float(poll or 1)))
                    continue

                # 1. Chờ nến mới
                tf = self._active_timeframe()
                tf_seconds = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600}.get(tf, 60)
                now_sec = datetime.now(timezone.utc).timestamp()
                wait = tf_seconds - (now_sec % tf_seconds) + 0.5
                
                time.sleep(min(wait, 5)) 
                if wait > 5: continue

                # Sàn đóng -> không xử lý
                if not mt5h.is_market_open(config.SYMBOL):
                    time.sleep(10)
                    continue

                try:
                    self._maybe_log_session()
                    self._on_candle_tick()
                except Exception as e:
                    logger.error(f"Error in tick: {e}")
                    time.sleep(10)

        finally:
            mt5h.disconnect()

    # ------------------------------------------------------------------
    #  GUARD — phát hiện & đóng lệnh KHÔNG do bot mở
    # ------------------------------------------------------------------
    def _guard_loop(self):
        """Luồng riêng: quét lệnh ngoài bot định kỳ (GUARD_POLL_SEC)."""
        if not mt5h.connect():
            return
        while self.is_running:
            try:
                self._guard_external()
            except Exception as e:
                logger.error(f"Guard error: {e}")
            time.sleep(max(1.0, float(getattr(config, "GUARD_POLL_SEC", 2) or 2)))

    def _guard_external(self):
        if not getattr(config, "GUARD_EXTERNAL", True):
            return
        # Sàn đóng -> không thể đóng lệnh, bỏ qua (tránh log lỗi)
        if getattr(config, "GUARD_SYMBOL_ONLY", True) and not mt5h.is_market_open(config.SYMBOL):
            return
        known = set(strategy_manager.get_magics())
        symbol = config.SYMBOL if getattr(config, "GUARD_SYMBOL_ONLY", True) else None
        do_close = getattr(config, "GUARD_CLOSE_EXTERNAL", True)

        # --- Vị thế không thuộc bot ---
        for p in mt5h.get_all_positions(symbol):
            if p.magic in known:
                continue
            side = "BUY" if p.type == 0 else "SELL"
            logger.warning(
                f"🚨 LỆNH NGOÀI BOT  |  ticket={p.ticket}  |  {p.symbol} {side}  |  "
                f"vol={p.volume}  |  magic={p.magic}  |  open={p.price_open}  |  "
                f"time={datetime.fromtimestamp(p.time)}  |  cmt={p.comment!r}"
            )
            if do_close and mt5h.close_position(p, p.magic, "external guard"):
                logger.warning(f"🚫 ĐÃ ĐÓNG lệnh ngoài bot  |  ticket={p.ticket}")

        # --- Lệnh chờ không thuộc bot ---
        for o in mt5h.get_all_pending_orders(symbol):
            if o.magic in known:
                continue
            logger.warning(
                f"🚨 LỆNH CHỜ NGOÀI BOT  |  ticket={o.ticket}  |  {o.symbol}  |  "
                f"type={o.type}  |  vol={o.volume}  |  price={o.price_open}  |  magic={o.magic}"
            )
            if do_close and mt5h.cancel_pending_order(o.ticket):
                logger.warning(f"🚫 ĐÃ HỦY lệnh chờ ngoài bot  |  ticket={o.ticket}")

    # ------------------------------------------------------------------
    #  MANUAL SL/TP — ẩn SL/TP khỏi sàn, tự cắt bằng MARKET khi chạm điểm
    # ------------------------------------------------------------------
    def _manual_path(self) -> str:
        p = getattr(config, "MANUAL_SLTP_FILE", "logs/manual_sltp.json")
        if not os.path.isabs(p):
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            p = os.path.join(base, p)
        return p

    def _load_manual(self):
        try:
            path = self._manual_path()
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                self._manual = {int(k): v for k, v in data.items()}
        except Exception as e:
            logger.error(f"[MANUAL SLTP] load error: {e}")
            self._manual = {}

    def _save_manual(self):
        try:
            path = self._manual_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({str(k): v for k, v in self._manual.items()}, f, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[MANUAL SLTP] save error: {e}")

    def _set_manual(self, magic, side, sl, tp):
        self._manual[int(magic)] = {"side": side, "sl": float(sl), "tp": float(tp)}
        self._save_manual()

    def _clear_manual(self, magic):
        if self._manual.pop(int(magic), None) is not None:
            self._save_manual()

    def clear_manual(self):
        self._manual = {}
        self._save_manual()

    def _manual_sltp_loop(self):
        """Luồng riêng: kiểm tra SL/TP nội bộ và cắt market khi chạm.
        BUY quan sát BID; SELL quan sát ASK (= bid + spread)."""
        if not mt5h.connect():
            return
        while self.is_running:
            try:
                for s in strategy_manager.get_all_strategy_objects():
                    if not getattr(s, "manual_sltp", False):
                        continue
                    magic = int(getattr(s, "magic", 0))
                    meta = self._manual.get(magic)
                    pos = mt5h.get_open_position(config.SYMBOL, magic)
                    if pos is not None and meta:
                        self._manual_warned.discard(magic)
                        tick = mt5h.get_tick(config.SYMBOL)
                        if tick is None:
                            continue
                        price = tick.bid if pos.type == 0 else tick.ask
                        sl = float(meta.get("sl") or 0.0)
                        tp = float(meta.get("tp") or 0.0)
                        hit = why = None
                        if pos.type == 0:
                            if sl > 0 and price <= sl:
                                hit, why = True, "SL"
                            elif tp > 0 and price >= tp:
                                hit, why = True, "TP"
                        else:
                            if sl > 0 and price >= sl:
                                hit, why = True, "SL"
                            elif tp > 0 and price <= tp:
                                hit, why = True, "TP"
                        if hit:
                            logger.info(
                                f"🎯 [MANUAL {why}] {s.name} | ticket={pos.ticket} | price={price}"
                            )
                            # Chỉ xóa SL/TP nội bộ khi đóng THÀNH CÔNG (nếu lỗi giữ lại để thử tiếp)
                            if mt5h.close_position(pos, magic, f"manual {why}"):
                                self._clear_manual(magic)
                    elif pos is not None and not meta:
                        # Có vị thế nhưng KHÔNG có SL/TP nội bộ -> cảnh báo (không thể tự cắt)
                        if magic not in self._manual_warned:
                            self._manual_warned.add(magic)
                            logger.warning(
                                f"⚠️ [MANUAL SLTP] {s.name} đang có vị thế nhưng THIẾU SL/TP nội bộ "
                                f"(ticket={pos.ticket}) — cần đặt SL/TP thủ công hoặc đóng lệnh!"
                            )
                    elif pos is None:
                        if not mt5h.get_pending_orders(config.SYMBOL, magic) and magic in self._manual:
                            self._clear_manual(magic)
                        self._manual_warned.discard(magic)
            except Exception as e:
                logger.error(f"[MANUAL SLTP] loop error: {e}")
            time.sleep(max(0.3, float(getattr(config, "MANUAL_SLTP_POLL_SEC", 1) or 1)))

    def _cancel_other_pending(self):
        """Hủy mọi lệnh CHỜ của các chiến lược KHÁC khi vào PP chạy liên tục (hedging)."""
        cur = strategy_manager.get_current_strategy()
        for s in strategy_manager.get_all_strategy_objects():
            if s is cur:
                continue
            try:
                for order in mt5h.get_pending_orders(config.SYMBOL, s.magic):
                    mt5h.cancel_pending_order(order.ticket)
            except Exception as e:
                logger.error(f"cancel pending {getattr(s, 'name', '?')}: {e}")

    def _on_candle_tick(self):
        # Chỉ CHẠY chiến lược đang được chọn trên UI (các PP loại trừ nhau).
        active = strategy_manager.get_active_strategies()
        active_magics = {s.magic for s in active}
        for strategy in active:
            try:
                self._process_strategy(strategy)
            except Exception as e:
                logger.error(f"Error in strategy {getattr(strategy, 'name', '?')}: {e}")

        # Vẫn quản lý vị thế đang mở của PP không còn chạy (BE/partial), KHÔNG vào lệnh mới.
        # Đồng thời HỦY mọi lệnh chờ limit còn treo của PP không còn chạy.
        for strategy in strategy_manager.get_all_strategy_objects():
            if strategy.magic in active_magics:
                continue
            try:
                position = mt5h.get_open_position(config.SYMBOL, strategy.magic)
                if position is not None:
                    self._manage_position(strategy, strategy.magic, position)
                for order in mt5h.get_pending_orders(config.SYMBOL, strategy.magic):
                    mt5h.cancel_pending_order(order.ticket)
                self._pending.pop(strategy.magic, None)
            except Exception as e:
                logger.error(f"Error managing leftover {getattr(strategy, 'name', '?')}: {e}")

        # Dọn bộ nhớ theo dõi các ticket đã đóng
        open_tickets = {
            p.ticket for p in mt5h.get_open_positions(config.SYMBOL, strategy_manager.get_magics())
        }
        for t in list(self._pos_r.keys()):
            if t not in open_tickets:
                self._pos_r.pop(t, None)
                self._partial_done.discard(t)

    def _process_strategy(self, strategy):
        magic = getattr(strategy, "magic", 0)
        count = getattr(strategy, "history_bars", 200)
        tf = getattr(strategy, "timeframe", config.TIMEFRAME)
        df = mt5h.get_candles(config.SYMBOL, tf, count=count)
        if df is None or len(df) < 50:
            return
        df = strategy.calculate_indicators(df)
        position = mt5h.get_open_position(config.SYMBOL, magic)

        # 1) Quản lý lệnh đang mở (partial + BE)
        if position is not None:
            self._manage_position(strategy, magic, position)
            self._pending.pop(magic, None)   # đã có lệnh → hủy lệnh chờ
        else:
            # 2) Lệnh chờ limit (entry hồi giá) — nếu chiến lược dùng
            self._process_pending(strategy, magic, df)

        # 3) Vào lệnh market cho chiến lược dùng tín hiệu thường (khi KHÔNG có lệnh chờ treo)
        if position is None and not mt5h.get_pending_orders(config.SYMBOL, magic):
            signal = strategy.check_signal(df)
            if signal:
                info = mt5h.get_symbol_info(config.SYMBOL)
                tick = mt5h.get_tick(config.SYMBOL)
                if not info or not tick:
                    return
                price = tick.ask if signal == "BUY" else tick.bid
                sl, tp = strategy.get_sl_tp(df, price, info.digits, signal)
                if sl and tp:
                    lot = getattr(strategy, "lot", config.FIXED_LOT)
                    comment = getattr(strategy, "comment", "Bot")
                    manual = getattr(strategy, "manual_sltp", False)
                    send_sl, send_tp = (0.0, 0.0) if manual else (sl, tp)
                    logger.info(f"⚡ EXECUTE {signal} | Strategy: {strategy.name} | Price: {price} | SL: {sl} | TP: {tp}")
                    ticket = mt5h.open_position(config.SYMBOL, signal, lot, send_sl, send_tp, magic, comment)
                    if manual and ticket:
                        self._set_manual(magic, signal, sl, tp)

    def _manage_position(self, strategy, magic, position):
        #   1) Chốt một phần (partial TP) khi đạt partial_at_r × R
        #   2) Dời SL về hòa vốn (BE) khi đạt be_move_at_r × R
        be_r = getattr(strategy, "be_move_at_r", 0)
        part_r = getattr(strategy, "partial_at_r", 0)
        part_f = getattr(strategy, "partial_frac", 0)

        if not position.sl:
            return
        info = mt5h.get_symbol_info(config.SYMBOL)
        tick = mt5h.get_tick(config.SYMBOL)
        if not info or not tick:
            return

        entry = position.price_open
        stop = position.sl
        digits = info.digits
        not_yet_be = round(stop, digits) != round(entry, digits)

        # Ghi nhớ R ban đầu khi SL còn ở mức gốc (chưa dời BE)
        if position.ticket not in self._pos_r and not_yet_be:
            self._pos_r[position.ticket] = abs(entry - stop)
        R = self._pos_r.get(position.ticket, 0.0)

        price = tick.bid if position.type == 0 else tick.ask

        # 1) Chốt một phần (chỉ 1 lần cho mỗi ticket)
        if part_f > 0 and part_r > 0 and R > 0 and position.ticket not in self._partial_done:
            hit_part = (
                (position.type == 0 and price >= entry + part_r * R) or
                (position.type == 1 and price <= entry - part_r * R)
            )
            if hit_part:
                if mt5h.split_volume(position.volume, part_f, info) <= 0:
                    self._partial_done.add(position.ticket)
                elif mt5h.close_position_partial(position, part_f, magic, "partial TP"):
                    self._partial_done.add(position.ticket)

        # 2) Dời SL về hòa vốn
        if be_r > 0 and R > 0 and not_yet_be:
            hit_be = (
                (position.type == 0 and price >= entry + be_r * R) or
                (position.type == 1 and price <= entry - be_r * R)
            )
            if hit_be:
                logger.info(
                    f"⚡ BE-MOVE  |  ticket={position.ticket}  |  "
                    f"SL {stop:.{digits}f} → {entry:.{digits}f}"
                )
                mt5h.modify_position(
                    config.SYMBOL, position.ticket,
                    sl=round(entry, digits), tp=position.tp,
                )

    def _process_pending(self, strategy, magic, df):
        """Quản lý lệnh CHỜ LIMIT THẬT đặt trên MT5.

        - Nếu đang có lệnh chờ trên sàn: chỉ hủy khi hết hạn hoặc quá killzone.
        - Nếu không còn lệnh chờ: hỏi chiến lược setup mới rồi ĐẶT LỆNH LIMIT thật.
        Sàn tự khớp trong nến → khớp đúng như backtest, không cần bot chờ giá.
        """
        orders = mt5h.get_pending_orders(config.SYMBOL, magic)

        # --- Đang có lệnh chờ thật trên sàn ---
        if orders:
            meta = self._pending.get(magic)
            kz_end = getattr(strategy, "kz_end", None)
            broker_hour = self._server_now().hour
            expired = bool(meta and time.time() > meta.get("expire_ts", 0))
            if kz_end is not None and broker_hour >= (kz_end + 1) % 24:
                expired = True
            if expired:
                for o in orders:
                    mt5h.cancel_pending_order(o.ticket)
                self._pending.pop(magic, None)
                self._clear_manual(magic)
                logger.info(f"⏹️ HỦY LỆNH CHỜ | {strategy.name} | hết hạn / quá killzone")
            return

        # --- Không còn lệnh chờ: setup cũ đã khớp hoặc bị hủy ---
        self._pending.pop(magic, None)

        setup = strategy.get_pending_setup(df)
        if not setup:
            return

        info = mt5h.get_symbol_info(config.SYMBOL)
        tick = mt5h.get_tick(config.SYMBOL)
        if not info or not tick:
            return

        typ = setup["type"]
        level = float(setup["level"])
        sl = float(setup["sl"])
        tp = float(setup["tp"])
        wait_min = float(setup.get("wait_min", 60))

        # Khớp đúng như backtest: BUY limit tại level+spread (ask), SELL tại level (bid)
        spread = round(tick.ask - tick.bid, info.digits)
        price = round(level + spread, info.digits) if typ == "BUY" else round(level, info.digits)

        lot = getattr(strategy, "lot", config.FIXED_LOT)
        comment = getattr(strategy, "comment", "Bot")
        manual = getattr(strategy, "manual_sltp", False)
        # Ẩn SL/TP khỏi sàn: không gửi kèm lệnh chờ; bot tự quản lý nội bộ
        send_sl, send_tp = (0.0, 0.0) if manual else (sl, tp)
        ticket = mt5h.place_limit_order(
            config.SYMBOL, typ, lot, price, send_sl, send_tp, magic, comment,
            expire_minutes=int(wait_min),
        )
        if ticket:
            self._pending[magic] = {
                "ticket": ticket,
                "expire_ts": time.time() + wait_min * 60.0,
            }
            if manual:
                self._set_manual(magic, typ, sl, tp)
            logger.info(
                f"📌 LỆNH CHỜ {typ} | {strategy.name} | "
                f"level={price:.2f} | SL={sl:.2f} | TP={tp:.2f} | ticket={ticket}"
                + ("  [ẩn SL/TP trên sàn]" if manual else "")
            )

bot_engine = BotEngine()
