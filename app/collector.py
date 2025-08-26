import os, json, time, math, socket
from typing import Dict, Any
import paramiko
import paho.mqtt.client as mqtt

MQTT_HOST = os.getenv("MQTT_HOST", "homeassistant")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER") or None
MQTT_PASS = os.getenv("MQTT_PASS") or None
INTERVAL = max(5, int(os.getenv("INTERVAL", "30")))
SERVERS = json.loads(os.getenv("SERVERS_JSON", "[]"))

DISCOVERY_PREFIX = "homeassistant"

# State für Netzraten (Delta-Berechnung)
_last_net: Dict[str, Dict[str, int]] = {}
_last_ts: Dict[str, float] = {}

# ---------- MQTT ----------
client = mqtt.Client()
if MQTT_USER:
    client.username_pw_set(MQTT_USER, MQTT_PASS)
client.connect(MQTT_HOST, MQTT_PORT, 60)
client.loop_start()

def publish_discovery(name: str, key: str, unit: str = None, device_class: str = None):
    uid = f"{name}_{key}"
    topic = f"{DISCOVERY_PREFIX}/sensor/{uid}/config"
    payload = {
        "name": f"{name} {key}",
        "state_topic": f"vserver_ssh/{name}/state",
        "value_template": f"{{{{ value_json.{key} | default(0) }}}}",
        "unique_id": uid,
        "device": {"identifiers": [f"vserver_ssh_{name}"], "name": name},
    }
    if unit: payload["unit_of_measurement"] = unit
    if device_class: payload["device_class"] = device_class
    client.publish(topic, json.dumps(payload), retain=True)

def ensure_discovery(name: str):
    for key, unit, dc in [
        ("cpu", "%", None),
        ("mem", "%", None),
        ("disk", "%", None),
        ("net_in", "B/s", None),
        ("net_out", "B/s", None),
        ("uptime", "s", "duration"),
        ("temp", "°C", "temperature"),
    ]:
        publish_discovery(name, key, unit, dc)

# ---------- SSH ----------
def run_ssh(host: str, username: str, password: str, port: int = 22, cmd: str = "echo ok") -> str:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname=host, port=port, username=username, password=password, timeout=10, banner_timeout=10, auth_timeout=10)
    try:
        stdin, stdout, stderr = ssh.exec_command(cmd, timeout=15)
        out = stdout.read().decode("utf-8", "ignore")
        err = stderr.read().decode("utf-8", "ignore")
        if err and not out:
            raise RuntimeError(err.strip())
        return out
    finally:
        ssh.close()

# Remote-Script: CPU (über /proc/stat doppelt), Mem (/proc/meminfo), Disk (df /), Uptime, Temp (thermal_zone0), Net (bytes um Interface-summen)
REMOTE_SCRIPT = r'''
set -e
# CPU %
read cpu user nice system idle iowait irq softirq steal guest < /proc/stat
prev_total=$((user+nice+system+idle+iowait+irq+softirq+steal))
prev_idle=$((idle+iowait))
sleep 1
read cpu user nice system idle iowait irq softirq steal guest < /proc/stat
total=$((user+nice+system+idle+iowait+irq+softirq+steal))
idle_all=$((idle+iowait))
d_total=$((total-prev_total))
d_idle=$((idle_all-prev_idle))
cpu=$(( (100*(d_total - d_idle) + d_total/2) / d_total ))

# MEM %
mem_total=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
mem_avail=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)
if [ -z "$mem_avail" ]; then mem_avail=$(awk '/MemFree/ {print $2}' /proc/meminfo); fi
mem=$(( (100*(mem_total - mem_avail) + mem_total/2) / mem_total ))

# DISK % (Root)
disk=$(df -P / | awk 'NR==2 {print $5}' | tr -d '%')

# UPTIME (Sekunden)
uptime=$(awk '{print int($1)}' /proc/uptime)

# TEMP (°C, best-effort)
temp=""
if [ -f /sys/class/thermal/thermal_zone0/temp ]; then
  t=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo "")
  if [ -n "$t" ]; then temp=$(awk -v v="$t" 'BEGIN{printf "%.1f", (v>=1000?v/1000:v)}'); fi
fi

# NET (Summen Bytes RX/TX über alle nicht-lo Interfaces)
rx=$(awk -F'[: ]+' '/:/{if($1!="lo"){rx+=$3; tx+=$11}} END{print rx+0}' /proc/net/dev)
tx=$(awk -F'[: ]+' '/:/{if($1!="lo"){rx+=$3; tx+=$11}} END{print tx+0}' /proc/net/dev)

printf '{"cpu":%s,"mem":%s,"disk":%s,"uptime":%s,"temp":%s,"rx":%s,"tx":%s}\n' \
  "$cpu" "$mem" "$disk" "$uptime" "${temp:-null}" "$rx" "$tx"
'''

def sample_server(srv: Dict[str, Any]) -> Dict[str, Any]:
    out = run_ssh(
        host=srv["host"],
        username=srv["username"],
        password=srv["password"],
        port=int(srv.get("port", 22)),
        cmd=REMOTE_SCRIPT
    ).strip()
    data = json.loads(out)
    # Netzraten berechnen (Bytes/s)
    now = time.time()
    last = _last_net.get(srv["name"])
    last_ts = _last_ts.get(srv["name"])
    net_in = net_out = 0.0
    if last and last_ts:
        dt = max(1e-6, now - last_ts)
        net_in = max(0.0, (data["rx"] - last["rx"]) / dt)
        net_out = max(0.0, (data["tx"] - last["tx"]) / dt)
    _last_net[srv["name"]] = {"rx": data["rx"], "tx": data["tx"]}
    _last_ts[srv["name"]] = now

    # Antwort reduzieren
    return {
        "cpu": int(data["cpu"]),
        "mem": int(data["mem"]),
        "disk": int(data["disk"]),
        "uptime": int(data["uptime"]),
        "temp": (None if data["temp"] is None else float(data["temp"])),
        "net_in": round(net_in, 2),
        "net_out": round(net_out, 2),
    }

def main():
    # Discovery einmalig
    for s in SERVERS:
        ensure_discovery(s["name"])

    # initiale Netzbasis holen (damit ab dem 2. Tick Raten stimmen)
    for s in SERVERS:
        try:
            _ = sample_server(s)
        except Exception:
            pass

    while True:
        start = time.time()
        for s in SERVERS:
            try:
                payload = sample_server(s)
                client.publish(f"vserver_ssh/{s['name']}/state", json.dumps(payload), retain=False)
            except Exception as e:
                # bei Fehler zumindest Uptime/Temp leer publishen, damit Entity weiterlebt
                err = {"cpu": 0, "mem": 0, "disk": 0, "uptime": 0, "temp": None, "net_in": 0, "net_out": 0}
                client.publish(f"vserver_ssh/{s['name']}/state", json.dumps(err), retain=False)
        # Intervall einhalten
        sleep_for = INTERVAL - (time.time() - start)
        if sleep_for > 0:
            time.sleep(sleep_for)

if __name__ == "__main__":
    main()
