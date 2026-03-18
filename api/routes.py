"""
api/routes.py
Các liên kết API cho Control Center.
"""
from flask import jsonify, request, render_template
import core.mt5_handler as mt5h
import config
from core.bot_engine import bot_engine
from strategies.manager import strategy_manager

def register_routes(app):
    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/api/status', methods=['GET'])
    def get_status():
        return jsonify({
            "bot_running": bot_engine.is_running,
            "bot_status": bot_engine.status,
            "current_strategy": strategy_manager.get_current_key(),
            "symbol": config.SYMBOL,
            "timeframe": config.TIMEFRAME
        })

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
        pos = mt5h.get_open_position(config.SYMBOL, config.MAGIC_NUMBER)
        if pos:
            return jsonify([{
                "ticket": pos.ticket,
                "type": "BUY" if pos.type == 0 else "SELL",
                "volume": pos.volume,
                "price_open": pos.price_open,
                "sl": pos.sl,
                "tp": pos.tp,
                "profit": pos.profit
            }])
        return jsonify([])

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
