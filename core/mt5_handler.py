"""
mt5_handler.py
Wrapper cho MetaTrader5 API: connect, lấy dữ liệu, đặt lệnh, đóng lệnh.
"""

import MetaTrader5 as mt5
import pandas as pd
import logging

logger = logging.getLogger(__name__)

# Map string timeframe → mt5 constant
_TF_MAP = {
    "M1":  mt5.TIMEFRAME_M1,
    "M5":  mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1":  mt5.TIMEFRAME_H1,
    "H4":  mt5.TIMEFRAME_H4,
    "D1":  mt5.TIMEFRAME_D1,
}


# ────────────────────────────────────────────────
#  Connection
# ────────────────────────────────────────────────
_MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"

def connect() -> bool:
    if not mt5.initialize(path=_MT5_PATH):
        logger.error(f"MT5 initialize() failed → {mt5.last_error()}")
        return False

    acc = mt5.account_info()
    if acc:
        logger.info(
            f"Connected  |  Account: {acc.login}  |  "
            f"Balance: {acc.balance:.2f} {acc.currency}  |  "
            f"Broker: {acc.company}"
        )
    return True


def disconnect():
    mt5.shutdown()
    logger.info("MT5 disconnected.")


# ────────────────────────────────────────────────
#  Market Data
# ────────────────────────────────────────────────
def get_candles(symbol: str, timeframe_str: str, count: int = 300) -> pd.DataFrame | None:
    tf = _TF_MAP.get(timeframe_str.upper())
    if tf is None:
        logger.error(f"Unknown timeframe: {timeframe_str}")
        return None

    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    if rates is None or len(rates) == 0:
        logger.error(f"copy_rates_from_pos failed for {symbol} {timeframe_str} → {mt5.last_error()}")
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.rename(columns={"open": "open", "high": "high",
                        "low": "low", "close": "close",
                        "tick_volume": "volume"}, inplace=True)
    return df


def get_account_balance() -> float:
    acc = mt5.account_info()
    return acc.balance if acc else 0.0


def get_symbol_info(symbol: str):
    info = mt5.symbol_info(symbol)
    if info is None:
        logger.error(f"Symbol not found: {symbol}")
    # Ensure symbol is visible in Market Watch
    elif not info.visible:
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
    return info


def get_tick(symbol: str):
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logger.error(f"Cannot get tick for {symbol}")
    return tick


# ────────────────────────────────────────────────
#  Position Management
# ────────────────────────────────────────────────
def get_open_position(symbol: str, magic: int):
    """Trả về Position đầu tiên của bot, hoặc None."""
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return None
    for p in positions:
        if p.magic == magic:
            return p
    return None


# ────────────────────────────────────────────────
#  Lot Size Calculation
# ────────────────────────────────────────────────
def calc_lot_by_risk(symbol: str, sl_distance: float, balance: float, risk_pct: float) -> float:
    """
    Tính lot size theo % risk.
    sl_distance: khoảng cách từ entry đến SL (tính bằng price, không phải pip).
    """
    info = get_symbol_info(symbol)
    if info is None or sl_distance <= 0:
        return 0.0

    # Giá trị 1 pip (1 point) cho 1 lot
    tick_value = info.trade_tick_value   # VD: 0.01 USD/tick cho XAUUSD
    tick_size  = info.trade_tick_size    # VD: 0.01
    point      = info.point              # VD: 0.01

    # Số ticks trong SL distance
    ticks_in_sl = sl_distance / tick_size

    risk_amount = balance * (risk_pct / 100.0)

    if tick_value == 0 or ticks_in_sl == 0:
        return 0.0

    lot = risk_amount / (ticks_in_sl * tick_value)
    lot = round(lot, 2)
    lot = max(info.volume_min, min(lot, info.volume_max))
    return lot


# ────────────────────────────────────────────────
#  Open / Close Orders
# ────────────────────────────────────────────────
def open_position(
    symbol: str,
    order_type: str,   # "BUY" or "SELL"
    lot: float,
    sl: float,
    tp: float,
    magic: int,
    comment: str,
) -> bool:
    info = get_symbol_info(symbol)
    if info is None:
        return False

    tick = get_tick(symbol)
    if tick is None:
        return False

    if order_type == "BUY":
        price     = tick.ask
        mt5_type  = mt5.ORDER_TYPE_BUY
    else:
        price    = tick.bid
        mt5_type = mt5.ORDER_TYPE_SELL

    digits = info.digits
    sl = round(sl, digits)
    tp = round(tp, digits)

    request = {
        "action":      mt5.TRADE_ACTION_DEAL,
        "symbol":      symbol,
        "volume":      lot,
        "type":        mt5_type,
        "price":       price,
        "sl":          sl,
        "tp":          tp,
        "magic":       magic,
        "comment":     comment,
        "type_time":   mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        logger.error(
            f"open_position FAILED  |  retcode={result.retcode}  |  {result.comment}"
        )
        return False

    logger.info(
        f"{'🟢 BUY' if order_type == 'BUY' else '🔴 SELL'} OPENED  |  "
        f"Ticket={result.order}  |  Price={price:.5f}  |  "
        f"SL={sl:.5f}  |  TP={tp:.5f}  |  Lot={lot}"
    )
    return True


def close_position(position, magic: int, comment: str = "close") -> bool:
    tick = get_tick(position.symbol)
    if tick is None:
        return False

    if position.type == mt5.POSITION_TYPE_BUY:
        price    = tick.bid
        mt5_type = mt5.ORDER_TYPE_SELL
    else:
        price    = tick.ask
        mt5_type = mt5.ORDER_TYPE_BUY

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       position.symbol,
        "volume":       position.volume,
        "type":         mt5_type,
        "position":     position.ticket,
        "price":        price,
        "magic":        magic,
        "comment":      comment,
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        logger.error(
            f"close_position FAILED  |  Ticket={position.ticket}  |  "
            f"retcode={result.retcode}  |  {result.comment}"
        )
        return False

    logger.info(f"✅ CLOSED  |  Ticket={position.ticket}  |  Profit={position.profit:.2f}")
    return True
