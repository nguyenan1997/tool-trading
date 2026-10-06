"""
strategies/ranked_fvg.py
Port 1:1 từ indicator TradingView "Ranked FVG Signals (Zeiierman improved)".

QUY TẮC (khớp Pine):
  1. FVG 3 nến:
       bull: low > high[2]  -> top = low,      bottom = high[2], dir = +1
       bear: high < low[2]  -> top = low[2],   bottom = high,    dir = -1
     Lọc kích thước: (top - bottom) >= MIN_SIZE_ATR * ATR(14).
  2. Điểm mạnh = gap(40) + vol(30) + trend(20) + candle(10), clamp 0..100:
       gapS    = min(zsize/ATR, 2)/2 * 40
       volS    = min(volume/volMA, 2)/2 * 30
       trendS  = trendAlign ? 20 : 0        (close>EMA_trend khi bull | close<EMA khi bear)
       candleS = |close-open| / max(high-low, mintick) * 10
       bullStrength = dir==1 ? main : 100-main ; bearStrength = dir==-1 ? main : 100-main
  3. Theo dõi lấp (mitigation) mỗi nến (age>0):
       touched = bull ? low<=top : high>=bottom
       mitigation = clamp(fillDist/zoneSize, 0, 1) ; >= 1 -> xoá vùng.
       (ghi nhớ firstTouch = nến chạm đầu tiên)
  4. Xếp hạng: sắp theo "newest" (bornBar) hoặc "quality".
  5. Tín hiệu (trên nến ĐÓNG):
       - age >= WAIT_BARS, chưa signaled (nếu ONCE_ZONE)
       - touched (giá chạm vùng)
       - lọc: sức mạnh, cùng chiều EMA (nếu bật), chỉ FVG tươi (nếu bật)
       - xác nhận (chọn 1): EMA cross  HOẶC  nến đảo chiều
       - entry = biên gần (bull: top, bear: bottom)
       - sl    = bull: bottom - SL_BUF_ATR*ATR | bear: top + SL_BUF_ATR*ATR
       - tp    = biên gần của FVG ĐỐI DIỆN gần nhất (bull: min bottom > entry; bear: max top < entry)
                 nếu không có: REQUIRE_OPP ? bỏ : entry ± FALLBACK_RR*risk
       - chỉ lấy khi RR = |tp-entry| / risk >= MIN_RR

Vào lệnh: MARKET tại nến tín hiệu (engine lo điền lệnh), SL/TP là các mức tuyệt đối ở trên.
"""

import numpy as np
import pandas as pd

import config
from .base import BaseStrategy


def _atr(high, low, close, period=14):
    """ATR = RMA(True Range) — khớp ta.atr(14) của Pine (ewm alpha=1/period)."""
    h = pd.Series(high, dtype=float)
    l = pd.Series(low, dtype=float)
    c = pd.Series(close, dtype=float)
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean().to_numpy()


class RankedFVGStrategy(BaseStrategy):
    def __init__(
        self,
        max_stored=config.RANKED_FVG_MAX_STORED,
        min_size_atr=config.RANKED_FVG_MIN_SIZE_ATR,
        vol_len=config.RANKED_FVG_VOL_LEN,
        trend_len=config.RANKED_FVG_TREND_LEN,
        sort_mode=config.RANKED_FVG_SORT,
        wait_bars=config.RANKED_FVG_WAIT_BARS,
        min_rr=config.RANKED_FVG_MIN_RR,
        sl_buf_atr=config.RANKED_FVG_SL_BUF_ATR,
        require_opp=config.RANKED_FVG_REQUIRE_OPP,
        fallback_rr=config.RANKED_FVG_FALLBACK_RR,
        tp_cap_r=config.RANKED_FVG_TP_CAP_R,
        once_zone=config.RANKED_FVG_ONCE_ZONE,
        min_strength=config.RANKED_FVG_MIN_STRENGTH,
        trend_filter=config.RANKED_FVG_TREND_FILTER,
        only_fresh=config.RANKED_FVG_ONLY_FRESH,
        use_killzone=config.RANKED_FVG_USE_KILLZONE,
        killzones=config.RANKED_FVG_KILLZONES,
        bias_mode=config.RANKED_FVG_BIAS_MODE,
        bias_ema=config.RANKED_FVG_BIAS_EMA,
        entry_confirm=config.RANKED_FVG_ENTRY_CONFIRM,
        ema_len=config.RANKED_FVG_EMA_LEN,
        entry_mode=config.RANKED_FVG_ENTRY_MODE,
        pend_min=config.RANKED_FVG_PEND_MIN,
        magic=config.MAGIC_RANKED_FVG,
    ):
        super().__init__("Ranked FVG Signals", magic=magic)
        self.max_stored = int(max_stored)
        self.min_size_atr = float(min_size_atr)
        self.vol_len = int(vol_len)
        self.trend_len = int(trend_len)
        self.sort_mode = str(sort_mode).lower()
        self.wait_bars = int(wait_bars)
        self.min_rr = float(min_rr)
        self.sl_buf_atr = float(sl_buf_atr)
        self.require_opp = bool(require_opp)
        self.fallback_rr = float(fallback_rr)
        self.tp_cap_r = float(tp_cap_r)
        self.once_zone = bool(once_zone)
        self.min_strength = int(min_strength)
        self.trend_filter = bool(trend_filter)
        self.only_fresh = bool(only_fresh)
        self.use_killzone = bool(use_killzone)
        self.killzones = list(killzones or [])
        self.bias_mode = str(bias_mode).lower()
        self.bias_ema = int(bias_ema)
        self.entry_confirm = str(entry_confirm).lower()
        self.ema_len = int(ema_len)
        self.entry_mode = str(entry_mode).lower()
        self.pend_min = int(pend_min)

        self.use_signals = True
        self.lot = config.RANKED_FVG_LOT
        self.comment = config.RANKED_FVG_COMMENT
        self.timeframe = config.RANKED_FVG_TF
        self.history_bars = config.RANKED_FVG_HISTORY_BARS
        self.warmup_bars = config.RANKED_FVG_WARMUP_BARS
        self.manual_sltp = bool(getattr(config, "RANKED_FVG_MANUAL_SLTP", False))

        # Không dùng partial / BE / trailing (khớp indicator).
        self.partial_frac = 0.0
        self.partial_at_r = 0.0
        self.be_move_at_r = 0.0

    # ------------------------------------------------------------------
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        n = len(df)
        if n < 10:
            return df

        op = df["open"].to_numpy(dtype=float)
        hi = df["high"].to_numpy(dtype=float)
        lo = df["low"].to_numpy(dtype=float)
        cl = df["close"].to_numpy(dtype=float)
        if "volume" in df.columns:
            vol = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
        else:
            vol = np.zeros(n, dtype=float)

        atr = _atr(hi, lo, cl, 14)
        volMA = pd.Series(vol).rolling(max(2, self.vol_len)).mean().to_numpy()
        trendEMA = pd.Series(cl).ewm(span=self.trend_len, adjust=False).mean().to_numpy()
        emaE = pd.Series(cl).ewm(span=self.ema_len, adjust=False).mean().to_numpy()

        # Xác nhận EMA (crossover / crossunder như ta.crossover)
        emaBuyCross = np.zeros(n, dtype=bool)
        emaSellCross = np.zeros(n, dtype=bool)
        for i in range(1, n):
            emaBuyCross[i] = (cl[i] > emaE[i]) and (cl[i - 1] <= emaE[i - 1])
            emaSellCross[i] = (cl[i] < emaE[i]) and (cl[i - 1] >= emaE[i - 1])

        # Nến đảo chiều: engulfing hoặc pin bar (râu >= 60% range)
        bullRev = np.zeros(n, dtype=bool)
        bearRev = np.zeros(n, dtype=bool)
        for i in range(1, n):
            rng = max(hi[i] - lo[i], 1e-9)
            engBull = (cl[i] > op[i]) and (op[i] <= cl[i - 1]) and (cl[i] >= op[i - 1])
            engBear = (cl[i] < op[i]) and (op[i] >= cl[i - 1]) and (cl[i] <= op[i - 1])
            pinBull = (min(op[i], cl[i]) - lo[i]) >= 0.6 * rng
            pinBear = (hi[i] - max(op[i], cl[i])) >= 0.6 * rng
            bullRev[i] = engBull or pinBull
            bearRev[i] = engBear or pinBear

        # Killzone + bias đa khung
        ts = pd.to_datetime(df["time"] if "time" in df.columns else df.index)
        hour = ts.hour.to_numpy() if hasattr(ts, "hour") else np.zeros(n, dtype=int)
        in_kz = np.ones(n, dtype=bool)
        if self.use_killzone and self.killzones:
            in_kz[:] = False
            for (h0, h1) in self.killzones:
                in_kz |= (hour >= h0) & (hour < h1)
        bias = np.zeros(n, dtype=int)
        if self.bias_mode == "h4ema":
            tmp = pd.DataFrame({"ts": ts.to_numpy(), "close": cl}).set_index("ts")
            h4 = tmp.resample("4h").agg({"close": "last"}).dropna()
            h4["v"] = h4["close"].ewm(span=self.bias_ema, adjust=False).mean().shift(1)
            h4 = h4.reset_index()
            m = pd.merge_asof(pd.DataFrame({"ts": ts.to_numpy(), "close": cl}),
                              h4[["ts", "v"]], on="ts", direction="backward")
            ev = m["v"].to_numpy()
            bias = np.where(cl > ev, 1, np.where(cl < ev, -1, 0))
        elif self.bias_mode == "prevday":
            dts = pd.Series(ts).dt.date if not isinstance(ts, pd.Series) else ts.dt.date
            dd = pd.DataFrame({"date": np.asarray(dts), "open": op, "close": cl})
            dayc = dd.groupby("date").agg(o=("open", "first"), c=("close", "last"))
            dayc["b"] = np.where(dayc["c"] > dayc["o"], 1, -1)
            prevb = dayc["b"].shift(1)
            bias = pd.Series(np.asarray(dts)).map(prevb).fillna(0).astype(int).to_numpy()

        sig_buy = np.zeros(n, dtype=bool)
        sig_sell = np.zeros(n, dtype=bool)
        lvl_buy = np.full(n, np.nan)
        sl_buy = np.full(n, np.nan)
        tp_buy = np.full(n, np.nan)
        rr_buy = np.full(n, np.nan)
        lvl_sell = np.full(n, np.nan)
        sl_sell = np.full(n, np.nan)
        tp_sell = np.full(n, np.nan)
        rr_sell = np.full(n, np.nan)

        fvgs = []  # mỗi phần tử: dict (top,bottom,dir,bornBar,zsize,volScore,trendScore,quality,bullStr,bearStr,signaled,firstTouch)

        for i in range(n):
            a = atr[i]
            # --- 1. Phát hiện FVG mới ---
            if i >= 2 and np.isfinite(a) and a > 0:
                gap_up = lo[i] > hi[i - 2]
                gap_down = hi[i] < lo[i - 2]
                if gap_up or gap_down:
                    top = lo[i] if gap_up else lo[i - 2]
                    bottom = hi[i - 2] if gap_up else hi[i]
                    dirn = 1 if gap_up else -1
                    zsize = top - bottom
                    if zsize >= self.min_size_atr * a:
                        vm = volMA[i]
                        volScore = (vol[i] / vm) if (vm and np.isfinite(vm) and vm > 0) else 1.0
                        tScore = 1.0 if ((dirn == 1 and cl[i] > trendEMA[i]) or
                                         (dirn == -1 and cl[i] < trendEMA[i])) else 0.0
                        gapS = min(zsize / a, 2.0) / 2.0 * 40.0
                        volS = min(volScore, 2.0) / 2.0 * 30.0
                        trendS = tScore * 20.0
                        candleS = abs(cl[i] - op[i]) / max(hi[i] - lo[i], 1e-9) * 10.0
                        main = int(max(min(gapS + volS + trendS + candleS, 100.0), 0.0))
                        bullStr = main if dirn == 1 else 100 - main
                        bearStr = main if dirn == -1 else 100 - main
                        quality = zsize * 100.0 + volScore * 10.0 + tScore * 20.0
                        fvgs.append({
                            "top": top, "bottom": bottom, "dir": dirn, "bornBar": i,
                            "zsize": zsize, "volScore": volScore, "trendScore": tScore,
                            "quality": quality, "bullStr": bullStr, "bearStr": bearStr,
                            "mitigation": 0.0, "signaled": False, "firstTouch": -1,
                        })

            # --- 2. Cập nhật lấp (mitigation) — duyệt từ cuối lên ---
            j = len(fvgs) - 1
            while j >= 0:
                f = fvgs[j]
                age = i - f["bornBar"]
                touched = (lo[i] <= f["top"]) if f["dir"] == 1 else (hi[i] >= f["bottom"])
                if touched and age > 0:
                    if f["firstTouch"] < 0:
                        f["firstTouch"] = i
                    fillDist = (f["top"] - lo[i]) if f["dir"] == 1 else (hi[i] - f["bottom"])
                    zoneSize = max(f["top"] - f["bottom"], 1e-9)
                    f["mitigation"] = min(max(fillDist / zoneSize, 0.0), 1.0)
                f["quality"] = (f["zsize"] * 100.0 + f["volScore"] * 10.0 +
                                f["trendScore"] * 20.0 - f["mitigation"] * 50.0 - age * 0.1)
                if f["mitigation"] >= 1.0:
                    fvgs.pop(j)
                j -= 1

            # --- 3. Giới hạn số vùng lưu ---
            while len(fvgs) > self.max_stored:
                fvgs.pop()

            # --- 4. Sắp xếp ---
            if self.sort_mode == "quality":
                fvgs.sort(key=lambda f: f["quality"], reverse=True)
            else:
                fvgs.sort(key=lambda f: f["bornBar"], reverse=True)

            # --- 5. Tín hiệu ---
            if self.use_signals and len(fvgs) > 0 and in_kz[i]:
                for f in fvgs:
                    age = i - f["bornBar"]
                    if age < self.wait_bars:
                        continue
                    if self.once_zone and f["signaled"]:
                        continue

                    touched = (lo[i] <= f["top"]) if f["dir"] == 1 else (hi[i] >= f["bottom"])
                    if not touched:
                        continue

                    if self.bias_mode != "none":
                        if f["dir"] == 1 and not (bias[i] > 0):
                            continue
                        if f["dir"] == -1 and not (bias[i] < 0):
                            continue

                    own = f["bullStr"] if f["dir"] == 1 else f["bearStr"]
                    if self.min_strength > 0 and own < self.min_strength:
                        continue
                    if self.trend_filter and not ((f["dir"] == 1 and cl[i] > trendEMA[i]) or
                                                  (f["dir"] == -1 and cl[i] < trendEMA[i])):
                        continue
                    if self.only_fresh and f["firstTouch"] != i:
                        continue

                    if self.entry_confirm == "candle":
                        conf = bullRev[i] if f["dir"] == 1 else bearRev[i]
                    else:  # "ema"
                        conf = emaBuyCross[i] if f["dir"] == 1 else emaSellCross[i]
                    if not conf:
                        continue

                    entry = f["top"] if f["dir"] == 1 else f["bottom"]
                    if f["dir"] == 1:
                        sl = f["bottom"] - self.sl_buf_atr * a
                    else:
                        sl = f["top"] + self.sl_buf_atr * a
                    risk = abs(entry - sl)
                    if risk <= 0:
                        continue

                    # TP = biên gần khối FVG đối diện gần nhất
                    tp = np.nan
                    if f["dir"] == 1:
                        for o in fvgs:
                            if o["dir"] == -1 and o["bottom"] > entry:
                                tp = o["bottom"] if not np.isfinite(tp) else min(tp, o["bottom"])
                    else:
                        for o in fvgs:
                            if o["dir"] == 1 and o["top"] < entry:
                                tp = o["top"] if not np.isfinite(tp) else max(tp, o["top"])

                    if not np.isfinite(tp):
                        if self.require_opp:
                            continue
                        tp = entry + self.fallback_rr * risk if f["dir"] == 1 else entry - self.fallback_rr * risk

                    if self.tp_cap_r > 0:
                        mv = self.tp_cap_r * risk
                        if abs(tp - entry) > mv:
                            tp = entry + mv if f["dir"] == 1 else entry - mv

                    reward = abs(tp - entry)
                    rr = reward / risk
                    if rr < self.min_rr:
                        continue

                    f["signaled"] = True
                    if f["dir"] == 1:
                        sig_buy[i] = True
                        lvl_buy[i], sl_buy[i], tp_buy[i], rr_buy[i] = entry, sl, tp, rr
                    else:
                        sig_sell[i] = True
                        lvl_sell[i], sl_sell[i], tp_sell[i], rr_sell[i] = entry, sl, tp, rr

        df["rf_sig_buy"] = sig_buy
        df["rf_sig_sell"] = sig_sell
        df["rf_lvl_buy"] = lvl_buy
        df["rf_sl_buy"] = sl_buy
        df["rf_tp_buy"] = tp_buy
        df["rf_rr_buy"] = rr_buy
        df["rf_lvl_sell"] = lvl_sell
        df["rf_sl_sell"] = sl_sell
        df["rf_tp_sell"] = tp_sell
        df["rf_rr_sell"] = rr_sell
        return df

    # ------------------------------------------------------------------
    def check_signal(self, df: pd.DataFrame):
        if len(df) < 3:
            return None
        sig = df.iloc[-2]
        try:
            buy = bool(sig.get("rf_sig_buy", False))
            sell = bool(sig.get("rf_sig_sell", False))
        except Exception:
            return None
        if not buy and not sell:
            return None
        if buy and sell:
            # chọn tín hiệu có R:R cao hơn
            rb = sig.get("rf_rr_buy", np.nan)
            rs = sig.get("rf_rr_sell", np.nan)
            return "BUY" if (rb if np.isfinite(rb) else -1) >= (rs if np.isfinite(rs) else -1) else "SELL"
        return "BUY" if buy else "SELL"

    # ------------------------------------------------------------------
    def _pick_signal(self, sig):
        """Trả về ('BUY'/'SELL'/None, lvl, sl, tp) từ nến tín hiệu."""
        try:
            buy = bool(sig.get("rf_sig_buy", False))
            sell = bool(sig.get("rf_sig_sell", False))
        except Exception:
            return None, np.nan, np.nan, np.nan
        if not buy and not sell:
            return None, np.nan, np.nan, np.nan
        side = None
        if buy and sell:
            rb = sig.get("rf_rr_buy", np.nan)
            rs = sig.get("rf_rr_sell", np.nan)
            side = "BUY" if (rb if np.isfinite(rb) else -1) >= (rs if np.isfinite(rs) else -1) else "SELL"
        else:
            side = "BUY" if buy else "SELL"
        if side == "BUY":
            return side, float(sig["rf_lvl_buy"]), float(sig["rf_sl_buy"]), float(sig["rf_tp_buy"])
        return side, float(sig["rf_lvl_sell"]), float(sig["rf_sl_sell"]), float(sig["rf_tp_sell"])

    def get_pending_setup(self, df: pd.DataFrame):
        """Khi entry_mode = 'limit': đặt LỆNH CHỜ LIMIT tại biên FVG (đúng 'Entry' của TV)."""
        if self.entry_mode != "limit" or len(df) < 3:
            return None
        side, lvl, sl, tp = self._pick_signal(df.iloc[-2])
        if side is None or not all(np.isfinite((lvl, sl, tp))):
            return None
        if side == "BUY" and not (sl < lvl < tp):
            return None
        if side == "SELL" and not (tp < lvl < sl):
            return None
        return {"type": side, "level": float(lvl), "sl": float(sl), "tp": float(tp), "wait_min": self.pend_min}

    # ------------------------------------------------------------------
    def get_sl_tp(self, df, entry_price, digits, order_type):
        if len(df) < 3:
            return None, None
        sig = df.iloc[-2]
        try:
            if order_type == "BUY":
                sl = float(sig["rf_sl_buy"])
                tp = float(sig["rf_tp_buy"])
            else:
                sl = float(sig["rf_sl_sell"])
                tp = float(sig["rf_tp_sell"])
        except Exception:
            return None, None
        if not (np.isfinite(sl) and np.isfinite(tp)):
            return None, None
        # SL/TP phải hợp lệ quanh giá vào thực tế
        if order_type == "BUY" and not (sl < entry_price < tp):
            return None, None
        if order_type == "SELL" and not (tp < entry_price < sl):
            return None, None
        return round(sl, int(digits)), round(tp, int(digits))
