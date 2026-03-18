"""
main.py – Tệp khởi chạy chính của hệ thống.
"""
import sys
import os

# Đảm bảo thư mục gốc nằm trong sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import app
from core.bot_engine import bot_engine

if __name__ == '__main__':
    print("--- KHỞI CHẠY HỆ THỐNG GIAO DỊCH KHOA HỌC ---")
    
    # Khởi động Bot Engine trong một luồng riêng
    bot_engine.start()
    
    # Khởi chạy Flask Web Server (Control Center)
    # LƯU Ý: Chạy server ở đây sẽ chặn luồng chính
    app.run(host='0.0.0.0', port=5000, debug=False)
