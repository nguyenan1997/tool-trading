"""
api/routes.py
Các liên kết API cho Control Center.
"""
from flask import jsonify, request, render_template
import core.mt5_handler as mt5h
import config
from datetime import datetime
import pandas as pd
import logging
import os
import re
from core.bot_engine import bot_engine
from core.hedging_engine import hedging_engine
from core.bigmouse_engine import bigmouse_engine
from strategies.manager import strategy_manager
from backtest.engine import Backtester
from backtest.data_loader import get_historical_data, get_with_warmup, _BAR_MINUTES
from strategies.ict import ICTKillzoneFVGStrategy
from strategies.hedging import HedgingStrategy
from strategies.bigmouse import BigMouseStrategy

logger = logging.getLogger(__name__)

# Nhận diện dòng access log của Flask/Werkzeug: ... "GET /api/... HTTP/1.1" 200 -
_ACCESS_LOG_RE = re.compile(r'"\s*(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+\S+\s+HTTP/\d')


def _num(data, key, default, cast=float):
    val = data.get(key)
    try:
        return cast(val) if val not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _flag(data, key, default=True):
    val = data.get(key)
    if val in (None, ""):
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _build_strategy(data):
    """Xây dựng chiến lược theo tham số từ UI ('hedging' | 'ict')."""
    sid = (data.get("strategy") or "ict").strip().lower()

    if sid == "hedging":
        # Hedging Grid không mô phỏng được bằng Backtester hiện tại (nhiều vị thế
        # 2 chiều, không SL) — chỉ chạy live. Trả object để nhận diện chiến lược.
        return HedgingStrategy(
            tp_usd=_num(data, "hedge_tp_usd", config.HEDGE_TP_USD),
            lot=_num(data, "hedge_lot", config.HEDGE_LOT),
        ), "hedging"

    if sid == "bigmouse":
        # BigMouse cũng không mô phỏng được bằng Backtester hiện tại (mỏ neo +
        # hedge stop + martingale) — chỉ chạy live.
        return BigMouseStrategy(
            direction=(data.get("bm_direction") or config.BIGMOUSE_DIRECTION),
            lot=_num(data, "bm_lot", config.BIGMOUSE_LOT),
            tp_usd=_num(data, "bm_tp_usd", config.BIGMOUSE_TP_USD),
            hedge_trigger_usd=_num(data, "bm_hedge_trigger_usd", config.BIGMOUSE_HEDGE_TRIGGER_USD),
            hedge_tp_usd=_num(data, "bm_hedge_tp_usd", config.BIGMOUSE_HEDGE_TP_USD),
            hedge_lot_mult=_num(data, "bm_hedge_lot_mult", config.BIGMOUSE_HEDGE_LOT_MULT),
            basket_tp_usd=_num(data, "bm_basket_tp_usd", config.BIGMOUSE_BASKET_TP_USD),
            martingale=_flag(data, "bm_martingale", config.BIGMOUSE_MARTINGALE),
            martingale_mult=_num(data, "bm_martingale_mult", config.BIGMOUSE_MARTINGALE_MULT),
            martingale_max_steps=_num(data, "bm_martingale_max_steps", config.BIGMOUSE_MARTINGALE_MAX_STEPS, int),
        ), "bigmouse"

    return ICTKillzoneFVGStrategy(
        swing_k=_num(data, "ict_swing_k", config.ICT_SWING_K, int),
        min_sweep_atr=_num(data, "ict_min_sweep_atr", config.ICT_MIN_SWEEP_ATR),
        choch_wait=_num(data, "ict_choch_wait", config.ICT_CHOCH_WAIT, int),
        disp_atr=_num(data, "ict_disp_atr", config.ICT_DISP_ATR),
        zone_lookback=_num(data, "ict_zone_lookback", config.ICT_ZONE_LOOKBACK, int),
        entry_frac=_num(data, "ict_entry_frac", config.ICT_ENTRY_FRAC),
        require_fvg=_flag(data, "ict_require_fvg", config.ICT_REQUIRE_FVG),
        entry_mode=(data.get("ict_entry_mode") or config.ICT_ENTRY_MODE),
        bias_mode=(data.get("ict_bias_mode") or config.ICT_BIAS_MODE),
        sl_buf_atr=_num(data, "ict_sl_buf_atr", config.ICT_SL_BUF_ATR),
        tp_mode=(data.get("ict_tp_mode") or config.ICT_TP_MODE),
        tp_r=_num(data, "ict_tp_r", config.ICT_TP_R),
        pend_min=_num(data, "ict_pend_min", config.ICT_PEND_MIN, int),
    ), "ict"

def register_routes(app):
    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/backtest')
    def backtest_page():
        return render_template('backtest.html')

    @app.route('/api/backtest/run', methods=['POST'])
    def run_backtest_api():
        data = request.json
        symbol = data.get("symbol", config.SYMBOL)
        tf = data.get("timeframe", config.TIMEFRAME)
        # Xử lý chuỗi rỗng từ UI tránh lỗi ValueError
        count_val = data.get("count")
        count = int(count_val) if count_val else 1000
        
        start_date = data.get("start_date") # YYYY-MM-DD
        
        balance_val = data.get("balance")
        balance = float(balance_val) if balance_val else 200.0
        
        lot_val = data.get("lot")
        lot = float(lot_val) if lot_val else 0.02
        
        # --- Lấy SPREAD THẬT + DIGITS từ broker qua MT5 API ---
        spread = 0.30   # fallback
        digits = 2      # fallback cho XAUUSD
        try:
            info = mt5h.get_symbol_info(symbol)
            if info is not None:
                digits = info.digits
                spread = round(info.spread * info.point, digits)
                logger.info(f"[Backtest] {symbol}: spread={spread} ({info.spread} pts × {info.point}), digits={digits}")
        except Exception as e:
            logger.warning(f"[Backtest] Không lấy được info từ MT5, dùng fallback: {e}")
        
        # Khởi tạo chiến lược (ICT tự chạy khung M5; các PP khác theo UI)
        strategy, sid = _build_strategy(data)
        tf = getattr(strategy, "timeframe", tf)

        # Lấy dữ liệu (kèm warmup để chỉ báo hội tụ; engine sẽ bỏ qua phần warmup)
        warmup = int(getattr(strategy, "warmup_bars", 0) or 0)
        df = get_with_warmup(symbol, tf, count=count, start_date=start_date, warmup_bars=warmup)
        if df is None or df.empty:
            return jsonify({"error": "Failed to get data for the specified range"}), 400

        # Chạy backtest với spread + digits thật từ broker (kèm mô phỏng sát thực tế)
        exec_df = None
        exec_tf = getattr(config, "BACKTEST_EXEC_TF", "")
        if exec_tf:
            try:
                ratio = max(1, _BAR_MINUTES.get(tf, 1) // _BAR_MINUTES.get(exec_tf, 1))
                m = get_historical_data(symbol, exec_tf, count=count * ratio + 2000)
                if m is not None and not m.empty:
                    exec_df = m[m.index >= df.index[0]]
            except Exception as e:
                logger.warning(f"[Backtest] exec_df lỗi ({e}), bỏ qua")

        tester = Backtester(
            strategy, initial_balance=balance, lot_size=lot, digits=digits, spread=spread,
            exec_df=exec_df,
            commission_per_lot=getattr(config, "BACKTEST_COMMISSION_PER_LOT", 0.0),
            slippage_points=getattr(config, "BACKTEST_SLIPPAGE_POINTS", 0),
            spread_mult=getattr(config, "BACKTEST_SPREAD_MULT", 1.0),
            spread_min=getattr(config, "BACKTEST_SPREAD_MIN", 0.0),
            realistic_fills=getattr(config, "BACKTEST_REALISTIC_FILLS", True),
        )
        trades = tester.run(df)
        
        # Chuyển đổi datetime sang string để tránh lỗi jsonify
        formatted_trades = []
        for t in trades:
            t_copy = t.copy()
            if isinstance(t_copy["entry_time"], (datetime, pd.Timestamp)):
                t_copy["entry_time"] = t_copy["entry_time"].strftime('%Y-%m-%d %H:%M')
            if isinstance(t_copy["exit_time"], (datetime, pd.Timestamp)):
                t_copy["exit_time"] = t_copy["exit_time"].strftime('%Y-%m-%d %H:%M')
            formatted_trades.append(t_copy)

        # Tính toán chỉ số hiệu năng đầy đủ
        pnls = [t["pnl"] for t in trades]
        total = len(pnls)
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gross_win = sum(wins)
        gross_loss = -sum(losses)
        if gross_loss > 1e-9:
            pf = round(gross_win / gross_loss, 3)
        else:
            pf = None  # vô cực → trả None để JSON hợp lệ
        equity = balance
        peak = balance
        max_dd = 0.0
        for p in pnls:
            equity += p
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)

        return jsonify({
            "summary": {
                "total_trades": total,
                "win_rate": round((len(wins) / total * 100) if total else 0.0, 2),
                "final_balance": round(tester.balance, 2),
                "profit": round(tester.balance - balance, 2),
                "profit_factor": pf,
                "expectancy": round((sum(pnls) / total) if total else 0.0, 2),
                "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
                "avg_loss": round(gross_loss / len(losses), 2) if losses else 0.0,
                "max_drawdown": round(max_dd, 2),
                "spread_used": spread,   # Hiển thị spread đang dùng để verify
                "digits": digits,
                "strategy": sid,         # Chiến lược thực tế đã chạy
                "timeframe": tf,
            },
            "trades": formatted_trades
        })

    @app.route('/api/status', methods=['GET'])
    def get_status():
        try:
            session = bot_engine.get_session_status()
        except Exception as e:
            logger.warning(f"session status error: {e}")
            session = None
        return jsonify({
            "bot_running": bot_engine.is_running,
            "bot_status": bot_engine.status,
            "current_strategy": strategy_manager.get_current_key(),
            "symbol": config.SYMBOL,
            "timeframe": getattr(strategy_manager.get_current_strategy(), "timeframe", config.TIMEFRAME),
            "session": session
        })

    @app.route('/api/logs', methods=['GET'])
    def get_logs():
        """Trả về N dòng log gần nhất (mặc định 200)."""
        lines_val = request.args.get("lines")
        try:
            max_lines = int(lines_val) if lines_val else 200
        except ValueError:
            max_lines = 200
        max_lines = max(1, min(max_lines, 1000))

        log_path = config.LOG_FILE
        if not os.path.isabs(log_path):
            log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), log_path)

        try:
            # Đọc lùi từ cuối file cho tới khi gom đủ `max_lines` dòng KHÔNG phải access log
            with open(log_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                block = 16384
                data = b""
                while size > 0:
                    read_size = min(block, size)
                    size -= read_size
                    f.seek(size)
                    data = f.read(read_size) + data
                    lines = data.decode("utf-8", errors="replace").splitlines()
                    filtered = [l for l in lines if not _ACCESS_LOG_RE.search(l)]
                    if len(filtered) > max_lines:
                        break
            lines = [l for l in data.decode("utf-8", errors="replace").splitlines()
                     if not _ACCESS_LOG_RE.search(l)]
            return jsonify({"lines": lines[-max_lines:]})
        except FileNotFoundError:
            return jsonify({"lines": []})
        except Exception as e:
            logger.error(f"Cannot read log file: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route('/api/account', methods=['GET'])
    def get_account():
        if not mt5h.connect():
            return jsonify({"error": "Cannot connect to MT5"}), 500
        balance = mt5h.get_account_balance()
        return jsonify({"balance": balance, "currency": "USD"})

    @app.route('/api/positions', methods=['GET'])
    def get_positions():
        if not mt5h.connect():
            return jsonify({"error": "Cannot connect to MT5"}), 500
        positions = mt5h.get_open_positions(config.SYMBOL, strategy_manager.get_magics())
        return jsonify([
            {
                "ticket": pos.ticket,
                "magic": pos.magic,
                "strategy": strategy_manager.get_name_by_magic(pos.magic),
                "type": "BUY" if pos.type == 0 else "SELL",
                "volume": pos.volume,
                "price_open": pos.price_open,
                "sl": pos.sl,
                "tp": pos.tp,
                "profit": pos.profit
            }
            for pos in positions
        ])

    @app.route('/api/strategies', methods=['GET'])
    def get_strategies():
        return jsonify(strategy_manager.get_all_strategies())

    @app.route('/api/strategy', methods=['POST'])
    def update_strategy():
        data = request.json
        strategy_id = data.get("strategy_id")
        if strategy_manager.set_strategy(strategy_id):
            return jsonify({"success": True})
        return jsonify({"success": False, "error": "Invalid strategy"}), 400

    @app.route('/api/close-all', methods=['POST'])
    def close_all():
        """Đóng toàn bộ vị thế + hủy toàn bộ lệnh chờ của bot, rồi DỪNG bot.
        Dừng bot trước để hedging không mở lại cặp mới ngay khi bị đóng sạch."""
        was_running = bot_engine.is_running
        if was_running:
            bot_engine.stop()
        magics = strategy_manager.get_magics()
        closed = mt5h.close_all_positions(config.SYMBOL, magics, "close all (UI)")
        canceled = mt5h.cancel_all_pending(config.SYMBOL, magics)
        hedging_engine.reset()
        bigmouse_engine.reset()
        bot_engine.clear_manual()
        logger.info(f"[CLOSE-ALL] đóng {closed} vị thế, hủy {canceled} lệnh chờ (bot_running={was_running})")
        return jsonify({
            "closed": closed,
            "canceled": canceled,
            "bot_stopped": was_running,
            "bot_running": bot_engine.is_running,
        })

    @app.route('/api/toggle-bot', methods=['POST'])
    def toggle_bot():
        if bot_engine.is_running:
            bot_engine.stop()
        else:
            bot_engine.start()
        return jsonify({"bot_running": bot_engine.is_running})
