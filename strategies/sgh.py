"""
strategies/sgh.py
Phỏng theo EA "Smart Gold Hunter" (MQL5 product 170050) cho XAUUSD.

Triết lý: No Grid / No Martingale / No Recovery / No Hedging
          => SINGLE ENTRY / ONE SHOT, mỗi setup 1 lệnh, có SL/TP thật,
             Break Even + trailing, nhiều profile + lớp bảo vệ.

Entry: phá vỡ kênh Donchian (đỉnh/đáy N nến) + xác nhận thân nến + lọc EMA.
Quản lý: SL theo ATR, TP = bội R, Break Even, trailing SL, (tùy chọn chốt một phần).
Bảo vệ (live): giới hạn lãi/lỗ ngày, spread, news, đóng lệnh thứ 6.
"""
import numpy as np
import pandas as pd

import config
from .base import BaseStrategy


def _atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = frame["high"], frame["low"], frame["close"]
    tr = pd.concat(
        [h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


# Preset theo profile (đè lên giá trị config)
_PROFILES = {
    "striker": dict(tp_r=1.5, sl_atr=1.5, min_body_atr=0.30, break_lookback=20,
                    max_trades=3, be_at_r=1.0, trail_at_r=1.0, trail_gap_r=0.8,
                    partial_frac=0.0),
    "ultimate scalper": dict(tp_r=1.0, sl_atr=1.0, min_body_atr=0.20, break_lookback=10,
                             max_trades=6, be_at_r=0.7, trail_at_r=0.8, trail_gap_r=0.6,
                             partial_frac=0.0),
    "swinger": dict(tp_r=3.0, sl_atr=2.0, min_body_atr=0.40, break_lookback=40,
                    max_trades=1, be_at_r=1.5, trail_at_r=1.5, trail_gap_r=1.0,
                    partial_frac=0.0),
    "prop scalper": dict(tp_r=1.2, sl_atr=1.0, min_body_atr=0.30, break_lookback=15,
                         max_trades=2, be_at_r=0.8, trail_at_r=0.8, trail_gap_r=0.6,
                         partial_frac=0.0),
    "prr scalping": dict(tp_r=2.0, sl_atr=1.0, min_body_atr=0.25, break_lookback=12,
                         max_trades=4, be_at_r=1.0, trail_at_r=1.0, trail_gap_r=0.7,
                         partial_frac=0.0),
}


class SmartGoldHunterStrategy(BaseStrategy):
    def __init__(
        self,
        profile=config.SGH_PROFILE,
        break_lookback=config.SGH_BREAK_LOOKBACK,
        break_buf_atr=config.SGH_BREAK_BUF_ATR,
        min_body_atr=config.SGH_MIN_BODY_ATR,
        trend_mode=config.SGH_TREND_MODE,
        ema_period=config.SGH_EMA,
        sl_atr=config.SGH_SL_ATR,
        tp_r=config.SGH_TP_R,
        max_trades=config.SGH_MAX_TRADES_PER_DAY,
        min_bars_between=config.SGH_MIN_BARS_BETWEEN,
        be_at_r=config.SGH_BE_AT_R,
        trail_at_r=config.SGH_TRAIL_AT_R,
        trail_gap_r=config.SGH_TRAIL_GAP_R,
        partial_frac=config.SGH_PARTIAL_FRAC,
        partial_at_r=config.SGH_PARTIAL_AT_R,
        max_spread_points=config.SGH_MAX_SPREAD_POINTS,
        friday_close_hour=config.SGH_FRIDAY_CLOSE_HOUR,
        news_filter=config.SGH_NEWS_FILTER,
        news_hour=config.SGH_NEWS_HOUR,
        news_minute=config.SGH_NEWS_MINUTE,
        news_skip_min=config.SGH_NEWS_SKIP_MIN,
        daily_profit_target_pct=config.SGH_DAILY_PROFIT_TARGET_PCT,
        daily_loss_limit_pct=config.SGH_DAILY_LOSS_LIMIT_PCT,
        history_bars=config.SGH_HISTORY_BARS,
        lot=config.SGH_LOT,
        magic=config.MAGIC_SGH,
    ):
        super().__init__("Smart Gold Hunter", magic=magic)
        # Áp preset profile (không áp cho "custom")
        prof = (profile or "custom").strip().lower()
        p = _PROFILES.get(prof)
        if p:
            break_lookback = p["break_lookback"]
            min_body_atr = p["min_body_atr"]
            sl_atr = p["sl_atr"]
            tp_r = p["tp_r"]
            max_trades = p["max_trades"]
            be_at_r = p["be_at_r"]
            trail_at_r = p["trail_at_r"]
            trail_gap_r = p["trail_gap_r"]
            partial_frac = p["partial_frac"]

        self.profile = profile
        self.break_lookback = int(break_lookback)
        self.break_buf_atr = break_buf_atr
        self.min_body_atr = min_body_atr
        self.trend_mode = str(trend_mode).lower()
        self.ema_period = int(ema_period)
        self.sl_atr = sl_atr
        self.tp_r = tp_r
        self.max_trades = int(max_trades)
        self.min_bars_between = int(min_bars_between)
        self.friday_close_hour = int(friday_close_hour)
        self.news_filter = bool(news_filter)
        self.news_hour = int(news_hour)
        self.news_minute = int(news_minute)
        self.news_skip_min = int(news_skip_min)
        self.daily_profit_target_pct = daily_profit_target_pct
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.max_spread_points = int(max_spread_points or 0)
        self.history_bars = history_bars
        self.lot = lot
        self.comment = config.SGH_COMMENT
        self.timeframe = config.SGH_TF
        self.warmup_bars = config.SGH_WARMUP_BARS

        # Quản lý lệnh (khớp Backtester + BotEngine)
        self.partial_frac = partial_frac
        self.partial_at_r = partial_at_r
        self.be_move_at_r = be_at_r
        self.trail_at_r = trail_at_r
        self.trail_gap_r = trail_gap_r

    # ------------------------------------------------------------------
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        n = len(df)
        if n < self.break_lookback + 20:
            return df

        raw = df["time"] if "time" in df.columns else df.index
        ts = pd.to_datetime(pd.Series(np.asarray(raw)))
        high = df["high"].to_numpy(); low = df["low"].to_numpy()
        close = df["close"].to_numpy(); open_ = df["open"].to_numpy()

        base = pd.DataFrame({"high": high, "low": low, "close": close, "open": open_})
        base["atr"] = _atr(base, 14).to_numpy()
        base["ema"] = base["close"].ewm(span=self.ema_period, adjust=False).mean()
        hour = ts.dt.hour.to_numpy(); minute = ts.dt.minute.to_numpy()
        wd = ts.dt.weekday.to_numpy()

        # Kênh Donchian (đỉnh/đáy N nến TRƯỚC nến hiện tại)
        H = base["high"].rolling(self.break_lookback).max().shift(1).to_numpy()
        L = base["low"].rolling(self.break_lookback).min().shift(1).to_numpy()
        atr = base["atr"].to_numpy(); ema = base["ema"].to_numpy()

        sig_b = np.zeros(n, dtype=bool); sig_s = np.zeros(n, dtype=bool)
        sl_b = np.full(n, np.nan); sl_s = np.full(n, np.nan)

        cur_date = None; trades_today = 0; last_idx = -10**9
        for i in range(1, n):
            d = ts.dt.date.iat[i]
            if d != cur_date:
                cur_date = d; trades_today = 0
            a = atr[i]
            if not np.isfinite(a) or a <= 0:
                continue

            # Bảo vệ thời gian / news
            if self.friday_close_hour < 24 and wd[i] == 4 and hour[i] >= self.friday_close_hour:
                continue
            if self.news_filter and wd[i] == 4 and hour[i] == self.news_hour and \
                    abs(int(minute[i]) - self.news_minute) <= self.news_skip_min:
                continue
            if self.max_trades > 0 and trades_today >= self.max_trades:
                continue
            if i - last_idx < self.min_bars_between:
                continue

            buff = self.break_buf_atr * a
            body = abs(close[i] - open_[i])
            if body < self.min_body_atr * a:
                continue

            allow_buy = self.trend_mode != "ema" or close[i] > ema[i]
            allow_sell = self.trend_mode != "ema" or close[i] < ema[i]

            if allow_buy and np.isfinite(H[i]) and close[i] >= H[i] + buff:
                sl = close[i] - self.sl_atr * a
                if close[i] - sl > 0:
                    sl_b[i] = sl; sig_b[i] = True
                    trades_today += 1; last_idx = i
                    continue
            if allow_sell and np.isfinite(L[i]) and close[i] <= L[i] - buff:
                sl = close[i] + self.sl_atr * a
                if sl - close[i] > 0:
                    sl_s[i] = sl; sig_s[i] = True
                    trades_today += 1; last_idx = i

        df["sgh_sig_buy"] = sig_b
        df["sgh_sl_buy"] = sl_b
        df["sgh_sig_sell"] = sig_s
        df["sgh_sl_sell"] = sl_s
        return df

    # ------------------------------------------------------------------
    def check_signal(self, df: pd.DataFrame):
        if len(df) < 3:
            return None
        sig = df.iloc[-2]
        try:
            if bool(sig.get("sgh_sig_buy", False)) and np.isfinite(float(sig["sgh_sl_buy"])):
                return "BUY"
            if bool(sig.get("sgh_sig_sell", False)) and np.isfinite(float(sig["sgh_sl_sell"])):
                return "SELL"
        except Exception:
            return None
        return None

    def get_sl_tp(self, df, entry_price, digits, order_type):
        if len(df) < 3:
            return None, None
        sig = df.iloc[-2]
        try:
            if order_type == "BUY":
                sl = float(sig["sgh_sl_buy"]); R = entry_price - sl
                tp = entry_price + self.tp_r * R
            else:
                sl = float(sig["sgh_sl_sell"]); R = sl - entry_price
                tp = entry_price - self.tp_r * R
        except Exception:
            return None, None
        if not all(np.isfinite((sl, tp))) or R <= 0:
            return None, None
        return round(sl, int(digits)), round(tp, int(digits))
