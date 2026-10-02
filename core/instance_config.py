"""
core/instance_config.py
Đọc/ghi danh sách MT5 instance từ mt5_instances.json.

Mỗi instance:
    name      : tên hiển thị (duy nhất)   vd "MT5-9148"
    terminal  : đường dẫn terminal64.exe
    symbol    : symbol giao dịch          vd "XAUUSD" hoặc "XAUUSD."
    port      : cổng Flask của agent
    strategy  : PP mặc định               vd "basket_dca"
    autostart : true = agent tự Start bot khi mở
    spawn     : true = Control Center tự chạy agent này
"""
from __future__ import annotations

import json
import os
import threading

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FILE = os.path.join(_ROOT, "mt5_instances.json")
_lock = threading.RLock()


def load_instances() -> list:
    with _lock:
        if not os.path.exists(_FILE):
            return []
        try:
            with open(_FILE, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            return []


def save_instances(data: list):
    with _lock:
        with open(_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def get_instance(name: str):
    for i in load_instances():
        if str(i.get("name")) == str(name):
            return i
    return None


def update_instance(name: str, **fields):
    data = load_instances()
    found = False
    for i in data:
        if str(i.get("name")) == str(name):
            i.update(fields)
            found = True
    if found:
        save_instances(data)
    return found
