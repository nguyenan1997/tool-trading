"""
strategies/asian_sweep.py
HỆ THỐNG 2 — Asian Sweep (ICT). ĐỘC LẬP HOÀN TOÀN với Trend Momentum.

Ý tưởng:
- Vùng tích lũy Á: 04:00–07:59 (giờ broker).
- Killzone sớm Âu: 10:00–11:59 (giờ broker) — NGOÀI khung TM (12–21).
- Trong killzone: giá QUÉT biên vùng Á (thủng đáy / vượt đỉnh) rồi đóng nến
  "reclaim" trở lại trong vùng → đặt LỆNH CHỜ LIMIT hồi 50% cây reclaim.
- SL = điểm quét ± 0.2×ATR(M15); TP = biên đối diện vùng Á.
- Quản lý: chốt một phần 50% @1R + dời SL hòa vốn (do BotEngine thực thi).
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


class AsianSweepStrategy(BaseStrategy):
    def __init__(
        self,
        range_start=config.AS_RANGE_START,
        range_end=config.AS_RANGE_END,
        kz_start=config.AS_KZ_START,
        kz_end=config.AS_KZ_END,
        retrace=config.AS_RETRACE,
        use_bias=config.AS_USE_BIAS,
        bias_ema=config.AS_BIAS_EMA,
        tp_mode=config.AS_TP_MODE,
        tp_r=config.AS_TP_R,
        wait_min=config.AS_WAIT_MIN,
        sl_buf_atr=config.AS_SL_BUF_ATR,
        min_sweep_atr=config.AS_MIN_SWEEP_ATR,
        atr_lo=config.AS_ATR_LO,
        atr_hi=config.AS_ATR_HI,
        history_bars=config.AS_HISTORY_BARS,
        partial_frac=config.AS_PARTIAL_FRAC,
        partial_at_r=config.AS_PARTIAL_AT_R,
        be_move_at_r=config.AS_BE_AT_R,
        magic=config.MAGIC_ASIAN,
    ):
        super().__init__("Asian Sweep (ICT)", magic=magic)
        self.range_start = range_start
        self.range_end = range_end
        self.kz_start = kz_start
        self.kz_end = kz_end
        self.retrace = retrace
        self.use_bias = use_bias
        self.bias_ema = bias_ema
        self.tp_mode = tp_mode
        self.tp_r = tp_r
        self.wait_min = wait_min
        self.sl_buf_atr = sl_buf_atr
        self.min_sweep_atr = min_sweep_atr
        self.atr_lo = atr_lo
        self.atr_hi = atr_hi
        self.history_bars = history_bars
        self.partial_frac = partial_frac
        self.partial_at_r = partial_at_r
        self.be_move_at_r = be_move_at_r
        self.lot = config.AS_LOT
        self.comment = config.AS_COMMENT

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if len(df) < 60:
            return df

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

        # ATR(M15) — nến M15 đã đóng
        m15 = (
            tmp.set_index("ts")
            .resample("15min")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna()
        )
        m15["atr15"] = _atr(m15, 14).shift(1)
        m15 = m15.reset_index().rename(columns={"index": "ts"})
        m15 = m15[["ts", "atr15"]]

        base = pd.merge_asof(tmp, m15, on="ts", direction="backward")

        # Bias H4 (EMA) — lọc xu hướng lớn (nến H4 đã đóng)
        if self.use_bias:
            h4 = (
                tmp.set_index("ts")
                .resample("4h")
                .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
                .dropna()
            )
            h4["bias_ema"] = h4["close"].ewm(span=self.bias_ema, adjust=False).mean().shift(1)
            h4 = h4.reset_index()[["ts", "bias_ema"]]
            base = pd.merge_asof(base, h4, on="ts", direction="backward")

        base["hour"] = base["ts"].dt.hour
        base["date"] = base["ts"].dt.date

        # Biên vùng Á theo ngày
        in_range = base["hour"].between(self.range_start, self.range_end)
        rng = (
            base[in_range]
            .groupby("date")
            .agg(rhi=("high", "max"), rlo=("low", "min"))
        )
        base["rhi"] = base["date"].map(rng["rhi"])
        base["rlo"] = base["date"].map(rng["rlo"])

        # Quét biên trong killzone (chỉ tính trong killzone, giống nghiên cứu)
        in_kz = base["hour"].between(self.kz_start, self.kz_end)
        base["_lo_kz"] = base["low"].where(in_kz)
        base["_hi_kz"] = base["high"].where(in_kz)
        base["day_low"] = base.groupby("date")["_lo_kz"].cummin()
        base["day_high"] = base.groupby("date")["_hi_kz"].cummax()

        sweep_off = self.min_sweep_atr * base["atr15"]
        base["swept_low"] = in_kz & (base["low"] < (base["rlo"] - sweep_off))
        base["swept_high"] = in_kz & (base["high"] > (base["rhi"] + sweep_off))
        base["swept_low_cum"] = base.groupby("date")["swept_low"].cummax()
        base["swept_high_cum"] = base.groupby("date")["swept_high"].cummax()

        raw_buy = in_kz & base["swept_low_cum"] & (base["close"] > base["rlo"])
        raw_sell = in_kz & base["swept_high_cum"] & (base["close"] < base["rhi"])
        if self.use_bias:
            raw_buy = raw_buy & (base["close"] > base["bias_ema"])
            raw_sell = raw_sell & (base["close"] < base["bias_ema"])
        if self.atr_lo > 0 or self.atr_hi < 1:
            atr_rank = base["atr15"].rolling(1440).rank(pct=True)
            band = atr_rank.between(self.atr_lo, self.atr_hi)
            raw_buy = raw_buy & band
            raw_sell = raw_sell & band
        base["sig_buy"] = raw_buy & (raw_buy.groupby(base["date"]).cumsum() == 1)
        base["sig_sell"] = raw_sell & (raw_sell.groupby(base["date"]).cumsum() == 1)

        # Mức vào (limit hồi), SL, TP
        base["lvl_buy"] = base["day_low"] + self.retrace * (base["close"] - base["day_low"])
        base["sl_buy"] = base["day_low"] - self.sl_buf_atr * base["atr15"]
        base["tp_buy"] = base["rhi"]
        base["lvl_sell"] = base["day_high"] - self.retrace * (base["day_high"] - base["close"])
        base["sl_sell"] = base["day_high"] + self.sl_buf_atr * base["atr15"]
        base["tp_sell"] = base["rlo"]

        cols = (
            "atr15", "hour", "rhi", "rlo", "day_low", "day_high",
            "sig_buy", "sig_sell",
            "lvl_buy", "sl_buy", "tp_buy", "lvl_sell", "sl_sell", "tp_sell",
        )
        for col in cols:
            df[col] = base[col].to_numpy()
        return df

    def check_signal(self, df: pd.DataFrame) -> str | None:
        # Chiến lược này dùng lệnh chờ limit (get_pending_setup), không vào market.
        return None

    def get_pending_setup(self, df: pd.DataFrame):
        if len(df) < 3:
            return None
        sig = df.iloc[-2]
        try:
            if bool(sig["sig_buy"]):
                lvl, sl = float(sig["lvl_buy"]), float(sig["sl_buy"])
                tp = self._tp("BUY", lvl, sl, float(sig["tp_buy"]))
                if all(np.isfinite((lvl, sl, tp))) and tp > lvl > sl:
                    return {"type": "BUY", "level": lvl, "sl": sl, "tp": tp, "wait_min": self.wait_min}
            elif bool(sig["sig_sell"]):
                lvl, sl = float(sig["lvl_sell"]), float(sig["sl_sell"])
                tp = self._tp("SELL", lvl, sl, float(sig["tp_sell"]))
                if all(np.isfinite((lvl, sl, tp))) and sl > lvl > tp:
                    return {"type": "SELL", "level": lvl, "sl": sl, "tp": tp, "wait_min": self.wait_min}
        except Exception:
            return None
        return None

    def _tp(self, typ, level, sl, range_tp):
        """TP theo cấu hình: 'range' = biên đối diện vùng Á; 'R' = bội số R."""
        if str(self.tp_mode).upper() == "R":
            r = abs(level - sl)
            return level + self.tp_r * r if typ == "BUY" else level - self.tp_r * r
        return range_tp

    def get_sl_tp(self, df, entry_price, digits, order_type):
        # Không dùng (entry qua lệnh chờ). Giữ để thoả interface.
        return None, None
