"""
main.py  –  XAUUSD M5 SuperTrend + EMA100 Trading Bot
──────────────────────────────────────────────────────
Entry  : SuperTrend flip + EMA100 filter (tại nến đóng)
Exit   : TP 1:1  hoặc  SuperTrend flip ngược chiều
Position : tối đa 1 lệnh
"""

import time
import logging
from datetime import datetime, timezone

import config
import mt5_handler as mt5h
from indicators import calculate_supertrend, calculate_ema


# ─────────────────────────────────────────────────────────────
#  Logging setup
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
#  Timeframe helpers
# ─────────────────────────────────────────────────────────────
_TF_SECONDS = {
    "M1": 60, "M5": 300, "M15": 900,
    "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400,
}


def _seconds_to_next_close(tf_str: str) -> float:
    """Số giây còn lại đến khi nến hiện tại đóng (tính theo UTC)."""
    tf_sec  = _TF_SECONDS.get(tf_str.upper(), 300)
    now_sec = datetime.now(timezone.utc).timestamp()
    elapsed = now_sec % tf_sec
    wait    = tf_sec - elapsed
    # Nếu còn < 1s → nến vừa đóng, đợi nến tiếp theo
    if wait < 1:
        wait += tf_sec
    return wait


def _in_trading_hours() -> bool:
    """Kiểm tra giờ giao dịch (UTC). Bỏ qua nếu TRADE_HOURS rỗng."""
    if not config.TRADE_HOURS:
        return True
    utc_hour = datetime.now(timezone.utc).hour
    for (start, end) in config.TRADE_HOURS:
        if start <= utc_hour < end:
            return True
    return False


# ─────────────────────────────────────────────────────────────
#  Lot size decision
# ─────────────────────────────────────────────────────────────
def _decide_lot(symbol: str, sl_distance: float) -> float:
    if config.LOT_MODE == "FIXED":
        return config.FIXED_LOT

    balance = mt5h.get_account_balance()
    lot = mt5h.calc_lot_by_risk(
        symbol, sl_distance, balance, config.RISK_PERCENT
    )
    if lot <= 0:
        logger.warning("Risk-based lot calc returned 0 → fallback to FIXED_LOT")
        return config.FIXED_LOT
    return lot


# ─────────────────────────────────────────────────────────────
#  Core candle-close logic
# ─────────────────────────────────────────────────────────────
def _on_candle_close():
    """Gọi sau mỗi nến đóng. Phân tích tín hiệu và hành động."""

    # --- Lấy dữ liệu ---
    df = mt5h.get_candles(config.SYMBOL, config.TIMEFRAME, count=350)
    if df is None or len(df) < config.EMA_PERIOD + 10:
        logger.warning("Không đủ dữ liệu nến → bỏ qua.")
        return

    # --- Tính chỉ báo ---
    df = calculate_supertrend(df, config.ST_PERIOD, config.ST_MULTIPLIER)
    df = calculate_ema(df, config.EMA_PERIOD)
    ema_col = f"ema{config.EMA_PERIOD}"

    # --- Lấy 2 nến đã đóng gần nhất ---
    # df.iloc[-1] = nến đang hình thành (chưa đóng) → bỏ qua
    # df.iloc[-2] = nến VỪA đóng (nến tín hiệu)
    # df.iloc[-3] = nến trước đó
    signal_candle = df.iloc[-2]
    prev_candle   = df.iloc[-3]

    cur_dir  = int(signal_candle["st_dir"])
    prev_dir = int(prev_candle["st_dir"])

    close_price = signal_candle["close"]
    ema_val     = signal_candle[ema_col]
    st_val      = signal_candle["supertrend"]   # = SL line

    logger.info(
        f"[{signal_candle['time']}]  "
        f"Close={close_price:.2f}  |  "
        f"ST={st_val:.2f} ({'Bull' if cur_dir == 1 else 'Bear'})  |  "
        f"EMA{config.EMA_PERIOD}={ema_val:.2f}"
    )

    # --- Phát hiện flip ---
    flip_up   = (prev_dir == -1 and cur_dir == 1)   # Bear → Bull
    flip_down = (prev_dir ==  1 and cur_dir == -1)  # Bull → Bear

    # --- Lấy vị thế hiện tại ---
    position = mt5h.get_open_position(config.SYMBOL, config.MAGIC_NUMBER)

    # ══════════════════════════════════════
    #  EXIT: đóng lệnh khi ST flip ngược
    # ══════════════════════════════════════
    if position is not None:
        is_buy  = (position.type == 0)   # mt5.POSITION_TYPE_BUY = 0
        is_sell = (position.type == 1)

        if is_buy and flip_down:
            logger.info("ST flip DOWN → Đóng lệnh BUY.")
            mt5h.close_position(position, config.MAGIC_NUMBER, "flip_exit")
            position = None

        elif is_sell and flip_up:
            logger.info("ST flip UP → Đóng lệnh SELL.")
            mt5h.close_position(position, config.MAGIC_NUMBER, "flip_exit")
            position = None

    # ══════════════════════════════════════
    #  ENTRY: mở lệnh mới nếu có tín hiệu
    # ══════════════════════════════════════
    if position is None:
        if not _in_trading_hours():
            logger.info("Ngoài giờ trading → bỏ qua entry.")
            return

        info = mt5h.get_symbol_info(config.SYMBOL)
        if info is None:
            return
        digits = info.digits

        # ── BUY ──────────────────────────────
        if flip_up and close_price > ema_val:
            sl        = round(st_val, digits)          # ST lower band
            tick      = mt5h.get_tick(config.SYMBOL)
            if tick is None:
                return
            entry     = tick.ask
            distance  = entry - sl
            if distance <= 0:
                logger.warning(f"BUY: distance <= 0 (entry={entry}, sl={sl}) → bỏ qua.")
                return
            tp  = round(entry + distance, digits)
            lot = _decide_lot(config.SYMBOL, distance)

            logger.info(f"🟢 BUY SIGNAL  |  Entry≈{entry:.2f}  SL={sl:.2f}  TP={tp:.2f}  Lot={lot}")
            mt5h.open_position(
                config.SYMBOL, "BUY", lot, sl, tp,
                config.MAGIC_NUMBER, config.ORDER_COMMENT
            )

        # ── SELL ─────────────────────────────
        elif flip_down and close_price < ema_val:
            sl        = round(st_val, digits)          # ST upper band
            tick      = mt5h.get_tick(config.SYMBOL)
            if tick is None:
                return
            entry     = tick.bid
            distance  = sl - entry
            if distance <= 0:
                logger.warning(f"SELL: distance <= 0 (entry={entry}, sl={sl}) → bỏ qua.")
                return
            tp  = round(entry - distance, digits)
            lot = _decide_lot(config.SYMBOL, distance)

            logger.info(f"🔴 SELL SIGNAL  |  Entry≈{entry:.2f}  SL={sl:.2f}  TP={tp:.2f}  Lot={lot}")
            mt5h.open_position(
                config.SYMBOL, "SELL", lot, sl, tp,
                config.MAGIC_NUMBER, config.ORDER_COMMENT
            )

        else:
            reason = []
            if not flip_up and not flip_down:
                reason.append("no ST flip")
            elif flip_up and close_price <= ema_val:
                reason.append("flip UP nhưng Close ≤ EMA → filtered")
            elif flip_down and close_price >= ema_val:
                reason.append("flip DOWN nhưng Close ≥ EMA → filtered")
            logger.info(f"Không có tín hiệu  ({', '.join(reason)})")

    else:
        pos_type = "BUY" if position.type == 0 else "SELL"
        logger.info(
            f"Đang giữ lệnh {pos_type}  |  "
            f"Ticket={position.ticket}  |  Profit={position.profit:.2f}"
        )


# ─────────────────────────────────────────────────────────────
#  Main loop
# ─────────────────────────────────────────────────────────────
def run():
    logger.info("=" * 60)
    logger.info("   XAUUSD M5  SuperTrend(10,3) + EMA100  Bot  START")
    logger.info(f"   LOT_MODE={config.LOT_MODE}  FIXED_LOT={config.FIXED_LOT}  RISK={config.RISK_PERCENT}%")
    logger.info("=" * 60)

    if not mt5h.connect():
        logger.critical("Không kết nối được MT5. Thoát.")
        return

    try:
        while True:
            wait = _seconds_to_next_close(config.TIMEFRAME)
            logger.info(f"⏳ Chờ {wait:.1f}s đến khi nến {config.TIMEFRAME} đóng...")
            time.sleep(wait + 0.5)   # +0.5s buffer để broker cập nhật

            try:
                _on_candle_close()
            except Exception as e:
                logger.error(f"Lỗi trong _on_candle_close: {e}", exc_info=True)
                time.sleep(10)

    except KeyboardInterrupt:
        logger.info("Bot dừng bởi người dùng (Ctrl+C).")

    finally:
        mt5h.disconnect()


if __name__ == "__main__":
    run()
