"""
core/bot_engine.py
Hệ điều hành của Bot (Trading Loop).
"""
import time
import threading
import logging
from datetime import datetime, timezone, timedelta

import config
from . import mt5_handler as mt5h
from .hedging_engine import hedging_engine
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
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            logger.info("Bot Engine STARTED")

    def stop(self):
        with self._lock:
            self.is_running = False
            self.status = "Stopped"
            t = self._thread
            if t is not None and t.is_alive() and t is not threading.current_thread():
                t.join(timeout=10)
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
        """Khung thời gian của chiến lược đang chọn (SMC chạy M5, còn lại M1)."""
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
        return getattr(config, "TM_SESSION", (12, 21))

    def get_session_status(self) -> dict:
        """Trạng thái phiên + đếm ngược, hiển thị theo GIỜ VIỆT NAM (UTC+7).
        Phiên định nghĩa theo giờ broker của CHIẾN LƯỢC ĐANG CHỌN; ở đây quy đổi để hiển thị."""
        strategy = strategy_manager.get_current_strategy()
        if getattr(strategy, "is_hedging", False):
            vn_off = int(getattr(config, "VN_UTC_OFFSET", 7))
            vn_now = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=vn_off)
            return {
                "in_session": True, "now": vn_now.strftime("%H:%M"),
                "session": "24/7", "seconds_to_open": 0, "open_at": None,
                "strategy": getattr(strategy, "name", "?"),
                "label": "Hedging chạy liên tục 24/7 (không giới hạn phiên)",
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
                # Chiến lược Hedging chạy vòng lặp riêng, poll liên tục theo giây
                if getattr(strategy_manager.get_current_strategy(), "is_hedging", False):
                    try:
                        cur_key = strategy_manager.get_current_key()
                        if self._hedge_cleared_key != cur_key:
                            self._hedge_cleared_key = cur_key
                            self._cancel_other_pending()
                        self._maybe_log_session()
                        hedging_engine.process(strategy_manager.get_current_strategy())
                    except Exception as e:
                        logger.error(f"Error in hedging: {e}")
                        time.sleep(5)
                    time.sleep(max(1, int(getattr(config, "HEDGE_POLL_SEC", 1))))
                    continue

                # 1. Chờ nến mới
                tf = self._active_timeframe()
                tf_seconds = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600}.get(tf, 60)
                now_sec = datetime.now(timezone.utc).timestamp()
                wait = tf_seconds - (now_sec % tf_seconds) + 0.5
                
                time.sleep(min(wait, 5)) 
                if wait > 5: continue

                try:
                    self._maybe_log_session()
                    self._on_candle_tick()
                except Exception as e:
                    logger.error(f"Error in tick: {e}")
                    time.sleep(10)

        finally:
            mt5h.disconnect()

    def _cancel_other_pending(self):
        """Hủy mọi lệnh CHỜ của các chiến lược KHÁC khi vào hedging."""
        for s in strategy_manager.get_all_strategy_objects():
            if getattr(s, "is_hedging", False):
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
        magic = getattr(strategy, "magic", config.MAGIC_TM)
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
                    comment = getattr(strategy, "comment", config.ORDER_COMMENT)
                    logger.info(f"⚡ EXECUTE {signal} | Strategy: {strategy.name} | Price: {price} | SL: {sl} | TP: {tp}")
                    mt5h.open_position(config.SYMBOL, signal, lot, sl, tp, magic, comment)

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
        comment = getattr(strategy, "comment", config.ORDER_COMMENT)
        ticket = mt5h.place_limit_order(
            config.SYMBOL, typ, lot, price, sl, tp, magic, comment,
            expire_minutes=int(wait_min),
        )
        if ticket:
            self._pending[magic] = {
                "ticket": ticket,
                "expire_ts": time.time() + wait_min * 60.0,
            }
            logger.info(
                f"📌 LỆNH CHỜ {typ} | {strategy.name} | "
                f"level={price:.2f} | SL={sl:.2f} | TP={tp:.2f} | ticket={ticket}"
            )

bot_engine = BotEngine()
