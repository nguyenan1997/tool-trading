"""
agent.py
================================================================
Agent cho MỘT phần mềm MT5 (1 tiến trình = 1 terminal).

- Nhận `--name <instance>` (khớp mt5_instances.json).
- Trỏ config.SYMBOL + mt5_handler._MT5_PATH theo instance.
- Chạy BotEngine + đăng ký API (giống app.py) trên cổng riêng.
- Thêm API:
    GET  /api/instance                 -> thông tin agent
    POST /api/set-strategy {strategy}  -> đổi PP + lưu vào json

Chạy trực tiếp:
    python agent.py --name MT5-9148
Hoặc để control_center.py tự spawn.
================================================================
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
from core.instance_config import get_instance, update_instance  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="MT5 Agent")
    ap.add_argument("--name", required=True, help="tên instance trong mt5_instances.json")
    ap.add_argument("--port", type=int, default=None, help="ghi đè cổng")
    a = ap.parse_args()

    inst = get_instance(a.name)
    if not inst:
        print(f"[AGENT] Không tìm thấy instance '{a.name}' trong mt5_instances.json")
        return

    # --- Ghi đè cấu hình toàn cục TRƯỚC khi import các module dùng nó ---
    config.SYMBOL = inst.get("symbol", config.SYMBOL)
    config.LOG_FILE = inst.get("log_file", f"logs/agent_{a.name}.log")

    import core.mt5_handler as mt5h
    if inst.get("terminal"):
        mt5h._MT5_PATH = inst["terminal"]

    from utils.logger import setup_logger
    setup_logger()

    from flask import Flask, jsonify, request
    from flask_cors import CORS
    from api.routes import register_routes
    from core.bot_engine import bot_engine
    from strategies.manager import strategy_manager

    app = Flask(__name__)
    CORS(app)
    register_routes(app)

    @app.route("/api/instance", methods=["GET"])
    def instance_info():
        return jsonify({
            "name": a.name,
            "symbol": config.SYMBOL,
            "terminal": inst.get("terminal"),
            "strategy": strategy_manager.get_current_key(),
            "bot_running": bot_engine.is_running,
        })

    @app.route("/api/set-strategy", methods=["POST"])
    def set_strategy_persist():
        data = request.json or {}
        sid = data.get("strategy")
        if sid and strategy_manager.set_strategy(sid):
            update_instance(a.name, strategy=sid)
            return jsonify({"success": True, "strategy": sid})
        return jsonify({"success": False, "error": "Invalid strategy"}), 400

    # PP mặc định từ instance
    if inst.get("strategy"):
        strategy_manager.set_strategy(inst["strategy"])
    if inst.get("autostart"):
        bot_engine.start()

    port = a.port or int(inst.get("port", 5101))
    print(f"[AGENT] {a.name} | {config.SYMBOL} | {inst.get('terminal')} | port {port} "
          f"| strategy={strategy_manager.get_current_key()} | bot={bot_engine.is_running}")
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
