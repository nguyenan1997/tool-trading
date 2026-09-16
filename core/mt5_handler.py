"""
mt5_handler.py
Wrapper cho MetaTrader5 API: connect, lấy dữ liệu, đặt lệnh, đóng lệnh.
"""

import MetaTrader5 as mt5
import pandas as pd
import logging
import math

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
    # Kiểm tra xem MT5 đã được khởi tạo chưa
    terminal_info = mt5.terminal_info()
    if terminal_info is not None:
        return True # Đã kết nối
        
    # Nếu chưa, thực hiện initialize
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
    # Đảm bảo luôn kết nối trước khi lấy dữ liệu
    if not connect():
        return None
        
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

def get_candles_range(symbol: str, timeframe_str: str, date_from, date_to) -> pd.DataFrame | None:
    # Đảm bảo luôn kết nối
    if not connect():
        return None
        
    tf = _TF_MAP.get(timeframe_str.upper())
    if tf is None:
        logger.error(f"Unknown timeframe: {timeframe_str}")
        return None

    rates = mt5.copy_rates_range(symbol, tf, date_from, date_to)
    if rates is None or len(rates) == 0:
        logger.error(f"copy_rates_range failed for {symbol} {timeframe_str} from {date_from} → {mt5.last_error()}")
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.rename(columns={"open": "open", "high": "high",
                        "low": "low", "close": "close",
                        "tick_volume": "volume"}, inplace=True)
    return df


def get_account_balance() -> float:
    if not connect():
        return 0.0
    acc = mt5.account_info()
    return acc.balance if acc else 0.0


def get_symbol_info(symbol: str):
    if not connect():
        return None
    info = mt5.symbol_info(symbol)
    if info is None:
        logger.error(f"Symbol not found: {symbol}")
    # Ensure symbol is visible in Market Watch
    elif not info.visible:
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
    return info


def get_tick(symbol: str):
    if not connect():
        return None
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logger.error(f"Cannot get tick for {symbol}")
    return tick


# ────────────────────────────────────────────────
#  Position Management
# ────────────────────────────────────────────────
def get_open_position(symbol: str, magic: int):
    """Trả về Position đầu tiên của bot (theo magic), hoặc None."""
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return None
    for p in positions:
        if p.magic == magic:
            return p
    return None


def get_open_positions(symbol: str, magics) -> list:
    """Trả về tất cả Position của bot có magic nằm trong danh sách `magics`."""
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return []
    magics = set(magics)
    return [p for p in positions if p.magic in magics]


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
#  Filling mode (tránh lỗi 10030 Unsupported filling mode)
# ────────────────────────────────────────────────
_FILLING_ORDER = (mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN)

# Retcode liên quan tới trượt giá / giá đổi
_SLIPPAGE_RETCODES = {
    10004,  # TRADE_RETCODE_REQUOTE
    10020,  # TRADE_RETCODE_PRICE_CHANGED
    10021,  # TRADE_RETCODE_PRICE_OFF
}


def _supported_filling(info) -> int:
    """Chọn chế độ khớp lệnh mà symbol hỗ trợ (theo symbol_info.filling_mode)."""
    fm = getattr(info, "filling_mode", 0) or 0
    # MT5 Python không export SYMBOL_FILLING_*; giá trị chuẩn: FOK = 1, IOC = 2
    if fm & getattr(mt5, "SYMBOL_FILLING_IOC", 2):
        return mt5.ORDER_FILLING_IOC
    if fm & getattr(mt5, "SYMBOL_FILLING_FOK", 1):
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


def _order_send(request: dict, info=None):
    """Gửi lệnh; nếu bị lỗi 10030 (Unsupported filling mode) thì tự thử mode khác."""
    if info is not None:
        request["type_filling"] = _supported_filling(info)
    result = mt5.order_send(request)
    if result is None:
        return result
    if result.retcode == mt5.TRADE_RETCODE_INVALID_FILL:
        for fm in _FILLING_ORDER:
            if request.get("type_filling") == fm:
                continue
            request["type_filling"] = fm
            result = mt5.order_send(request)
            if result is None or result.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
                break
    return result


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
    max_slippage_points: int = None,
) -> bool:
    info = get_symbol_info(symbol)
    if info is None:
        return False

    tick = get_tick(symbol)
    if tick is None:
        return False

    import config
    if max_slippage_points is None:
        max_slippage_points = int(getattr(config, "MAX_SLIPPAGE_POINTS", 0))

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
    if max_slippage_points > 0:
        request["deviation"] = max_slippage_points  # giới hạn trượt giá (points)

    result = _order_send(request, info)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        code = result.retcode if result else "None"
        msg = result.comment if result else "order_send returned None"
        if code in _SLIPPAGE_RETCODES:
            logger.warning(f"⚠️ BỎ LỆNH do trượt giá  |  retcode={code}  |  {msg}")
        else:
            logger.error(f"open_position FAILED  |  retcode={code}  |  {msg}")
        return False

    fill = float(getattr(result, "price", 0) or price)
    slip_points = abs(fill - price) / info.point

    # Broker có thể bỏ qua `deviation` -> khớp lệch quá ngưỡng thì đóng ngay, coi như không vào
    if max_slippage_points > 0 and slip_points > max_slippage_points:
        logger.warning(
            f"⚠️ TRƯỢT GIÁ {slip_points:.0f} points (> {max_slippage_points})  |  "
            f"yêu cầu {price:.{digits}f} -> khớp {fill:.{digits}f}  |  ĐÓNG LỆNH, coi như không vào"
        )
        pos = get_open_position(symbol, magic)
        if pos is not None:
            close_position(pos, magic, "slippage cancel")
        return False

    logger.info(
        f"{'🟢 BUY' if order_type == 'BUY' else '🔴 SELL'} OPENED  |  "
        f"Ticket={result.order}  |  Fill={fill:.5f}  |  Yêu cầu={price:.5f}  |  "
        f"SL={sl:.5f}  |  TP={tp:.5f}  |  Lot={lot}  |  trượt={slip_points:.0f}pts"
    )
    return True


def close_position(position, magic: int, comment: str = "close") -> bool:
    tick = get_tick(position.symbol)
    if tick is None:
        return False
    info = get_symbol_info(position.symbol)

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

    result = _order_send(request, info)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        code = result.retcode if result else "None"
        msg = result.comment if result else "order_send returned None"
        logger.error(
            f"close_position FAILED  |  Ticket={position.ticket}  |  "
            f"retcode={code}  |  {msg}"
        )
        return False

    logger.info(f"✅ CLOSED  |  Ticket={position.ticket}  |  Profit={position.profit:.2f}")
    return True


# ────────────────────────────────────────────────
#  Partial Close (chốt lời từng phần)
# ────────────────────────────────────────────────
def split_volume(volume: float, frac: float, info) -> float:
    """
    Tính khối lượng cần đóng = volume × frac, làm tròn XUỐNG theo volume_step.
    Trả về 0.0 nếu không thể chia hợp lệ (phần đóng hoặc phần còn lại < volume_min).
    """
    if info is None or frac <= 0 or frac >= 1:
        return 0.0
    step = info.volume_step or 0.01
    vol = round(math.floor((volume * frac) / step + 1e-9) * step, 2)
    remaining = round(volume - vol, 2)
    if vol < info.volume_min - 1e-9 or remaining < info.volume_min - 1e-9:
        return 0.0
    return vol


def close_position_partial(position, frac: float, magic: int = 0, comment: str = "partial") -> bool:
    """
    Đóng `frac` (0..1) khối lượng của vị thế. Phần còn lại giữ nguyên.
    Trả về False nếu khối lượng quá nhỏ để chia (theo volume_min/volume_step) hoặc lỗi.
    """
    info = get_symbol_info(position.symbol)
    if info is None:
        return False

    vol = split_volume(position.volume, frac, info)
    if vol <= 0:
        logger.info(
            f"partial close BỎ QUA  |  volume={position.volume} quá nhỏ để chia "
            f"(step={info.volume_step}, min={info.volume_min})"
        )
        return False

    tick = get_tick(position.symbol)
    if tick is None:
        return False

    if position.type == mt5.POSITION_TYPE_BUY:
        price = tick.bid
        mt5_type = mt5.ORDER_TYPE_SELL
    else:
        price = tick.ask
        mt5_type = mt5.ORDER_TYPE_BUY

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       position.symbol,
        "volume":       vol,
        "type":         mt5_type,
        "position":     position.ticket,
        "price":        price,
        "magic":        magic,
        "comment":      comment,
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = _order_send(request, info)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        code = result.retcode if result else "None"
        msg = result.comment if result else "order_send returned None"
        logger.error(
            f"partial close FAILED  |  Ticket={position.ticket}  |  "
            f"retcode={code}  |  {msg}"
        )
        return False

    logger.info(
        f"✂️ PARTIAL CLOSE  |  Ticket={position.ticket}  |  "
        f"đóng {vol} lot, còn {remaining} lot"
    )
    return True


# ────────────────────────────────────────────────
#  Modify Order (dời SL/TP — dùng cho BE-move)
# ────────────────────────────────────────────────
def modify_position(symbol: str, ticket: int, sl=None, tp=None, magic: int = None) -> bool:
    request = {
        "action":   mt5.TRADE_ACTION_SLTP,
        "symbol":   symbol,
        "position": ticket,
    }
    if sl is not None:
        request["sl"] = sl
    if tp is not None:
        request["tp"] = tp
    if magic is not None:
        request["magic"] = magic

    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        logger.error(
            f"modify_position FAILED  |  ticket={ticket}  |  "
            f"retcode={result.retcode}  |  {result.comment}"
        )
        return False
    logger.info(f"✅ MODIFIED  |  ticket={ticket}  |  SL={sl}  |  TP={tp}")
    return True
