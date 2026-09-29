"""
strategies/ict.py
ICT "Power of 3 / Silver Bullet" (theo Michael J. Huddleston).
Chạy trên XAUUSD M5. Xem docs/ict_playbook.md.

Mô hình đầy đủ (tất cả trên nến M5 ĐÃ ĐÓNG, không nhìn tương lai):
  1. BIAS khung lớn (mặc định `prevday` = hướng nến ngày trước) — chỉ trade cùng chiều.
  2. Vùng TÍCH LŨY Á (00–06h broker) tạo thanh khoản ở hai biên.
  3. Trong KILLZONE (London 07–11h, NY 12–16h) giá QUÉT biên ĐỐI DIỆN bias
     (manipulation) rồi ĐÓNG nến reclaim trở lại → dấu hiệu lấy thanh khoản.
  4. DISPLACEMENT + CHoCH: nến M5 đóng phá swing đối diện kèm thân nến lớn.
  5. Vùng vào lệnh: FVG mới nhất trước CHoCH (bắt buộc).
  6. Vào LIMIT tại CE/OTE của vùng; SL sau điểm quét ± buffer.
  7. TP = DRAW ON LIQUIDITY: PDH/PDL hoặc biên Á đối diện.
  8. Chốt 50% @1R, phần còn lại chạy tới TP.

Chiến lược dùng lệnh CHỜ LIMIT → triển khai ở get_pending_setup(),
check_signal() trả None (trừ khi ICT_ENTRY_MODE = "market").
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


class ICTKillzoneFVGStrategy(BaseStrategy):
    def __init__(
        self,
        swing_k=config.ICT_SWING_K,
        killzones=config.ICT_KILLZONES,
        asia=config.ICT_ASIA,
        bias_mode=config.ICT_BIAS_MODE,
        bias_ema=config.ICT_BIAS_EMA,
        use_asia_liq=config.ICT_USE_ASIA_LIQ,
        use_pdhpdl=config.ICT_USE_PDHPDL,
        use_swing_liq=config.ICT_USE_SWING_LIQ,
        min_sweep_atr=config.ICT_MIN_SWEEP_ATR,
        choch_wait=config.ICT_CHOCH_WAIT,
        disp_atr=config.ICT_DISP_ATR,
        zone_lookback=config.ICT_ZONE_LOOKBACK,
        entry_frac=config.ICT_ENTRY_FRAC,
        require_fvg=config.ICT_REQUIRE_FVG,
        entry_mode=config.ICT_ENTRY_MODE,
        sl_buf_atr=config.ICT_SL_BUF_ATR,
        min_r_atr=config.ICT_MIN_R_ATR,
        max_r_atr=config.ICT_MAX_R_ATR,
        tp_mode=config.ICT_TP_MODE,
        tp_r=config.ICT_TP_R,
        tp_min_r=config.ICT_TP_MIN_R,
        pend_min=config.ICT_PEND_MIN,
        one_per_day=config.ICT_ONE_PER_DAY,
        history_bars=config.ICT_HISTORY_BARS,
        partial_frac=config.ICT_PARTIAL_FRAC,
        partial_at_r=config.ICT_PARTIAL_AT_R,
        be_move_at_r=config.ICT_BE_AT_R,
        min_fvg_atr=config.ICT_MIN_FVG_ATR,
        vol_ma=config.ICT_VOL_MA,
        vol_min=config.ICT_VOL_MIN,
        skip_mitigated=config.ICT_SKIP_MITIGATED,
        mitigate_max=config.ICT_MITIGATE_MAX,
        fvg_select=config.ICT_FVG_SELECT,
        magic=config.MAGIC_ICT,
    ):
        super().__init__("ICT KZ→Sweep→FVG", magic=magic)
        self.swing_k = swing_k
        self.killzones = killzones
        self.asia = asia
        self.bias_mode = str(bias_mode).lower()
        self.bias_ema = bias_ema
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
        self.sl_buf_atr = sl_buf_atr
        self.min_r_atr = min_r_atr
        self.max_r_atr = max_r_atr
        self.tp_mode = tp_mode
        self.tp_r = tp_r
        self.tp_min_r = tp_min_r
        self.pend_min = pend_min
        self.one_per_day = one_per_day
        self.history_bars = history_bars
        self.partial_frac = partial_frac
        self.partial_at_r = partial_at_r
        self.be_move_at_r = be_move_at_r
        self.min_fvg_atr = min_fvg_atr
        self.vol_ma = int(vol_ma)
        self.vol_min = vol_min
        self.skip_mitigated = skip_mitigated
        self.mitigate_max = mitigate_max
        self.fvg_select = str(fvg_select).lower()
        self.lot = config.ICT_LOT
        self.comment = config.ICT_COMMENT
        self.timeframe = "M5"
        self.warmup_bars = config.ICT_WARMUP_BARS

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
        # Volume + MA + EMA trend (để chấm điểm/lọc FVG)
        if "volume" in df.columns:
            base["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0).to_numpy()
        else:
            base["volume"] = 0.0
        base["vol_ma"] = base["volume"].rolling(max(2, self.vol_ma)).mean()
        base["ema"] = base["close"].ewm(span=50, adjust=False).mean()
        base["hour"] = base["ts"].dt.hour
        base["date"] = base["ts"].dt.date

        # PDH/PDL ngày hôm trước (thanh khoản đối diện = draw on liquidity)
        day = base.groupby("date").agg(day_hi=("high", "max"), day_lo=("low", "min"))
        prev = day.shift(1)
        base["pdh"] = base["date"].map(prev["day_hi"])
        base["pdl"] = base["date"].map(prev["day_lo"])

        # Biên vùng tích lũy Á hôm nay (chỉ dùng SAU khi phiên Á kết thúc)
        in_asia = base["hour"].between(self.asia[0], self.asia[1])
        asia = base[in_asia].groupby("date").agg(a_hi=("high", "max"), a_lo=("low", "min"))
        base["asia_hi"] = base["date"].map(asia["a_hi"])
        base["asia_lo"] = base["date"].map(asia["a_lo"])
        base.loc[base["hour"] <= self.asia[1], ["asia_hi", "asia_lo"]] = np.nan

        # BIAS khung lớn H4 (nến H4 ĐÃ ĐÓNG: shift 1 để tránh nhìn trước)
        base = self._add_bias(base)

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
        bias = base["bias"].to_numpy()
        pdh = base["pdh"].to_numpy()
        pdl = base["pdl"].to_numpy()
        asia_hi = base["asia_hi"].to_numpy()
        asia_lo = base["asia_lo"].to_numpy()
        vol = base["volume"].to_numpy()
        volma = base["vol_ma"].to_numpy()
        ema = base["ema"].to_numpy()

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

            # Chỉ trade cùng chiều bias (bias = 0 khi mode "none" → cho cả hai)
            allow_buy = self.bias_mode == "none" or bias[i] > 0
            allow_sell = self.bias_mode == "none" or bias[i] < 0

            # ---------- BUY: quét đáy (side liquidity) rồi CHoCH tăng ----------
            if allow_buy and not done_buy:
                if act_b:
                    if i - start_b > self.choch_wait:
                        act_b = False
                    elif close[i] > ref_high and (close[i] - open_[i]) >= self.disp_atr * a:
                        zone = self._bull_zone(i, base, low, high, close, open_, atr, vol, volma, ema)
                        if zone is not None:
                            if self.entry_mode == "market":
                                res = self._market_buy(zone, sweep_low, close[i], a)
                            else:
                                res = self._build_buy(zone, sweep_low, close[i], a,
                                                      [pdh[i], asia_hi[i], last_sh[i]])
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

            # ---------- SELL: quét đỉnh (buy-side liquidity) rồi CHoCH giảm ----------
            if allow_sell and not done_sell:
                if act_s:
                    if i - start_s > self.choch_wait:
                        act_s = False
                    elif close[i] < ref_low and (open_[i] - close[i]) >= self.disp_atr * a:
                        zone = self._bear_zone(i, base, low, high, close, open_, atr, vol, volma, ema)
                        if zone is not None:
                            if self.entry_mode == "market":
                                res = self._market_sell(zone, sweep_high, close[i], a)
                            else:
                                res = self._build_sell(zone, sweep_high, close[i], a,
                                                       [pdl[i], asia_lo[i], last_sl[i]])
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

        for col, arr in (
            ("ict_sig_buy", sig_buy), ("ict_sig_sell", sig_sell),
            ("ict_lvl_buy", lvl_buy), ("ict_sl_buy", sl_buy), ("ict_tp_buy", tp_buy),
            ("ict_lvl_sell", lvl_sell), ("ict_sl_sell", sl_sell), ("ict_tp_sell", tp_sell),
        ):
            df[col] = arr
        return df

    # ------------------------------------------------------------------
    # Bias khung lớn
    # ------------------------------------------------------------------
    def _add_bias(self, base: pd.DataFrame) -> pd.DataFrame:
        if self.bias_mode == "h4ema":
            tmp = base[["ts", "close"]].set_index("ts")
            h4 = tmp.resample("4h").agg({"close": "last"}).dropna()
            h4["ema"] = h4["close"].ewm(span=self.bias_ema, adjust=False).mean().shift(1)
            h4 = h4.reset_index().rename(columns={"index": "ts"})
            base = pd.merge_asof(base, h4[["ts", "ema"]], on="ts", direction="backward")
            ema = base["ema"].to_numpy()
            cl = base["close"].to_numpy()
            base["bias"] = np.where(cl > ema, 1, np.where(cl < ema, -1, 0))
        elif self.bias_mode == "prevday":
            dayc = base.groupby("date").agg(o=("open", "first"), c=("close", "last"))
            dayc["b"] = np.where(dayc["c"] > dayc["o"], 1, -1)
            prevb = dayc["b"].shift(1)
            base["bias"] = base["date"].map(prevb).fillna(0).astype(int)
        else:
            base["bias"] = 0
        return base

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
    # Vùng vào lệnh (FVG / OB)
    # ------------------------------------------------------------------
    def _fvg_score(self, direction, size, a, volm, volmam, close_m, ema_m, o, h, l):
        """Điểm chất lượng FVG (học từ Ranked FVG – Zeiierman): size/ATR + volume + trend + thân nến."""
        gap = min(size / a, 2.0) / 2.0 * 40.0 if a and a > 0 else 0.0
        volS = min(volm / volmam, 2.0) / 2.0 * 30.0 if volmam and volmam > 0 else 0.0
        trend = 20.0 if ((direction > 0 and close_m > ema_m) or (direction < 0 and close_m < ema_m)) else 0.0
        rng = max(h - l, 1e-9)
        body = abs(close_m - o) / rng * 10.0
        return gap + volS + trend + body

    def _fvg_ok(self, i, m, zl, zh, direction, low, high, atr, vol, volma):
        size = zh - zl
        if size <= 0:
            return False
        a = atr[i]
        if self.min_fvg_atr > 0 and (not np.isfinite(a) or size < self.min_fvg_atr * a):
            return False
        if self.vol_min > 0:
            vm = volma[m]
            if np.isfinite(vm) and vm > 0 and vol[m] < self.vol_min * vm:
                return False
        if self.skip_mitigated:
            seg = low[m + 1:i + 1] if direction > 0 else high[m + 1:i + 1]
            if len(seg) > 0:
                pen = (zh - seg.min()) / size if direction > 0 else (seg.max() - zl) / size
                if np.isfinite(pen) and pen >= self.mitigate_max:
                    return False
        return True

    def _bull_zone(self, i, base, low, high, close, open_, atr, vol, volma, ema):
        lo = max(2, i - self.zone_lookback)
        best = None
        best_score = -1.0
        for m in range(i, lo - 1, -1):
            if low[m] > high[m - 2] and low[m] < close[i]:
                zl, zh = high[m - 2], low[m]
                if not self._fvg_ok(i, m, zl, zh, +1, low, high, atr, vol, volma):
                    continue
                if self.fvg_select == "score":
                    sc = self._fvg_score(+1, zh - zl, atr[i], vol[m], volma[m],
                                         close[m], ema[m], open_[m], high[m], low[m])
                    if sc > best_score:
                        best_score = sc
                        best = (zl, zh)
                else:
                    return (zl, zh)
        if best is not None:
            return best
        if self.require_fvg:
            return None
        ob = None
        for m in range(i, i - self.zone_lookback - 1, -1):
            if m < 0:
                break
            if close[m] < open_[m] and high[m] < close[i]:
                ob = (low[m], high[m])
                break
        return ob

    def _bear_zone(self, i, base, low, high, close, open_, atr, vol, volma, ema):
        lo = max(2, i - self.zone_lookback)
        best = None
        best_score = -1.0
        for m in range(i, lo - 1, -1):
            if high[m] < low[m - 2] and high[m] > close[i]:
                zl, zh = high[m], low[m - 2]
                if not self._fvg_ok(i, m, zl, zh, -1, low, high, atr, vol, volma):
                    continue
                if self.fvg_select == "score":
                    sc = self._fvg_score(-1, zh - zl, atr[i], vol[m], volma[m],
                                         close[m], ema[m], open_[m], high[m], low[m])
                    if sc > best_score:
                        best_score = sc
                        best = (zl, zh)
                else:
                    return (zl, zh)
        if best is not None:
            return best
        if self.require_fvg:
            return None
        ob = None
        for m in range(i, i - self.zone_lookback - 1, -1):
            if m < 0:
                break
            if close[m] > open_[m] and low[m] > close[i]:
                ob = (high[m], low[m])
                break
        return ob

    # ------------------------------------------------------------------
    # Dựng lệnh LIMIT + TP draw-on-liquidity
    # ------------------------------------------------------------------
    def _build_buy(self, zone, sweep_low, price, a, liq):
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
        tp = self._tp("BUY", entry, R, liq)
        if not np.isfinite(tp) or tp <= entry:
            return None
        return float(entry), float(sl), float(tp)

    def _build_sell(self, zone, sweep_high, price, a, liq):
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
        tp = self._tp("SELL", entry, R, liq)
        if not np.isfinite(tp) or tp >= entry:
            return None
        return float(entry), float(sl), float(tp)

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

    def _tp(self, typ, entry, R, liq_levels):
        """TP = DRAW ON LIQUIDITY: chọn mức thanh khoản GẦN NHẤT phía đối diện,
        cách entry ít nhất ICT_TP_MIN_R×R; nếu không có → bội số R."""
        if str(self.tp_mode).upper() == "LIQ":
            best = None
            for lv in liq_levels:
                if not np.isfinite(lv):
                    continue
                dist = (lv - entry) if typ == "BUY" else (entry - lv)
                if dist >= self.tp_min_r * R and (best is None or dist < best[1]):
                    best = (lv, dist)
            if best is not None:
                return best[0]
        return entry + self.tp_r * R if typ == "BUY" else entry - self.tp_r * R

    # ------------------------------------------------------------------
    # Interface với bot/backtester
    # ------------------------------------------------------------------
    def check_signal(self, df: pd.DataFrame):
        if self.entry_mode != "market" or len(df) < 3:
            return None
        sig = df.iloc[-2]
        try:
            if bool(sig.get("ict_sig_buy", False)) and np.isfinite(float(sig["ict_sl_buy"])):
                return "BUY"
            if bool(sig.get("ict_sig_sell", False)) and np.isfinite(float(sig["ict_sl_sell"])):
                return "SELL"
        except Exception:
            return None
        return None

    def get_pending_setup(self, df: pd.DataFrame):
        if self.entry_mode == "market" or len(df) < 3:
            return None
        sig = df.iloc[-2]
        try:
            if bool(sig.get("ict_sig_buy", False)):
                lvl = float(sig["ict_lvl_buy"])
                sl = float(sig["ict_sl_buy"])
                tp = float(sig["ict_tp_buy"])
                if all(np.isfinite((lvl, sl, tp))) and sl < lvl < tp:
                    return {"type": "BUY", "level": lvl, "sl": sl, "tp": tp,
                            "wait_min": self.pend_min}
            elif bool(sig.get("ict_sig_sell", False)):
                lvl = float(sig["ict_lvl_sell"])
                sl = float(sig["ict_sl_sell"])
                tp = float(sig["ict_tp_sell"])
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
                sl = float(sig["ict_sl_buy"])
                R = entry_price - sl
                tp = entry_price + self.tp_r * R
            else:
                sl = float(sig["ict_sl_sell"])
                R = sl - entry_price
                tp = entry_price - self.tp_r * R
        except Exception:
            return None, None
        if not all(np.isfinite((sl, tp))) or R <= 0:
            return None, None
        return round(sl, int(digits)), round(tp, int(digits))
