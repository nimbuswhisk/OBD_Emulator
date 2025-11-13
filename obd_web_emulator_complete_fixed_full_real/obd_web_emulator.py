#!/usr/bin/env python3
"""
OBD Web Emulator — Complete Enhanced Version (Fixed Gauge layout package)
- ELM327-like TCP server on port 35000
- Web UI (fancy gauges) at http://localhost:8080 (web/index.html)
- Random drive simulation
- Extra PIDs: voltage, fuel trims, load, IAT, MAF (simulated), boost, O2/AFR
- Trip recording (CSV) and /export endpoint
- Fixed layout adjustments for gauge canvas height and label positions
"""

import threading, socket, json, time, os, csv, math, random
from http.server import SimpleHTTPRequestHandler, HTTPServer

# Shared state (mutable by web UI)
_STATE = {
    "rpm":850.0, "speed":0.0, "coolant":65.0, "throttle":12.0,
    "engine_load":12.0, "iat":22.0, "voltage":12.6,
    "fuel_trim_short":0.0, "fuel_trim_long":0.0,
    "maf":2.5,          # g/s (simulated)
    "boost":0.0,        # kPa (gauge pressure)
    "o2":0.45,          # lambda (air-fuel ratio proxy)
    "afr":14.7          # stoichiometric AFR for gasoline ~14.7
}
_STATE_LOCK = threading.Lock()
_RANDOM_MODE = {"enabled": False}
# Trip log (append tuples)
_TRIP = []
_TRIP_LOCK = threading.Lock()

# Map PIDs -> handler (mode 01)
def pid_response(pid):
    with _STATE_LOCK:
        s = _STATE.copy()
    # Mode 01 PIDs
    if pid == "010C":  # RPM
        v = int(s["rpm"] * 4) & 0xFFFF
        return f"41 0C {v>>8:02X} {v&0xFF:02X}\r"
    if pid == "010D":  # Speed km/h
        return f"41 0D {int(s['speed'])&0xFF:02X}\r"
    if pid == "0105":  # Coolant temp (A - 40)
        A = int(s["coolant"] + 40) & 0xFF
        return f"41 05 {A:02X}\r"
    if pid == "0111":  # Throttle
        A = int(s["throttle"] * 255 / 100) & 0xFF
        return f"41 11 {A:02X}\r"
    if pid == "0104":  # Engine load
        A = int(s["engine_load"] * 255 / 100) & 0xFF
        return f"41 04 {A:02X}\r"
    if pid == "010F":  # IAT
        A = int(s["iat"] + 40) & 0xFF
        return f"41 0F {A:02X}\r"
    if pid == "0142":  # Control module voltage (value*100)
        val = int(round(s["voltage"] * 100)) & 0xFFFF
        return f"41 42 {(val>>8)&0xFF:02X} {val&0xFF:02X}\r"
    if pid == "0106":  # Short fuel trim (A-128)
        A = int(s["fuel_trim_short"] + 128) & 0xFF
        return f"41 06 {A:02X}\r"
    if pid == "0107":  # Long fuel trim
        A = int(s["fuel_trim_long"] + 128) & 0xFF
        return f"41 07 {A:02X}\r"
    # Simulated MAF (0110) — return two bytes as value*100
    if pid == "0110":
        val = int(round(s.get("maf", 0.0) * 100)) & 0xFFFF
        return f"41 10 {(val>>8)&0xFF:02X} {val&0xFF:02X}\r"
    # Simulated O2 / AFR — return custom PID 012F as proxy: A = AFR*10
    if pid == "012F":
        val = int(round(s.get("afr", 14.7) * 10)) & 0xFF
        return f"41 2F {val:02X}\r"
    # Boost (custom PID 015C) return two bytes pressure*10
    if pid == "015C":
        val = int(round(s.get("boost",0.0) * 10)) & 0xFFFF
        return f"41 5C {(val>>8)&0xFF:02X} {val&0xFF:02X}\r"
    return "NO DATA\r"

# ELM327 server
def handle_client(conn, addr):
    try:
        print(f"[ELM CONNECT] {addr}")
        conn.send(b"ELM327 v1.5\r>")
        buffer = ""
        while True:
            data = conn.recv(1024)
            if not data:
                break
            buffer += data.decode(errors="ignore")
            if "\r" in buffer:
                cmd = buffer.strip()
                buffer = ""
                print(f"[ELM REQ] {cmd}")
                lc = cmd.lower()
                if lc == "atz":
                    conn.send(b"ELM327 v1.5\r>")
                elif lc.startswith("ati"):
                    conn.send(b"ELM327 v1.5\r>")
                elif lc.startswith("ate"):
                    conn.send(b"OK\r>")
                elif cmd.startswith("01"):
                    pid = cmd[:4]
                    resp = pid_response(pid)
                    conn.send(resp.encode() + b">")
                else:
                    conn.send(b"OK\r>")
    except Exception as e:
        print("ELM client error:", e)
    finally:
        try: conn.close()
        except: pass
        print(f"[ELM DISCONNECT] {addr}")

def elm_server_thread():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", 35000))
    s.listen(5)
    print("[ELM] Listening on port 35000")
    while True:
        c, a = s.accept()
        threading.Thread(target=handle_client, args=(c, a), daemon=True).start()

# Web server and API (serves web/index.html and endpoints)
PORT = 8080
WEB_DIR = os.path.join(os.path.dirname(__file__), "web")

class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            path = os.path.join(WEB_DIR, "index.html")
            with open(path, "rb") as fh:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(fh.read())
            return
        if self.path == "/values":
            with _STATE_LOCK:
                payload = json.dumps(_STATE).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path == "/export":
            # export trip log as CSV
            with _TRIP_LOCK:
                rows = list(_TRIP)
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Disposition", "attachment; filename=trip_log.csv")
            self.end_headers()
            w = csv.writer(self.wfile)
            w.writerow(["timestamp","rpm","speed","throttle","coolant","afr","voltage"])
            for r in rows:
                w.writerow(r)
            return
        return super().do_GET()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        data = json.loads(body) if body else {}
        if self.path == "/set":
            with _STATE_LOCK:
                for k, v in data.items():
                    if k in _STATE:
                        _STATE[k] = float(v)
            # record to trip log
            with _TRIP_LOCK:
                _TRIP.append((time.time(), _STATE["rpm"], _STATE["speed"], _STATE["throttle"], _STATE["coolant"], _STATE.get("afr",14.7), _STATE.get("voltage",12.6)))
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":1}'); return
        if self.path == "/random":
            _RANDOM_MODE["enabled"] = bool(data.get("enabled", False))
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":1}'); return
        self.send_response(404); self.end_headers()

def web_thread():
    httpd = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[WEB] Serving http://localhost:{PORT}")
    httpd.serve_forever()

# Random drive simulation (updates _STATE periodically)
def random_drive_loop():
    t = 0.0
    while True:
        if _RANDOM_MODE["enabled"]:
            t += 0.12
            s = max(0.0, min(220.0, 50 + 50*math.sin(t/9.0) + random.uniform(-10,12)))
            thr = max(0.0, min(100.0, 15 + 40*math.sin(t/6.0) + random.uniform(-8,18)))
            rpm = max(600.0, min(7200.0, 700 + s*32 + thr*4 + random.uniform(-200,300)))
            cool = max(20.0, min(110.0, 70 + 4*math.sin(t/40.0) + random.uniform(-3,3)))
            load = min(100.0, (rpm/7000.0)*100*(0.6 + thr/200.0))
            volt = 12.6 + 0.25*math.sin(t/37.0) + random.uniform(-0.12,0.18)
            maf = max(0.5, 2.0 + s*0.05 + thr*0.02 + random.uniform(-0.5,0.8))
            afr = max(10.0, min(22.0, 14.7 - (thr/100.0)*2.0 + random.uniform(-0.3,0.3)))
            boost = max(0.0, min(150.0, (thr/100.0)*80 + (s/220.0)*10 + random.uniform(-2,6)))
            ft_s = random.uniform(-3.0, 3.0); ft_l = random.uniform(-2.0,2.0)
            with _STATE_LOCK:
                _STATE.update({"speed":s,"throttle":thr,"rpm":rpm,"coolant":cool,"engine_load":load,
                               "voltage":volt,"maf":maf,"afr":afr,"boost":boost,
                               "fuel_trim_short":ft_s,"fuel_trim_long":ft_l})
            # record sample
            with _TRIP_LOCK:
                _TRIP.append((time.time(), _STATE["rpm"], _STATE["speed"], _STATE["throttle"], _STATE["coolant"], _STATE.get("afr",14.7), _STATE.get("voltage",12.6)))
            # keep trip log reasonable length
            with _TRIP_LOCK:
                if len(_TRIP) > 20000:
                    _TRIP.pop(0)
        time.sleep(0.25)

if __name__ == "__main__":
    threading.Thread(target=elm_server_thread, daemon=True).start()
    threading.Thread(target=web_thread, daemon=True).start()
    threading.Thread(target=random_drive_loop, daemon=True).start()
    print("OBD Web Emulator running. Web UI: http://localhost:8080  ELM327 TCP: port 35000")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Exiting...")
