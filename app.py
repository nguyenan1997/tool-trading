"""
app.py – Khởi chạy hệ thống.
"""
from flask import Flask
from flask_cors import CORS
from utils.logger import setup_logger
from api.routes import register_routes
from core.bot_engine import bot_engine

# Khởi cấu hình log
setup_logger()

app = Flask(__name__)
CORS(app)

# Đăng ký API
register_routes(app)

if __name__ == '__main__':
    # Tự động bắt đầu bot engine
    bot_engine.start()
    
    # Khởi chạy server
    app.run(host='0.0.0.0', port=5000, debug=False)
