"""
app.py – Khởi chạy hệ thống.
"""
from flask import Flask
from flask_cors import CORS
from utils.logger import setup_logger
from api.routes import register_routes

# Khởi cấu hình log
setup_logger()

app = Flask(__name__)
CORS(app)

# Đăng ký API
register_routes(app)

if __name__ == '__main__':
    # KHÔNG tự động chạy bot: phải chọn phương pháp + bấm "Start Bot" trên UI.
    # Khởi chạy server
    app.run(host='0.0.0.0', port=5000, debug=False)
