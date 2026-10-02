"""
ea/dynamic_basket_dca.py
================================================================
Bản Python của EA "Dynamic_Basket_DCA_V1" (chạy trên MT5).

Quy tắc (dựng lại từ lịch sử giao dịch acc 91482104):
  1. STRADDLE: đặt 1 BUY STOP + 1 SELL STOP quanh giá, mỗi bên cách
     `INIT_DIST` (mặc định 0.50). Lot khởi đầu 0.01.
  2. Bên nào chạm trước -> vào lệnh; HỦY ngay lệnh còn lại.
     Lệnh đơn: TP = giá vào ± TP_INITIAL (0.15).
  3. DCA: mỗi khi giá đi NGƯỢC thêm `STEP` (1.00) so với lệnh cùng chiều
     gần nhất -> nhồi thêm 1 lệnh cùng chiều, lot theo dãy FIBONACCI:
        0.01, 0.02, 0.03, 0.05, 0.08, 0.13, 0.21, 0.34, 0.55, 0.89, ...
  4. TP ĐỘNG theo rổ: sau mỗi lần nhồi, TP = GIÁ VÀO BÌNH QUÂN GIA QUYỀN
     ± TP_BASKET (0.05). Chạm -> toàn bộ rổ đóng (broker khớp TP) -> RESET.
  5. KHÔNG Stop Loss. Chỉ thoát khi đạt TP.

⚠️ CẢNH BÁO: đây là martingale/DCA không SL. Nếu giá trend 1 chiều không hồi,
lot tăng theo Fibonacci và có thể CHÁY tài khoản. Chỉ chạy demo trước.

Cách dùng:
    python ea/dynamic_basket_dca.py                 # chạy với tham số mặc định
    python ea/dynamic_basket_dca.py --symbol XAUUSD --init-dist 0.5 --step 1.0
    python ea/dynamic_basket_dca.py --max-levels 8  # chặn nhồi quá 8 cấp (an toàn hơn)
    python ea/dynamic_basket_dca.py --help
================================================================
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import MetaTrader5 as mt5

# ----------------------------------------------------------------------
#  Cấu hình mặc định (khớp EA gốc trên acc 91482104)
# ----------------------------------------------------------------------
@dataclass
class Config:
    terminal_path: str = r"C:\Program Files\MetaTrader 5-2\terminal64.exe"
    symbol: str = "XAUUSD."
    magic: int = 26093001

    lot0: float = 0.01          # lot lệnh đầu
    init_dist: float = 0.50     # khoảng cách stop 2 đầu (mỗi bên) so với giá
    step: float = 1.00          # giá đi ngược thêm bao nhiêu thì nhồi DCA
    tp_initial: float = 0.15    # TP lệnh đơn (chưa nhồi)
    tp_basket: float = 0.05     # TP rổ tính từ giá vào bình quân

    max_levels: int = 0         # 0 = KHÔNG giới hạn (nguy hiểm). Đặt vd 8 để chặn.
    max_total_lot: float = 0.0  # 0 = không giới hạn tổng lot

    deviation_pts: int = 30     # trượt giá tối đa (points)
    poll_sec: float = 0.5       # chu kỳ vòng lặp
    requote_sec: float = 5.0    # nếu stop 2 đầu chưa khớp sau bao lâu thì đặt lại
    verbose: bool = True


# ----------------------------------------------------------------------
#  Tiện ích
# ----------------------------------------------------------------------
FIB = [1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610, 987]


def fib_lot(level: int, lot0: float = 0.01) -> float:
    """Lot theo dãy Fibonacci: level 0 -> 0.01, 1 -> 0.02, 2 -> 0.03, 3 -> 0.05 ..."""
    f = FIB[level] if level < len(FIB) else FIB[-1] * (2 ** (level - len(FIB) + 1))
    return round(f * lot0, 2)


def setup_logger(verbose: bool) -> logging.Logger:
    logger = logging.getLogger("basket_dca")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", "%Y-%m-%d %H:%M:%S"))
        logger.addHandler(ch)
        fh = logging.FileHandler("ea_basket_dca.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", "%Y-%m-%d %H:%M:%S"))
        logger.addHandler(fh)
    if not verbose:
        logger.setLevel(logging.INFO)
    return logger


# ----------------------------------------------------------------------
#  EA
# ----------------------------------------------------------------------
class DynamicBasketDCA:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.log = setup_logger(cfg.verbose)
        self.info = None
        self.digits = 2
        self.point = 0.01
        self.vol_min = 0.01
        self.vol_step = 0.01

        self.pending_time = 0.0     # thời điểm đặt straddle (để re-quote)
        self.level = 0              # số lệnh trong rổ hiện tại
        self.last_target = None     # TP đã set lần trước (tránh set lại liên tục)

    # ---------- kết nối / thông tin ----------
    def connect(self) -> bool:
        if not mt5.initialize(path=self.cfg.terminal_path):
            self.log.error(f"MT5 initialize() failed -> {mt5.last_error()}")
            return False
        acc = mt5.account_info()
        if acc is None:
            self.log.error("Không lấy được account_info.")
            return False
        if not (acc.trade_allowed and acc.trade_expert):
            self.log.warning("Tài khoản/terminal CHƯA cho phép giao dịch (Algo Trading?).")
        info = mt5.symbol_info(self.cfg.symbol)
        if info is None:
            self.log.error(f"Không có symbol {self.cfg.symbol}")
            return False
        if not info.visible:
            mt5.symbol_select(self.cfg.symbol, True)
            info = mt5.symbol_info(self.cfg.symbol)
        self.info = info
        self.digits = info.digits
        self.point = info.point
        self.vol_min = info.volume_min
        self.vol_step = info.volume_step
        self.log.info(
            f"Kết nối OK | acc={acc.login} ({acc.company}) | {self.cfg.symbol} "
            f"digits={info.digits} spread={info.spread}pts | balance={acc.balance:.2f}"
        )
        return True

    def _send(self, request: dict, info=None):
        info = info or self.info
        fm = getattr(info, "filling_mode", 0) or 0
        request["type_filling"] = (
            mt5.ORDER_FILLING_IOC if fm & getattr(mt5, "SYMBOL_FILLING_IOC", 2)
            else mt5.ORDER_FILLING_FOK if fm & getattr(mt5, "SYMBOL_FILLING_FOK", 1)
            else mt5.ORDER_FILLING_RETURN
        )
        res = mt5.order_send(request)
        if res is not None and res.retcode == mt5.TRADE_RETCODE_INVALID_FILL:
            for f in (mt5.ORDER_FILLING_RETURN, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK):
                request["type_filling"] = f
                res = mt5.order_send(request)
                if res is None or res.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
                    break
        return res

    # ---------- dữ liệu ----------
    def positions(self) -> list:
        ps = mt5.positions_get(symbol=self.cfg.symbol)
        return [p for p in (ps or []) if p.magic == self.cfg.magic]

    def pendings(self) -> list:
        os_ = mt5.orders_get(symbol=self.cfg.symbol)
        return [o for o in (os_ or []) if o.magic == self.cfg.magic]

    # ---------- đặt lệnh ----------
    def _place_stop(self, side: str, price: float, lot: float):
        req = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": self.cfg.symbol,
            "volume": lot,
            "type": mt5.ORDER_TYPE_BUY_STOP if side == "BUY" else mt5.ORDER_TYPE_SELL_STOP,
            "price": round(price, self.digits),
            "sl": 0.0, "tp": 0.0,
            "magic": self.cfg.magic,
            "comment": f"PY_INIT_{side}STOP",
            "type_time": mt5.ORDER_TIME_GTC,
        }
        res = self._send(req)
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            self.log.error(f"Đặt {side} STOP thất bại: {getattr(res,'retcode',None)} {getattr(res,'comment','')}")
            return None
        return res.order

    def place_straddle(self):
        tick = mt5.symbol_info_tick(self.cfg.symbol)
        if tick is None:
            return
        # BUY STOP phải >= ask (+stops_level), SELL STOP <= bid (-stops_level)
        min_gap = (self.info.trade_stops_level or 0) * self.point
        buy_price = max(tick.ask + self.cfg.init_dist, tick.ask + min_gap)
        sell_price = min(tick.bid - self.cfg.init_dist, tick.bid - min_gap)
        b = self._place_stop("BUY", buy_price, self.cfg.lot0)
        s = self._place_stop("SELL", sell_price, self.cfg.lot0)
        self.pending_time = time.time()
        self.level = 0
        self.last_target = None
        if b or s:
            self.log.info(f"Đặt STRADDLE | BUY STOP {buy_price:.{self.digits}f} | SELL STOP {sell_price:.{self.digits}f} | lot {self.cfg.lot0}")

    def cancel_pending(self):
        for o in self.pendings():
            req = {"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket}
            mt5.order_send(req)

    def _market(self, side: str, lot: float, comment: str):
        tick = mt5.symbol_info_tick(self.cfg.symbol)
        price = tick.ask if side == "BUY" else tick.bid
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.cfg.symbol,
            "volume": lot,
            "type": mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": 0.0, "tp": 0.0,
            "magic": self.cfg.magic,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "deviation": self.cfg.deviation_pts,
        }
        res = self._send(req)
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            self.log.error(f"{comment} thất bại: {getattr(res,'retcode',None)} {getattr(res,'comment','')}")
            return None
        return res.order

    def add_dca(self, side: str, level: int):
        lot = fib_lot(level, self.cfg.lot0)
        lot = max(self.vol_min, round(round(lot / self.vol_step) * self.vol_step, 2))
        if self.cfg.max_total_lot > 0:
            total = sum(p.volume for p in self.positions())
            if total + lot > self.cfg.max_total_lot:
                self.log.warning(f"BỎ DCA: tổng lot vượt giới hạn {self.cfg.max_total_lot}")
                return
        if self._market(side, lot, f"PY_DCA_{side}"):
            self.log.info(f"DCA #{level+1} {side} | lot {lot} | giá {mt5.symbol_info_tick(self.cfg.symbol).bid if side=='BUY' else mt5.symbol_info_tick(self.cfg.symbol).ask}")

    def set_basket_tp(self, positions: list):
        total_vol = sum(p.volume for p in positions)
        if total_vol <= 0:
            return
        wavg = sum(p.price_open * p.volume for p in positions) / total_vol
        side = positions[0].type  # 0 BUY, 1 SELL
        tp_dist = self.cfg.tp_initial if len(positions) == 1 else self.cfg.tp_basket
        target = wavg + tp_dist if side == 0 else wavg - tp_dist
        target = round(target, self.digits)
        if self.last_target is not None and abs(target - self.last_target) < 1e-9:
            return
        for p in positions:
            if abs((p.tp or 0.0) - target) < 1e-9:
                continue
            req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": self.cfg.symbol,
                   "position": p.ticket, "sl": 0.0, "tp": target}
            mt5.order_send(req)
        self.last_target = target
        self.log.info(f"TP rổ = {target:.{self.digits}f} (wavg {wavg:.{self.digits}f}, {len(positions)} lệnh, {total_vol:.2f} lot)")

    # ---------- vòng lặp ----------
    def step_once(self):
        positions = self.positions()
        pendings = self.pendings()

        # --- ĐANG CÓ RỔ ---
        if positions:
            if pendings:
                self.cancel_pending()
            side = positions[0].type
            self.level = len(positions)
            self.set_basket_tp(positions)

            # kiểm tra nhồi DCA
            if self.cfg.max_levels <= 0 or len(positions) < self.cfg.max_levels:
                tick = mt5.symbol_info_tick(self.cfg.symbol)
                if side == 0:      # BUY: giá xuống thêm STEP so với lệnh thấp nhất
                    last_entry = min(p.price_open for p in positions)
                    if tick.bid <= last_entry - self.cfg.step:
                        self.add_dca("BUY", len(positions))
                else:              # SELL: giá lên thêm STEP so với lệnh cao nhất
                    last_entry = max(p.price_open for p in positions)
                    if tick.ask >= last_entry + self.cfg.step:
                        self.add_dca("SELL", len(positions))
            return

        # --- KHÔNG CÓ RỔ, ĐANG CHỜ STRADDLE ---
        if pendings:
            # nếu quá lâu chưa khớp -> đặt lại gần giá (re-quote)
            if self.cfg.requote_sec > 0 and time.time() - self.pending_time > self.cfg.requote_sec:
                self.cancel_pending()
                self.place_straddle()
            return

        # --- FLAT -> mở chu kỳ mới ---
        self.place_straddle()

    def run(self):
        if not self.connect():
            return
        self.log.info("=== BẮT ĐẦU Dynamic Basket DCA (Python) ===")
        try:
            while True:
                try:
                    self.step_once()
                except Exception as e:
                    self.log.error(f"Lỗi vòng lặp: {e}")
                time.sleep(self.cfg.poll_sec)
        except KeyboardInterrupt:
            self.log.info("Dừng theo yêu cầu (Ctrl+C). Vị thế/lệnh chờ GIỮ NGUYÊN.")


# ----------------------------------------------------------------------
#  CLI
# ----------------------------------------------------------------------
def parse_args() -> Config:
    c = Config()
    p = argparse.ArgumentParser(description="Dynamic Basket DCA V1 - Python port")
    p.add_argument("--terminal", default=c.terminal_path)
    p.add_argument("--symbol", default=c.symbol)
    p.add_argument("--magic", type=int, default=c.magic)
    p.add_argument("--lot", type=float, default=c.lot0)
    p.add_argument("--init-dist", type=float, default=c.init_dist)
    p.add_argument("--step", type=float, default=c.step)
    p.add_argument("--tp-initial", type=float, default=c.tp_initial)
    p.add_argument("--tp-basket", type=float, default=c.tp_basket)
    p.add_argument("--max-levels", type=int, default=c.max_levels)
    p.add_argument("--max-total-lot", type=float, default=c.max_total_lot)
    p.add_argument("--poll-sec", type=float, default=c.poll_sec)
    p.add_argument("--requote-sec", type=float, default=c.requote_sec)
    p.add_argument("--quiet", action="store_true")
    a = p.parse_args()
    return Config(
        terminal_path=a.terminal, symbol=a.symbol, magic=a.magic, lot0=a.lot,
        init_dist=a.init_dist, step=a.step, tp_initial=a.tp_initial,
        tp_basket=a.tp_basket, max_levels=a.max_levels, max_total_lot=a.max_total_lot,
        poll_sec=a.poll_sec, requote_sec=a.requote_sec, verbose=not a.quiet,
    )


if __name__ == "__main__":
    DynamicBasketDCA(parse_args()).run()
