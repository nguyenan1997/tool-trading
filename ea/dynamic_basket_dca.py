"""
ea/dynamic_basket_dca.py
================================================================
BOT Python mô phỏng ĐÚNG EA "Dynamic_Basket_DCA_V1" (MT5, XAUUSD, M1).

Input EA gốc  ->  tham số bot:
  1. MagicNumber ....................... magic
  2. InitialLot ........................ initial_lot        (0.01)
  3. Total distance BUY<->SELL STOP .... total_dist         (1.0 -> ±0.5)
  4. RepositionPending ................. reposition_pending (true)
  5. Move pending framework ...........  move_framework     (0.5)
  6. Server protection ................. server_protection  (2)
  7. Distance last same-direction entry  dca_distance       (1.25)
  8. MaxDcaLevels ...................... max_dca_levels     (20)
  9. UseFibonacciLots .................. use_fibonacci      (true)
 10. LotMultiplierFallback ............. lot_mult_fallback  (1.6)
 11. Account currency (0=near BE) ...... be_currency        (0.1)
 12. Extra safety profit ............... extra_safety       (0.05)
 13. Put common TP on positions ........ common_tp          (true)
 14. Also close by live total P/L ...... close_by_live_pl   (true)
 15. SlippagePoints .................... slippage_points    (30)
 16. PrintDebug ........................ debug              (true)

Luật:
  - Đặt straddle BUY STOP + SELL STOP cách nhau `total_dist`; reposition khi
    thị trường đi >= `move_framework`.
  - Bên nào khớp -> HỦY bên còn lại. Lệnh đơn: TP = entry ± (be_currency+extra_safety).
  - Giá đi ngược `dca_distance` so với lệnh cùng chiều gần nhất -> nhồi thêm
    (tối đa `max_dca_levels`), lot Fibonacci (hoặc ×`lot_mult_fallback`).
  - TP chung = giá vào bình quân ± offset (đặt trên mọi lệnh).
  - Đóng thêm khi tổng P/L nổi đạt ngưỡng (close_by_live_pl).
  - KHÔNG Stop Loss. Chỉ demo.

Chạy:
    python ea/dynamic_basket_dca.py
    python ea/dynamic_basket_dca.py --magic 999001 --max-dca-levels 8
    python ea/dynamic_basket_dca.py --help
================================================================
"""
from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass

import MetaTrader5 as mt5

FIB = [1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610, 987, 1597, 2584, 4181]


@dataclass
class Config:
    terminal_path: str = r"C:\Program Files\MetaTrader 5-2\terminal64.exe"
    symbol: str = "XAUUSD."
    magic: int = 26093001

    initial_lot: float = 0.01
    total_dist: float = 1.0            # khoảng cách tổng BUY STOP <-> SELL STOP
    reposition_pending: bool = True    # tự đặt lại straddle khi giá chạy
    move_framework: float = 0.5        # giá chạy bao nhiêu thì reposition
    server_protection: int = 2         # số lần thử lại khi sàn từ chối
    dca_distance: float = 1.25         # bước nhồi DCA
    max_dca_levels: int = 20           # trần số cấp DCA
    use_fibonacci: bool = True         # lot Fibonacci
    lot_mult_fallback: float = 1.6     # nếu không dùng Fibonacci
    be_currency: float = 0.1           # account currency (0 = near breakeven)
    extra_safety: float = 0.05         # extra safety profit
    common_tp: bool = True             # đặt TP chung cho các vị thế
    close_by_live_pl: bool = True      # đóng theo tổng P/L nổi
    slippage_points: int = 30
    debug: bool = True

    poll_sec: float = 0.5
    requote_sec: float = 5.0


def setup_logger(debug: bool) -> logging.Logger:
    lg = logging.getLogger("PyBasketDCA")
    lg.handlers.clear()
    lg.setLevel(logging.DEBUG if debug else logging.INFO)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", "%Y-%m-%d %H:%M:%S")
    ch = logging.StreamHandler(); ch.setFormatter(fmt); lg.addHandler(ch)
    fh = logging.FileHandler("ea_basket_dca.log", encoding="utf-8"); fh.setFormatter(fmt); lg.addHandler(fh)
    return lg


class DynamicBasketDCA:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.log = setup_logger(cfg.debug)
        self.info = None
        self.digits = 2
        self.point = 0.01
        self.vol_min = 0.01
        self.vol_step = 0.01
        self.vol_max = 100.0
        self._ref_mid = None      # gia tham chieu khi dat straddle (reposition)
        self._last_target = None

    # ---------------- connection ----------------
    def connect(self) -> bool:
        if not mt5.initialize(path=self.cfg.terminal_path):
            self.log.error(f"MT5 initialize failed -> {mt5.last_error()}"); return False
        acc = mt5.account_info()
        if acc is None:
            self.log.error("no account_info"); return False
        info = mt5.symbol_info(self.cfg.symbol)
        if info is None:
            self.log.error(f"symbol {self.cfg.symbol} not found"); return False
        if not info.visible:
            mt5.symbol_select(self.cfg.symbol, True); info = mt5.symbol_info(self.cfg.symbol)
        self.info = info
        self.digits = info.digits; self.point = info.point
        self.vol_min = info.volume_min; self.vol_step = info.volume_step; self.vol_max = info.volume_max
        self.log.info(f"CONNECTED | acc={acc.login} {acc.company} | {self.cfg.symbol} "
                      f"digits={info.digits} spread={info.spread}pts | balance={acc.balance:.2f}")
        return True

    # ---------------- helpers ----------------
    def positions(self):
        ps = mt5.positions_get(symbol=self.cfg.symbol) or []
        return [p for p in ps if p.magic == self.cfg.magic]

    def pendings(self):
        os_ = mt5.orders_get(symbol=self.cfg.symbol) or []
        return [o for o in os_ if o.magic == self.cfg.magic]

    def _send(self, req, label=""):
        info = self.info
        fm = getattr(info, "filling_mode", 0) or 0
        req["type_filling"] = (mt5.ORDER_FILLING_IOC if fm & getattr(mt5, "SYMBOL_FILLING_IOC", 2)
                               else mt5.ORDER_FILLING_FOK if fm & getattr(mt5, "SYMBOL_FILLING_FOK", 1)
                               else mt5.ORDER_FILLING_RETURN)
        res = mt5.order_send(req)
        if res is not None and res.retcode == mt5.TRADE_RETCODE_INVALID_FILL:
            for f in (mt5.ORDER_FILLING_RETURN, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK):
                req["type_filling"] = f; res = mt5.order_send(req)
                if res is None or res.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
                    break
        if self.cfg.debug and res is not None and res.retcode != mt5.TRADE_RETCODE_DONE:
            self.log.debug(f"order_send {label}: retcode={res.retcode} {res.comment}")
        return res

    def dca_lot(self, level: int) -> float:
        if self.cfg.use_fibonacci:
            f = FIB[level] if level < len(FIB) else FIB[-1] * (2 ** (level - len(FIB) + 1))
            lot = self.cfg.initial_lot * f
        else:
            lot = self.cfg.initial_lot * (self.cfg.lot_mult_fallback ** level)
        lot = round(round(lot / self.vol_step) * self.vol_step, 2)
        return max(self.vol_min, min(lot, self.vol_max))

    def profit_target_usd(self) -> float:
        """Mục tiêu lãi CHUNG của rổ (account currency) = be_currency + extra_safety.
        Đã kiểm chứng từ lịch sử: EA luôn đóng khi lãi rổ đạt ~0.15$."""
        return self.cfg.be_currency + self.cfg.extra_safety

    def live_pl_target(self, n: int) -> float:
        return self.profit_target_usd()

    # ---------------- orders ----------------
    def _place_stop(self, side: str, price: float, lot: float) -> bool:
        req = {"action": mt5.TRADE_ACTION_PENDING, "symbol": self.cfg.symbol, "volume": lot,
               "type": mt5.ORDER_TYPE_BUY_STOP if side == "BUY" else mt5.ORDER_TYPE_SELL_STOP,
               "price": round(price, self.digits), "sl": 0.0, "tp": 0.0, "magic": self.cfg.magic,
               "comment": f"PyDCA_INIT_{side}", "type_time": mt5.ORDER_TIME_GTC}
        r = self._send(req, f"{side} stop")
        return r is not None and r.retcode == mt5.TRADE_RETCODE_DONE

    def place_straddle(self):
        tick = mt5.symbol_info_tick(self.cfg.symbol)
        if tick is None:
            return
        half = self.cfg.total_dist / 2.0
        mid = (tick.ask + tick.bid) / 2.0
        min_gap = (self.info.trade_stops_level or 0) * self.point
        buy = max(mid + half, tick.ask + min_gap)
        sell = min(mid - half, tick.bid - min_gap)
        ok_b = self._place_stop("BUY", buy, self.cfg.initial_lot)
        ok_s = self._place_stop("SELL", sell, self.cfg.initial_lot)
        self._ref_mid = (tick.ask + tick.bid) / 2.0
        if self.cfg.debug:
            self.log.info(f"STRADDLE  BUY {buy:.{self.digits}f} / SELL {sell:.{self.digits}f} "
                          f"(dist {self.cfg.total_dist})  ok={ok_b},{ok_s}")

    def cancel_pendings(self):
        for o in self.pendings():
            mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})

    def _market(self, side: str, lot: float, comment: str) -> bool:
        tick = mt5.symbol_info_tick(self.cfg.symbol)
        price = tick.ask if side == "BUY" else tick.bid
        req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": self.cfg.symbol, "volume": lot,
               "type": mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL,
               "price": price, "sl": 0.0, "tp": 0.0, "magic": self.cfg.magic,
               "comment": comment, "type_time": mt5.ORDER_TIME_GTC, "deviation": self.cfg.slippage_points}
        r = self._send(req, comment)
        return r is not None and r.retcode == mt5.TRADE_RETCODE_DONE

    def set_common_tp(self, positions):
        if not self.cfg.common_tp:
            return
        total = sum(p.volume for p in positions)
        wavg = sum(p.price_open * p.volume for p in positions) / total
        side = positions[0].type
        contract = self.info.trade_contract_size or 100.0
        # TP = binh quan ± (muc tieu USD / (tong lot × contract))
        off = self.profit_target_usd() / (total * contract) if total > 0 else 0.0
        target = round(wavg + off if side == 0 else wavg - off, self.digits)
        if self._last_target is not None and abs(target - self._last_target) < 1e-9:
            return
        for p in positions:
            if abs((p.tp or 0.0) - target) < 1e-9:
                continue
            mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "symbol": self.cfg.symbol,
                            "position": p.ticket, "sl": 0.0, "tp": target})
        self._last_target = target
        if self.cfg.debug:
            self.log.info(f"COMMON TP {target:.{self.digits}f} (wavg {wavg:.{self.digits}f}, "
                          f"{len(positions)} lenh, {total:.2f} lot)")

    def close_all(self, reason="close all"):
        for _ in range(max(1, self.cfg.server_protection) * 10):
            rest = self.positions()
            if not rest:
                break
            for p in rest:
                tick = mt5.symbol_info_tick(self.cfg.symbol)
                price = tick.bid if p.type == 0 else tick.ask
                mt5.order_send({"action": mt5.TRADE_ACTION_DEAL, "symbol": self.cfg.symbol,
                                "volume": p.volume, "position": p.ticket,
                                "type": mt5.ORDER_TYPE_SELL if p.type == 0 else mt5.ORDER_TYPE_BUY,
                                "price": price, "magic": self.cfg.magic, "comment": reason,
                                "type_time": mt5.ORDER_TIME_GTC, "deviation": self.cfg.slippage_points})
            time.sleep(0.2)
        self.cancel_pendings()
        self._last_target = None
        self._ref_mid = None

    # ---------------- main step ----------------
    def step_once(self):
        positions = self.positions()
        pendings = self.pendings()

        # --- dang co ro ---
        if positions:
            if pendings:
                self.cancel_pendings()
            sides = {p.type for p in positions}
            self.set_common_tp(positions)

            # dong theo tong P/L noi
            if self.cfg.close_by_live_pl:
                fl = sum(p.profit for p in positions)
                if fl >= self.live_pl_target(len(positions)):
                    self.log.info(f"CLOSE by live P/L {fl:.2f} >= {self.live_pl_target(len(positions)):.2f}")
                    self.close_all("PyDCA_livePL"); return

            # nhieu huong -> khong DCA (giong EA)
            if len(sides) > 1:
                if self.cfg.debug:
                    self.log.warning("mixed directions -> no DCA")
                return

            # DCA
            if len(positions) < self.cfg.max_dca_levels:
                side = positions[0].type
                tick = mt5.symbol_info_tick(self.cfg.symbol)
                if side == 0:
                    last = min(p.price_open for p in positions)
                    if tick.bid <= last - self.cfg.dca_distance:
                        lot = self.dca_lot(len(positions))
                        if self._market("BUY", lot, "PyDCA_DCA_BUY"):
                            self._last_target = None
                            self.log.info(f"DCA #{len(positions)+1} BUY lot {lot}")
                else:
                    last = max(p.price_open for p in positions)
                    if tick.ask >= last + self.cfg.dca_distance:
                        lot = self.dca_lot(len(positions))
                        if self._market("SELL", lot, "PyDCA_DCA_SELL"):
                            self._last_target = None
                            self.log.info(f"DCA #{len(positions)+1} SELL lot {lot}")
            return

        # --- dang cho straddle ---
        if pendings:
            if self.cfg.reposition_pending:
                tick = mt5.symbol_info_tick(self.cfg.symbol)
                if tick is not None and self._ref_mid is not None:
                    mid = (tick.ask + tick.bid) / 2.0
                    if abs(mid - self._ref_mid) >= self.cfg.move_framework:
                        self.cancel_pendings()
                        self.place_straddle()
            return

        # --- flat -> mo chu ky moi ---
        self._last_target = None
        self.place_straddle()

    def run(self):
        if not self.connect():
            return
        self.log.info("=== START PyBasketDCA (Dynamic_Basket_DCA_V1 port) ===")
        try:
            while True:
                try:
                    self.step_once()
                except Exception as e:
                    self.log.error(f"loop error: {e}")
                time.sleep(self.cfg.poll_sec)
        except KeyboardInterrupt:
            self.log.info("STOP (Ctrl+C). Vi the/lenh cho giu nguyen.")


def parse_args() -> Config:
    c = Config()
    p = argparse.ArgumentParser(description="Dynamic Basket DCA V1 - Python port")
    p.add_argument("--terminal", default=c.terminal_path)
    p.add_argument("--symbol", default=c.symbol)
    p.add_argument("--magic", type=int, default=c.magic)
    p.add_argument("--initial-lot", type=float, default=c.initial_lot)
    p.add_argument("--total-dist", type=float, default=c.total_dist)
    p.add_argument("--move-framework", type=float, default=c.move_framework)
    p.add_argument("--server-protection", type=int, default=c.server_protection)
    p.add_argument("--dca-distance", type=float, default=c.dca_distance)
    p.add_argument("--max-dca-levels", type=int, default=c.max_dca_levels)
    p.add_argument("--lot-mult-fallback", type=float, default=c.lot_mult_fallback)
    p.add_argument("--be-currency", type=float, default=c.be_currency)
    p.add_argument("--extra-safety", type=float, default=c.extra_safety)
    p.add_argument("--slippage-points", type=int, default=c.slippage_points)
    p.add_argument("--no-fib", action="store_true")
    p.add_argument("--no-common-tp", action="store_true")
    p.add_argument("--no-live-pl", action="store_true")
    p.add_argument("--no-reposition", action="store_true")
    p.add_argument("--quiet", action="store_true")
    a = p.parse_args()
    c.terminal_path = a.terminal; c.symbol = a.symbol; c.magic = a.magic
    c.initial_lot = a.initial_lot; c.total_dist = a.total_dist
    c.move_framework = a.move_framework; c.server_protection = a.server_protection
    c.dca_distance = a.dca_distance; c.max_dca_levels = a.max_dca_levels
    c.lot_mult_fallback = a.lot_mult_fallback; c.be_currency = a.be_currency
    c.extra_safety = a.extra_safety; c.slippage_points = a.slippage_points
    c.use_fibonacci = not a.no_fib; c.common_tp = not a.no_common_tp
    c.close_by_live_pl = not a.no_live_pl; c.reposition_pending = not a.no_reposition
    c.debug = not a.quiet
    return c


if __name__ == "__main__":
    DynamicBasketDCA(parse_args()).run()
