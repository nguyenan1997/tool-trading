"""
strategies/smc.py
HỆ THỐNG 3 — Smart Money Concept: Sweep → CHoCH → OB/FVG retrace.
ĐỘC LẬP với Trend Momentum và Asian Sweep. Chạy trên XAUUSD M5.

Quy trình (tất cả trên nến M5 ĐÃ ĐÓNG, không nhìn tương lai):
  1. Trong killzone, giá QUÉT một mức thanh khoản:
        - biên vùng Á hôm nay, PDH/PDL ngày trước, hoặc swing low/high gần nhất.
     Điều kiện quét: low < mức - SMC_MIN_SWEEP_ATR×ATR rồi ĐÓNG cửa trên mức
     (reclaim) — dấu hiệu lấy thanh khoản.
  2. Chờ CHoCH: trong SMC_CHOCH_WAIT nến, nến M5 đóng phá swing high đối diện
     (đối với BUY) kèm displacement (thân nến ≥ SMC_DISP_ATR×ATR).
  3. Vùng vào lệnh: FVG mới nhất trong SMC_ZONE_LOOKBACK nến trước CHoCH;
     nếu không có (và không bắt buộc FVG) → Order Block (nến ngược chiều cuối
     cùng trước cú đẩy).
  4. Vào LIMIT tại CE (SMC_ENTRY_FRAC) của vùng; SL sau điểm quét ± buffer;
     TP theo bội số R hoặc thanh khoản đối diện.
Chiến lược dùng lệnh CHỜ LIMIT → triển khai ở get_pending_setup(),
check_signal() trả None.
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


class SMCSweepChochStrategy(BaseStrategy):
    def __init__(
        self,
        swing_k=config.SMC_SWING_K,
        killzones=config.SMC_KILLZONES,
        asia=config.SMC_ASIA,
        use_asia_liq=config.SMC_USE_ASIA_LIQ,
        use_pdhpdl=config.SMC_USE_PDHPDL,
        use_swing_liq=config.SMC_USE_SWING_LIQ,
        min_sweep_atr=config.SMC_MIN_SWEEP_ATR,
        choch_wait=config.SMC_CHOCH_WAIT,
        disp_atr=config.SMC_DISP_ATR,
        zone_lookback=config.SMC_ZONE_LOOKBACK,
        entry_frac=config.SMC_ENTRY_FRAC,
        require_fvg=config.SMC_REQUIRE_FVG,
        entry_mode=config.SMC_ENTRY_MODE,
        use_bias=config.SMC_USE_BIAS,
        bias_ema=config.SMC_BIAS_EMA,
        sl_buf_atr=config.SMC_SL_BUF_ATR,
        min_r_atr=config.SMC_MIN_R_ATR,
        max_r_atr=config.SMC_MAX_R_ATR,
        tp_mode=config.SMC_TP_MODE,
        tp_r=config.SMC_TP_R,
        pend_min=config.SMC_PEND_MIN,
        one_per_day=config.SMC_ONE_PER_DAY,
        history_bars=config.SMC_HISTORY_BARS,
        partial_frac=config.SMC_PARTIAL_FRAC,
        partial_at_r=config.SMC_PARTIAL_AT_R,
        be_move_at_r=config.SMC_BE_AT_R,
        trail_at_r=config.SMC_TRAIL_AT_R,
        trail_gap_r=config.SMC_TRAIL_GAP_R,
        magic=config.MAGIC_SMC,
    ):
        super().__init__("SMC Sweep→CHoCH→OB/FVG", magic=magic)
        self.swing_k = swing_k
        self.killzones = killzones
        self.asia = asia
        self.use_asia_liq = use_asia_liq
        self.use_pdhpdl = use_pdhpdl
        self.use_swing_liq = use_swing_liq
        self.min_sweep_atr = min_sweep_atr
        self.choch_wait = choch_wait
        self.disp_atr = disp_atr
        self.zone_lookback = zone_lookback
        self.entry_frac = entry_frac
        self.require_fvg = require_fvg
        self.entry_mode = str(entry_mode).lower()
        self.use_bias = use_bias
        self.bias_ema = bias_ema
        self.sl_buf_atr = sl_buf_atr
        self.min_r_atr = min_r_atr
        self.max_r_atr = max_r_atr
        self.tp_mode = tp_mode
        self.tp_r = tp_r
        self.pend_min = pend_min
        self.one_per_day = one_per_day
        self.history_bars = history_bars
        self.partial_frac = partial_frac
        self.partial_at_r = partial_at_r
        self.be_move_at_r = be_move_at_r
        self.trail_at_r = trail_at_r
        self.trail_gap_r = trail_gap_r
        self.lot = config.SMC_LOT
        self.comment = config.SMC_COMMENT
        self.timeframe = "M5"

    # ------------------------------------------------------------------
    # Chỉ báo
    # ------------------------------------------------------------------
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if len(df) < 100:
            return df

        ts = pd.to_datetime(
            df["time"] if "time" in df.columns else df.index
        ).astype("datetime64[us]")
        base = pd.DataFrame(
            {
                "ts": ts.to_numpy(),
                "open": df["open"].to_numpy(),
                "high": df["high"].to_numpy(),
                "low": df["low"].to_numpy(),
                "close": df["close"].to_numpy(),
            }
        )
        base["atr"] = _atr(base, 14).to_numpy()
        base["hour"] = base["ts"].dt.hour
        base["date"] = base["ts"].dt.date

        # Bias H1 (EMA) — nến H1 đã đóng
        if self.use_bias:
            h1 = (
                base.set_index("ts")
                .resample("1h")
                .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
                .dropna()
            )
            h1["bias_ema"] = h1["close"].ewm(span=self.bias_ema, adjust=False).mean().shift(1)
            base = pd.merge_asof(
                base,
                h1.reset_index()[["ts", "bias_ema"]],
                on="ts",
                direction="backward",
            )
        else:
            base["bias_ema"] = np.nan

        # PDH/PDL ngày hôm trước
        day = base.groupby("date").agg(day_hi=("high", "max"), day_lo=("low", "min"))
        prev = day.shift(1)
        base["pdh"] = base["date"].map(prev["day_hi"])
        base["pdl"] = base["date"].map(prev["day_lo"])

        # Biên vùng Á hôm nay
        in_asia = base["hour"].between(self.asia[0], self.asia[1])
        asia = base[in_asia].groupby("date").agg(a_hi=("high", "max"), a_lo=("low", "min"))
        base["asia_hi"] = base["date"].map(asia["a_hi"])
        base["asia_lo"] = base["date"].map(asia["a_lo"])
        # Chỉ dùng biên vùng Á SAU khi phiên Á kết thúc (tránh nhìn trước dữ liệu tương lai)
        base.loc[base["hour"] <= self.asia[1], ["asia_hi", "asia_lo"]] = np.nan

        # Swing high/low (fractal) — chỉ xác nhận sau `swing_k` nến
        high = base["high"].to_numpy()
        low = base["low"].to_numpy()
        n = len(base)
        k = int(self.swing_k)
        ph = np.zeros(n, dtype=bool)
        pl = np.zeros(n, dtype=bool)
        for j in range(k, n - k):
            win_h = high[j - k:j + k + 1]
            win_l = low[j - k:j + k + 1]
            if high[j] >= win_h.max() and high[j] > high[j - k:j].max() and high[j] >= high[j + 1:j + k + 1].max():
                ph[j] = True
            if low[j] <= win_l.min() and low[j] < low[j - k:j].min() and low[j] <= low[j + 1:j + k + 1].min():
                pl[j] = True

        last_sh = np.full(n, np.nan)
        last_sl = np.full(n, np.nan)
        cur_h = np.nan
        cur_l = np.nan
        for i in range(n):
            j = i - k
            if j >= k:
                if ph[j]:
                    cur_h = high[j]
                if pl[j]:
                    cur_l = low[j]
            last_sh[i] = cur_h
            last_sl[i] = cur_l

        # Killzone
        in_kz = np.zeros(n, dtype=bool)
        for (h0, h1k) in self.killzones:
            in_kz |= (base["hour"].to_numpy() >= h0) & (base["hour"].to_numpy() < h1k)

        open_ = base["open"].to_numpy()
        close = base["close"].to_numpy()
        atr = base["atr"].to_numpy()
        hour = base["hour"].to_numpy()
        pdh = base["pdh"].to_numpy()
        pdl = base["pdl"].to_numpy()
        asia_hi = base["asia_hi"].to_numpy()
        asia_lo = base["asia_lo"].to_numpy()
        bias_ema = base["bias_ema"].to_numpy()

        sig_buy = np.zeros(n, dtype=bool)
        sig_sell = np.zeros(n, dtype=bool)
        lvl_buy = np.full(n, np.nan)
        sl_buy = np.full(n, np.nan)
        tp_buy = np.full(n, np.nan)
        lvl_sell = np.full(n, np.nan)
        sl_sell = np.full(n, np.nan)
        tp_sell = np.full(n, np.nan)

        # Trạng thái theo ngày
        cur_date = None
        done_buy = done_sell = False
        act_b = act_s = False
        sweep_low = sweep_high = np.nan
        ref_high = ref_low = np.nan
        start_b = start_s = -1

        for i in range(n):
            d = base["date"].iat[i]
            if d != cur_date:
                cur_date = d
                done_buy = done_sell = False
                act_b = act_s = False
                sweep_low = sweep_high = np.nan
                ref_high = ref_low = np.nan
                start_b = start_s = -1

            if not in_kz[i]:
                if hour[i] >= max(e for _, e in self.killzones):
                    act_b = act_s = False
                continue

            a = atr[i]
            if not np.isfinite(a) or a <= 0:
                continue

            # ---------- BUY: quét đáy rồi CHoCH tăng ----------
            if not done_buy:
                if act_b:
                    if i - start_b > self.choch_wait:
                        act_b = False
                    elif close[i] > ref_high and (close[i] - open_[i]) >= self.disp_atr * a:
                        zone = self._bull_zone(i, base, low, high, close, open_)
                        if zone is not None:
                            if self.entry_mode == "market":
                                res = self._market_buy(zone, sweep_low, close[i], a)
                            else:
                                res = self._build_buy(zone, sweep_low, close[i], a, pdh[i])
                            if res is not None:
                                lvl_buy[i], sl_buy[i], tp_buy[i] = res
                                sig_buy[i] = True
                                done_buy = True
                                act_b = False
                if not act_b and not done_buy:
                    if self._swept_low(i, low, close, a, asia_lo, pdl, last_sl):
                        act_b = True
                        sweep_low = low[i]
                        ref_high = last_sh[i]
                        start_b = i

            # ---------- SELL: quét đỉnh rồi CHoCH giảm ----------
            if not done_sell:
                if act_s:
                    if i - start_s > self.choch_wait:
                        act_s = False
                    elif close[i] < ref_low and (open_[i] - close[i]) >= self.disp_atr * a:
                        zone = self._bear_zone(i, base, low, high, close, open_)
                        if zone is not None:
                            if self.entry_mode == "market":
                                res = self._market_sell(zone, sweep_high, close[i], a)
                            else:
                                res = self._build_sell(zone, sweep_high, close[i], a, pdl[i])
                            if res is not None:
                                lvl_sell[i], sl_sell[i], tp_sell[i] = res
                                sig_sell[i] = True
                                done_sell = True
                                act_s = False
                if not act_s and not done_sell:
                    if self._swept_high(i, high, close, a, asia_hi, pdh, last_sh):
                        act_s = True
                        sweep_high = high[i]
                        ref_low = last_sl[i]
                        start_s = i

            # Bias HTF (tùy chọn) — loại tín hiệu ngược xu hướng lớn
            if self.use_bias and np.isfinite(bias_ema[i]):
                if sig_buy[i] and not (close[i] > bias_ema[i]):
                    sig_buy[i] = False
                    done_buy = False
                if sig_sell[i] and not (close[i] < bias_ema[i]):
                    sig_sell[i] = False
                    done_sell = False

        for col, arr in (
            ("smc_sig_buy", sig_buy), ("smc_sig_sell", sig_sell),
            ("smc_lvl_buy", lvl_buy), ("smc_sl_buy", sl_buy), ("smc_tp_buy", tp_buy),
            ("smc_lvl_sell", lvl_sell), ("smc_sl_sell", sl_sell), ("smc_tp_sell", tp_sell),
        ):
            df[col] = arr
        return df

    # ------------------------------------------------------------------
    # Kiểm tra quét thanh khoản
    # ------------------------------------------------------------------
    def _swept_low(self, i, low, close, a, asia_lo, pdl, last_sl):
        need = self.min_sweep_atr * a
        for lv in self._levels(self.use_asia_liq, asia_lo[i],
                               self.use_pdhpdl, pdl[i],
                               self.use_swing_liq, last_sl[i]):
            if np.isfinite(lv) and low[i] < lv - need and close[i] > lv:
                return True
        return False

    def _swept_high(self, i, high, close, a, asia_hi, pdh, last_sh):
        need = self.min_sweep_atr * a
        for lv in self._levels(self.use_asia_liq, asia_hi[i],
                               self.use_pdhpdl, pdh[i],
                               self.use_swing_liq, last_sh[i]):
            if np.isfinite(lv) and high[i] > lv + need and close[i] < lv:
                return True
        return False

    @staticmethod
    def _levels(use_a, a, use_p, p, use_s, s):
        out = []
        if use_a:
            out.append(a)
        if use_p:
            out.append(p)
        if use_s:
            out.append(s)
        return out

    # ------------------------------------------------------------------
    # Vùng OB / FVG
    # ------------------------------------------------------------------
    def _bull_zone(self, i, base, low, high, close, open_):
        lo = max(2, i - self.zone_lookback)
        fvg = None
        for m in range(i, lo - 1, -1):
            if low[m] > high[m - 2] and low[m] < close[i]:
                fvg = (high[m - 2], low[m])
                break
        if self.require_fvg:
            return fvg
        if fvg is not None:
            return fvg
        ob = None
        for m in range(i, i - self.zone_lookback - 1, -1):
            if m < 0:
                break
            if close[m] < open_[m] and high[m] < close[i]:
                ob = (low[m], high[m])
                break
        return ob

    def _bear_zone(self, i, base, low, high, close, open_):
        lo = max(2, i - self.zone_lookback)
        fvg = None
        for m in range(i, lo - 1, -1):
            if high[m] < low[m - 2] and high[m] > close[i]:
                fvg = (high[m], low[m - 2])
                break
        if self.require_fvg:
            return fvg
        if fvg is not None:
            return fvg
        ob = None
        for m in range(i, i - self.zone_lookback - 1, -1):
            if m < 0:
                break
            if close[m] > open_[m] and low[m] > close[i]:
                ob = (high[m], low[m])
                break
        return ob

    # ------------------------------------------------------------------
    # Dựng lệnh LIMIT
    # ------------------------------------------------------------------
    def _build_buy(self, zone, sweep_low, price, a, pdh):
        zl, zh = zone
        if not (np.isfinite(zl) and np.isfinite(zh)) or zh <= zl:
            return None
        entry = zh - self.entry_frac * (zh - zl)
        sl = min(sweep_low, zl) - self.sl_buf_atr * a
        R = entry - sl
        if R <= 0 or entry >= price - 1e-9:
            return None
        if R < self.min_r_atr * a or R > self.max_r_atr * a:
            return None
        tp = self._tp("BUY", entry, R, pdh)
        if not np.isfinite(tp) or tp <= entry:
            return None
        return float(entry), float(sl), float(tp)

    def _build_sell(self, zone, sweep_high, price, a, pdl):
        zl, zh = zone  # zl < zh
        if not (np.isfinite(zl) and np.isfinite(zh)) or zh <= zl:
            return None
        entry = zl + self.entry_frac * (zh - zl)
        sl = max(sweep_high, zh) + self.sl_buf_atr * a
        R = sl - entry
        if R <= 0 or entry <= price + 1e-9:
            return None
        if R < self.min_r_atr * a or R > self.max_r_atr * a:
            return None
        tp = self._tp("SELL", entry, R, pdl)
        if not np.isfinite(tp) or tp >= entry:
            return None
        return float(entry), float(sl), float(tp)

    # --- Vào MARKET ngay khi CHoCH (không chờ hồi) ---
    def _market_buy(self, zone, sweep_low, ref, a):
        zl, zh = zone
        if not (np.isfinite(zl) and np.isfinite(zh)) or zh <= zl:
            return None
        sl = min(sweep_low, zl) - self.sl_buf_atr * a
        R = ref - sl
        if R <= 0 or R < self.min_r_atr * a or R > self.max_r_atr * a:
            return None
        return float(ref), float(sl), float("nan")

    def _market_sell(self, zone, sweep_high, ref, a):
        zl, zh = zone  # zl < zh
        if not (np.isfinite(zl) and np.isfinite(zh)) or zh <= zl:
            return None
        sl = max(sweep_high, zh) + self.sl_buf_atr * a
        R = sl - ref
        if R <= 0 or R < self.min_r_atr * a or R > self.max_r_atr * a:
            return None
        return float(ref), float(sl), float("nan")

    def _tp(self, typ, entry, R, liq):
        if str(self.tp_mode).upper() == "LIQ" and np.isfinite(liq):
            if typ == "BUY" and liq > entry + 0.5 * R:
                return liq
            if typ == "SELL" and liq < entry - 0.5 * R:
                return liq
        return entry + self.tp_r * R if typ == "BUY" else entry - self.tp_r * R

    # ------------------------------------------------------------------
    # Interface với bot/backtester
    # ------------------------------------------------------------------
    def check_signal(self, df: pd.DataFrame):
        if self.entry_mode != "market" or len(df) < 3:
            return None
        sig = df.iloc[-2]
        try:
            if bool(sig.get("smc_sig_buy", False)) and np.isfinite(float(sig["smc_sl_buy"])):
                return "BUY"
            if bool(sig.get("smc_sig_sell", False)) and np.isfinite(float(sig["smc_sl_sell"])):
                return "SELL"
        except Exception:
            return None
        return None

    def get_pending_setup(self, df: pd.DataFrame):
        if self.entry_mode == "market" or len(df) < 3:
            return None
        sig = df.iloc[-2]
        try:
            if bool(sig.get("smc_sig_buy", False)):
                lvl = float(sig["smc_lvl_buy"])
                sl = float(sig["smc_sl_buy"])
                tp = float(sig["smc_tp_buy"])
                if all(np.isfinite((lvl, sl, tp))) and sl < lvl < tp:
                    return {"type": "BUY", "level": lvl, "sl": sl, "tp": tp,
                            "wait_min": self.pend_min}
            elif bool(sig.get("smc_sig_sell", False)):
                lvl = float(sig["smc_lvl_sell"])
                sl = float(sig["smc_sl_sell"])
                tp = float(sig["smc_tp_sell"])
                if all(np.isfinite((lvl, sl, tp))) and tp < lvl < sl:
                    return {"type": "SELL", "level": lvl, "sl": sl, "tp": tp,
                            "wait_min": self.pend_min}
        except Exception:
            return None
        return None

    def get_sl_tp(self, df, entry_price, digits, order_type):
        if self.entry_mode != "market" or len(df) < 3:
            return None, None
        sig = df.iloc[-2]
        try:
            if order_type == "BUY":
                sl = float(sig["smc_sl_buy"])
                R = entry_price - sl
                tp = entry_price + self.tp_r * R
            else:
                sl = float(sig["smc_sl_sell"])
                R = sl - entry_price
                tp = entry_price - self.tp_r * R
        except Exception:
            return None, None
        if not all(np.isfinite((sl, tp))) or R <= 0:
            return None, None
        return round(sl, int(digits)), round(tp, int(digits))
