"""
strategies/trend_momentum.py
Chiến lược Trend Momentum (đa khung thời gian).
- Trend filter: EMA200 H1 + ADX(14) H1.
- Entry M1: nến đóng phá đỉnh/đáy 20 nến (Donchian) + RSI M1 cùng chiều
  + chỉ trong phiên London + New York (UTC).
- Exit: SL = TM_SL_ATR x ATR(M15); TP = TM_TP_R x SL distance; BE-move khi +1R
  (BE-move do BotEngine thực thi qua modify_position).
"""
import numpy as np
import pandas as pd

import config
from .base import BaseStrategy


def _atr(frame: pd.DataFrame, period: int) -> pd.Series:
    h, l, c = frame["high"], frame["low"], frame["close"]
    tr = pd.concat(
        [h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _adx(frame: pd.DataFrame, period: int) -> pd.Series:
    h, l, c = frame["high"], frame["low"], frame["close"]
    tr = pd.concat(
        [h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1
    ).max(axis=1)
    up = h.diff()
    dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr_s = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=frame.index).ewm(alpha=1 / period, adjust=False).mean() / atr_s
    minus_di = 100 * pd.Series(minus_dm, index=frame.index).ewm(alpha=1 / period, adjust=False).mean() / atr_s
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


class TrendMomentumStrategy(BaseStrategy):
    def __init__(
        self,
        lookback=config.TM_LOOKBACK,
        rsi_period=config.TM_RSI_PERIOD,
        rsi_buy=config.TM_RSI_BUY,
        rsi_sell=config.TM_RSI_SELL,
        sl_atr=config.TM_SL_ATR,
        tp_r=config.TM_TP_R,
        adx_thresh=config.TM_ADX_THRESH,
        session=config.TM_SESSION,
        history_bars=config.TM_HISTORY_BARS,
        be_move_at_r=config.TM_BE_AT_R,
        magic=config.MAGIC_TM,
    ):
        super().__init__("Trend Momentum", magic=magic)
        self.lookback = lookback
        self.rsi_period = rsi_period
        self.rsi_buy = rsi_buy
        self.rsi_sell = rsi_sell
        self.sl_atr = sl_atr
        self.tp_r = tp_r
        self.adx_thresh = adx_thresh
        self.session = session
        self.history_bars = history_bars
        self.be_move_at_r = be_move_at_r

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if len(df) < 60:
            return df

        # Đồng bộ nhãn thời gian (MT5 nến "time" = giờ mở nến, khớp resample label=left)
        ts = pd.to_datetime(
            df["time"] if "time" in df.columns else df.index
        ).astype("datetime64[us]")
        tmp = pd.DataFrame(
            {
                "ts": ts.to_numpy(),
                "open": df["open"].to_numpy(),
                "high": df["high"].to_numpy(),
                "low": df["low"].to_numpy(),
                "close": df["close"].to_numpy(),
            }
        ).sort_values("ts")

        # M15 → ATR (dùng nến M15 ĐÃ ĐÓNG: shift 1)
        m15 = (
            tmp.set_index("ts")
            .resample("15min")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna()
        )
        m15["atr15"] = _atr(m15, 14).shift(1)
        m15 = m15.reset_index().rename(columns={"index": "t"})
        m15 = m15[["ts", "atr15"]].rename(columns={"ts": "t"})

        # H1 → EMA200 + ADX (nến H1 ĐÃ ĐÓNG: shift 1)
        h1 = (
            tmp.set_index("ts")
            .resample("1h")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna()
        )
        h1["ema200"] = h1["close"].ewm(span=200, adjust=False).mean().shift(1)
        h1["adx14"] = _adx(h1, 14).shift(1)
        h1 = h1.reset_index().rename(columns={"ts": "t"})
        h1 = h1[["t", "ema200", "adx14"]]

        base = pd.merge_asof(
            tmp[["ts"]], m15, left_on="ts", right_on="t", direction="backward"
        )
        base = pd.merge_asof(
            base, h1, left_on="ts", right_on="t", direction="backward"
        )

        close = tmp["close"]
        base["rsi"] = _rsi(close, self.rsi_period).to_numpy()
        base["prior_high"] = tmp["high"].shift(1).rolling(self.lookback).max().to_numpy()
        base["prior_low"] = tmp["low"].shift(1).rolling(self.lookback).min().to_numpy()
        base["hour"] = tmp["ts"].dt.hour.to_numpy()

        for col in ("atr15", "ema200", "adx14", "rsi", "prior_high", "prior_low", "hour"):
            df[col] = base[col].to_numpy()
        df["in_session"] = df["hour"].between(self.session[0], self.session[1])
        return df

    def check_signal(self, df: pd.DataFrame) -> str | None:
        if len(df) < 2:
            return None
        sig = df.iloc[-2]
        try:
            ema200 = float(sig["ema200"])
            atr15 = float(sig["atr15"])
            adx14 = float(sig["adx14"])
            rsi = float(sig["rsi"])
            prior_high = float(sig["prior_high"])
            prior_low = float(sig["prior_low"])
        except Exception:
            return None
        if not all(np.isfinite((ema200, atr15, adx14, rsi, prior_high, prior_low))):
            return None
        if not bool(sig["in_session"]):
            return None
        if adx14 < self.adx_thresh:
            return None

        if sig["close"] > ema200 and sig["close"] > prior_high and rsi >= self.rsi_buy:
            return "BUY"
        if sig["close"] < ema200 and sig["close"] < prior_low and rsi <= self.rsi_sell:
            return "SELL"
        return None

    def get_sl_tp(self, df: pd.DataFrame, entry_price: float, digits: int, order_type: str):
        atr15 = float(df.iloc[-2]["atr15"])
        if not np.isfinite(atr15) or atr15 <= 0:
            return None, None
        dist = self.sl_atr * atr15
        d = int(digits)
        if order_type == "BUY":
            sl = round(entry_price - dist, d)
            tp = round(entry_price + (dist * self.tp_r), d)
        else:
            sl = round(entry_price + dist, d)
            tp = round(entry_price - (dist * self.tp_r), d)
        return sl, tp