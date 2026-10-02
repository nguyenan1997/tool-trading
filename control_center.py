"""
control_center.py
================================================================
Trung tâm điều khiển đa-MT5.

- Đọc danh sách MT5 từ mt5_instances.json.
- Tự SPAWN một agent.py cho mỗi MT5 (mỗi agent 1 cổng riêng).
- Web UI: bảng điều khiển từng MT5 (trạng thái, account, PP, Start/Stop,
  Đóng tất cả, xem Log). Proxy lệnh qua HTTP tới agent.

Chạy:
    python control_center.py            # mặc định cổng 5000
    python control_center.py --port 5000
================================================================
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, jsonify, render_template, request  # noqa: E402
from flask_cors import CORS  # noqa: E402

from core.instance_config import load_instances, update_instance  # noqa: E402

_ROOT = os.path.dirname(os.path.abspath(__file__))
_procs = {}          # name -> subprocess.Popen
_lock = threading.RLock()


# ----------------------------------------------------------------------
#  Quản lý tiến trình agent
# ----------------------------------------------------------------------
def start_agent(inst) -> bool:
    name = inst["name"]
    with _lock:
        p = _procs.get(name)
        if p is not None and p.poll() is None:
            return True
        try:
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
            _procs[name] = subprocess.Popen(
                [sys.executable, "agent.py", "--name", name],
                cwd=_ROOT, creationflags=flags,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            return False


def stop_agent(inst) -> bool:
    name = inst["name"]
    with _lock:
        p = _procs.get(name)
        if p is not None and p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass
            _procs.pop(name, None)
            return True
    return False


def proc_alive(name: str) -> bool:
    p = _procs.get(name)
    return p is not None and p.poll() is None


# ----------------------------------------------------------------------
#  Proxy tới agent
# ----------------------------------------------------------------------
def _get(port, path, timeout=1.5):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:
        return None


def _post(port, path, payload, timeout=10):
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except Exception as e:
        return {"_error": str(e)}


def instance_status(inst):
    port = int(inst.get("port", 0))
    st = _get(port, "/api/status")
    online = st is not None
    acc = _get(port, "/api/account") if online else None
    pos = _get(port, "/api/positions") if online else None
    floating = None
    n_pos = 0
    if isinstance(pos, list):
        n_pos = len(pos)
        floating = round(sum(p.get("profit", 0) for p in pos), 2)
    return {
        "name": inst.get("name"),
        "symbol": inst.get("symbol"),
        "terminal": inst.get("terminal"),
        "port": port,
        "config_strategy": inst.get("strategy"),
        "online": online,
        "proc_alive": proc_alive(inst.get("name")),
        "bot_running": (st or {}).get("bot_running"),
        "bot_status": (st or {}).get("bot_status"),
        "current_strategy": (st or {}).get("current_strategy"),
        "session": (st or {}).get("session"),
        "balance": (acc or {}).get("balance") if isinstance(acc, dict) else None,
        "positions": n_pos,
        "floating": floating,
    }


# ----------------------------------------------------------------------
#  Web
# ----------------------------------------------------------------------
app = Flask(__name__)
CORS(app)


@app.route("/")
def index():
    return render_template("control_center.html")


@app.route("/api/instances", methods=["GET"])
def api_instances():
    return jsonify([instance_status(i) for i in load_instances()])


@app.route("/api/strategies", methods=["GET"])
def api_strategies():
    insts = load_instances()
    if not insts:
        return jsonify([])
    s = _get(int(insts[0]["port"]), "/api/strategies")
    return jsonify(s or [])


@app.route("/api/instances/<name>/action", methods=["POST"])
def api_action(name):
    inst = next((i for i in load_instances() if i.get("name") == name), None)
    if not inst:
        return jsonify({"error": "instance not found"}), 404
    data = request.json or {}
    action = (data.get("action") or "").strip()
    port = int(inst.get("port", 0))

    if action == "start-agent":
        ok = start_agent(inst)
        return jsonify({"success": ok})
    if action == "stop-agent":
        ok = stop_agent(inst)
        return jsonify({"success": ok})
    if action == "restart-agent":
        stop_agent(inst)
        time.sleep(1.0)
        return jsonify({"success": start_agent(inst)})

    # các action chuyển tiếp cho agent (phải online)
    if action == "toggle-bot":
        return jsonify(_post(port, "/api/toggle-bot", {}) or {"_error": "offline"})
    if action == "close-all":
        return jsonify(_post(port, "/api/close-all", {}) or {"_error": "offline"})
    if action == "set-strategy":
        sid = data.get("strategy")
        res = _post(port, "/api/set-strategy", {"strategy": sid}) or {"_error": "offline"}
        if res.get("success"):
            update_instance(name, strategy=sid)
        return jsonify(res)
    return jsonify({"error": f"unknown action {action}"}), 400


@app.route("/api/instances/<name>/logs", methods=["GET"])
def api_logs(name):
    inst = next((i for i in load_instances() if i.get("name") == name), None)
    if not inst:
        return jsonify({"lines": []})
    lines = request.args.get("lines", "200")
    return jsonify(_get(int(inst["port"]), f"/api/logs?lines={lines}") or {"lines": []})


@app.route("/api/instances/<name>/positions", methods=["GET"])
def api_positions(name):
    inst = next((i for i in load_instances() if i.get("name") == name), None)
    if not inst:
        return jsonify([])
    return jsonify(_get(int(inst["port"]), "/api/positions") or [])


def main():
    ap = argparse.ArgumentParser(description="MT5 Control Center")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--no-spawn", action="store_true")
    a = ap.parse_args()

    if not a.no_spawn:
        for inst in load_instances():
            if inst.get("spawn", True):
                start_agent(inst)
        print(f"[HUB] spawned {len(load_instances())} agent(s)")

    print(f"[HUB] Control Center: http://127.0.0.1:{a.port}")
    app.run(host="0.0.0.0", port=a.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
