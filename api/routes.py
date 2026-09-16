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
from strategies.manager import strategy_manager
from backtest.engine import Backtester
from backtest.data_loader import get_historical_data
from strategies.triple_ema import TripleEmaStrategy
from strategies.trend_momentum import TrendMomentumStrategy

logger = logging.getLogger(__name__)

# Nhận diện dòng access log của Flask/Werkzeug: ... "GET /api/... HTTP/1.1" 200 -
_ACCESS_LOG_RE = re.compile(r'"\s*(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+\S+\s+HTTP/\d')


def _build_strategy(data):
    """Xây dựng chiến lược theo tham số `strategy` (tách rõ từng loại)."""
    sid = (data.get("strategy") or "3ema").strip().lower()

    if sid == "trend_momentum":
        def _num(key, default, cast=float):
            val = data.get(key)
            return cast(val) if val not in (None, "") else default
        return TrendMomentumStrategy(
            lookback=_num("tm_lookback", config.TM_LOOKBACK, int),
            rsi_period=_num("tm_rsi_period", config.TM_RSI_PERIOD, int),
            rsi_buy=_num("tm_rsi_buy", config.TM_RSI_BUY),
            rsi_sell=_num("tm_rsi_sell", config.TM_RSI_SELL),
            sl_atr=_num("tm_sl_atr", config.TM_SL_ATR),
            tp_r=_num("tm_tp_r", config.TM_TP_R),
            adx_thresh=_num("tm_adx_thresh", config.TM_ADX_THRESH),
            session=config.TM_SESSION,
            history_bars=config.TM_HISTORY_BARS,
            be_move_at_r=config.TM_BE_AT_R,
        ), sid

    strategy = TripleEmaStrategy()
    if "ema_fast" in data: strategy.fast = int(data["ema_fast"])
    if "ema_medium" in data: strategy.medium = int(data["ema_medium"])
    if "ema_slow" in data: strategy.slow = int(data["ema_slow"])
    if "rr" in data: strategy.rr = float(data["rr"])
    return strategy, "3ema"

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
        balance = float(balance_val) if balance_val else 100.0
        
        lot_val = data.get("lot")
        lot = float(lot_val) if lot_val else 0.01
        
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
        
        # Lấy dữ liệu
        df = get_historical_data(symbol, tf, count=count, start_date=start_date)
        if df is None or df.empty:
            return jsonify({"error": "Failed to get data for the specified range"}), 400
            
        # Khởi tạo chiến lược theo selector (3ema / trend_momentum)
        strategy, sid = _build_strategy(data)
        
        # Chạy backtest với spread + digits thật từ broker
        tester = Backtester(strategy, initial_balance=balance, lot_size=lot, digits=digits, spread=spread)
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

        # Tính toán một số chỉ số nhanh
        wins = len([t for t in trades if t["result"] == "PROFIT"])
        total = len(trades)
        win_rate = (wins / total * 100) if total > 0 else 0
        
        return jsonify({
            "summary": {
                "total_trades": total,
                "win_rate": round(win_rate, 2),
                "final_balance": round(tester.balance, 2),
                "profit": round(tester.balance - balance, 2),
                "spread_used": spread,   # Hiển thị spread đang dùng để verify
                "digits": digits,
                "strategy": sid,         # Chiến lược thực tế đã chạy
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
            "timeframe": config.TIMEFRAME,
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

    @app.route('/api/toggle-bot', methods=['POST'])
    def toggle_bot():
        if bot_engine.is_running:
            bot_engine.stop()
        else:
            bot_engine.start()
        return jsonify({"bot_running": bot_engine.is_running})
