import os, json, time, re
from typing import Dict, Any, Optional, List, Set
from collections import defaultdict
import logging

import paramiko
import paho.mqtt.client as mqtt

# MQTT configuration (optional)
MQTT_HOST = os.getenv("MQTT_HOST")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883")) if MQTT_HOST else None
MQTT_USER = os.getenv("MQTT_USER") or None
MQTT_PASS = os.getenv("MQTT_PASS") or None
INTERVAL = max(5, int(os.getenv("INTERVAL", "30")))
SERVERS = json.loads(os.getenv("SERVERS_JSON", "[]"))
DISABLED_ENTITIES = set(json.loads(os.getenv("DISABLED_JSON", "[]")))

DISCOVERY_PREFIX = "homeassistant"

# Datei, in die die letzten Messwerte für das Web-Frontend geschrieben werden
WEB_STATS_PATH = "/app/web/stats.json"

# State für Netzraten (Delta-Berechnung)
_last_net: Dict[str, Dict[str, int]] = {}
_last_ts: Dict[str, float] = {}

# Verfolgte Container pro Server für MQTT Discovery
_container_discovered: Dict[str, Set[str]] = defaultdict(set)


def _sanitize(name: str) -> str:
    """Return a lowercase, MQTT/HA friendly name."""
    return re.sub(r"[^a-zA-Z0-9_]+", "_", name).lower()


def _setup_logging() -> None:
    """Configure module wide logging."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


def _setup_mqtt() -> Optional[mqtt.Client]:
    """Create and configure the MQTT client."""
    if not MQTT_HOST:
        logging.info("MQTT disabled; stats will be printed to log")
        return None

    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    try:
        rc = client.connect(MQTT_HOST, MQTT_PORT, 60)
    except Exception as exc:  # pragma: no cover - connection best effort
        logging.error("MQTT connection failed: %s", exc)
        return None

    if rc == mqtt.MQTT_ERR_SUCCESS:
        logging.info("Connected to MQTT broker at %s:%s", MQTT_HOST, MQTT_PORT)
        client.loop_start()
        return client

    logging.error("Failed to connect to MQTT broker: %s", mqtt.error_string(rc))
    return None


_setup_logging()
client: Optional[mqtt.Client] = _setup_mqtt()

def publish_discovery(
    name: str,
    key: str,
    unit: str = None,
    device_class: str = None,
    str_value: bool = False,
) -> None:
    """Publish the MQTT discovery config for a single sensor."""
    if not client:
        return
    uid = f"{name}_{key}"
    topic = f"{DISCOVERY_PREFIX}/sensor/{uid}/config"
    default = "''" if str_value else 0
    payload = {
        "name": f"{name} {key}",
        "state_topic": f"vserver_ssh/{name}/state",
        "value_template": f"{{{{ value_json.{key} | default({default}) }}}}",
        "unique_id": uid,
        "device": {"identifiers": [f"vserver_ssh_{name}"], "name": name},
    }
    if unit:
        payload["unit_of_measurement"] = unit
    if device_class:
        payload["device_class"] = device_class
    client.publish(topic, json.dumps(payload), retain=True)

def ensure_discovery(name: str) -> None:
    """Ensure MQTT discovery topics exist for all metrics."""
    if not client:
        return
    for key, unit, dc, str_value in [
        ("cpu", "%", None, False),
        ("mem", "%", None, False),
        ("disk", "%", None, False),
        ("net_in", "B/s", None, False),
        ("net_out", "B/s", None, False),
        ("uptime", "s", "duration", False),
        ("temp", "°C", "temperature", False),
        ("ram", "MB", None, False),
        ("cores", None, None, False),
        ("load_1", None, None, False),
        ("load_5", None, None, False),
        ("load_15", None, None, False),
        ("cpu_freq", "MHz", "frequency", False),
        ("os", None, None, True),
        ("pkg_count", None, None, False),
        ("pkg_list", None, None, True),
        ("docker", None, None, False),
        ("containers", None, None, True),
    ]:
        if key in DISABLED_ENTITIES:
            continue
        publish_discovery(name, key, unit, dc, str_value)


def ensure_container_discovery(name: str, containers: List[Dict[str, Any]]) -> None:
    """Publish MQTT discovery topics for all containers."""
    if not client:
        return
    known = _container_discovered[name]
    for c in containers:
        cname = _sanitize(c.get("name", ""))
        if not cname or cname in known:
            continue
        known.add(cname)
        for metric, unit in [("cpu", "%"), ("mem", "%")]:
            key = f"container_{cname}_{metric}"
            if key in DISABLED_ENTITIES:
                continue
            publish_discovery(name, key, unit)

# ---------- SSH ----------
def run_ssh(
    host: str,
    username: str,
    password: Optional[str] = None,
    key: Optional[str] = None,
    port: int = 22,
    cmd: str = "echo ok",
) -> str:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        hostname=host,
        port=port,
        username=username,
        password=password,
        key_filename=key,
        timeout=10,
        banner_timeout=10,
        auth_timeout=10,
    )
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
export LC_ALL=C
export LANG=C
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
# RAM total MB
ram=$(( (mem_total + 512) / 1024 ))

# DISK % (Root)
disk=$(df -P / | awk 'NR==2 {print $5}' | tr -d '%')

# UPTIME (Sekunden)
uptime=$(awk '{print int($1)}' /proc/uptime)

# CPU cores
cores=$(nproc)

# Load average (1/5/15 Minuten)
read load_1 load_5 load_15 _ < /proc/loadavg

# Aktuelle CPU-Frequenz in MHz (best-effort)
cpu_freq=""
if [ -f /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq ]; then
  cpu_freq=$(awk '{printf "%.0f", $1/1000}' /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null)
fi
if [ -n "$cpu_freq" ]; then cpu_freq_json=$cpu_freq; else cpu_freq_json=null; fi

# OS (best-effort)
os=$( (grep '^PRETTY_NAME' /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"') || uname -sr )
os_json=$(printf '%s' "$os" | sed 's/"/\\"/g')

# Installed packages (count + list up to 10)
pkg_count=0
pkg_list=""
if command -v dpkg >/dev/null 2>&1; then
  pkg_count=$(dpkg-query -f '.\n' -W | wc -l)
  pkg_list=$(dpkg-query -f '${binary:Package}\n' -W | head -n 10 | tr '\n' ',' | sed 's/,$//')
elif command -v rpm >/dev/null 2>&1; then
  pkg_count=$(rpm -qa | wc -l)
  pkg_list=$(rpm -qa | head -n 10 | tr '\n' ',' | sed 's/,$//')
fi
pkg_list_json=$(printf '%s' "$pkg_list" | sed 's/"/\\"/g')

# Docker (installed and running containers)
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker=1
  containers=$(docker ps --format '{{.Names}}' 2>/dev/null | tr '\n' ',' | sed 's/,$//')
  stats=$(docker stats --no-stream --format '{{.Name}}:{{.CPUPerc}}:{{.MemPerc}}' 2>/dev/null | sed 's/%//g' | awk -F: '{printf "{"name":"%s","cpu":%s,"mem":%s},", $1, $2, $3}')
  if [ -n "$stats" ]; then
    container_stats="[${stats%,}]"
  else
    container_stats="[]"
  fi
else
  docker=0
  containers=""
  container_stats="[]"
fi
containers_json=$(printf '%s' "$containers" | sed 's/"/\\"/g')
container_stats_json=$container_stats

# TEMP (°C, best-effort)
temp=""
if [ -f /sys/class/thermal/thermal_zone0/temp ]; then
  t=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo "")
  if [ -n "$t" ]; then temp=$(awk -v v="$t" 'BEGIN{printf "%.1f", (v>=1000?v/1000:v)}'); fi
fi

# NET (Summen Bytes RX/TX über alle nicht-lo Interfaces)
rx=$(awk -F'[: ]+' '/:/{if($1!="lo"){rx+=$3; tx+=$11}} END{print rx+0}' /proc/net/dev)
tx=$(awk -F'[: ]+' '/:/{if($1!="lo"){rx+=$3; tx+=$11}} END{print tx+0}' /proc/net/dev)

if [ -n "$temp" ]; then temp_json=$temp; else temp_json=null; fi
printf '{"cpu":%s,"mem":%s,"disk":%s,"uptime":%s,"temp":%s,"rx":%s,"tx":%s,"ram":%s,"cores":%s,"load_1":%s,"load_5":%s,"load_15":%s,"cpu_freq":%s,"os":"%s","pkg_count":%s,"pkg_list":"%s","docker":%s,"containers":"%s","container_stats":%s}\n' \
  "$cpu" "$mem" "$disk" "$uptime" "$temp_json" "$rx" "$tx" "$ram" "$cores" "$load_1" "$load_5" "$load_15" "$cpu_freq_json" "$os_json" "$pkg_count" "$pkg_list_json" "$docker" "$containers_json" "$container_stats_json"
'''

def sample_server(srv: Dict[str, Any]) -> Dict[str, Any]:
    out = run_ssh(
        host=srv["host"],
        username=srv["username"],
        password=srv.get("password"),
        key=srv.get("key"),
        port=int(srv.get("port", 22)),
        cmd=REMOTE_SCRIPT
    ).strip()
    # Manche Hosts senden Warnungen oder Login-Banner zusätzlich zur JSON-Antwort.
    # Versuche daher, den ersten JSON-Block aus dem Output herauszuschneiden,
    # bevor wir ihn parsen.
    if not out.startswith("{"):
        start = out.find("{")
        end = out.rfind("}")
        if start != -1 and end != -1:
            out = out[start : end + 1]
    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:  # pragma: no cover - schwer zu simulieren
        raise RuntimeError(f"Invalid JSON response: {out}") from exc
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
        "ram": int(data.get("ram", 0)),
        "cores": int(data.get("cores", 0)),
        "load_1": float(data.get("load_1", 0.0)),
        "load_5": float(data.get("load_5", 0.0)),
        "load_15": float(data.get("load_15", 0.0)),
        "cpu_freq": (None if data.get("cpu_freq") is None else int(data.get("cpu_freq", 0))),
        "os": data.get("os", ""),
        "pkg_count": int(data.get("pkg_count", 0)),
        "pkg_list": data.get("pkg_list", ""),
        "docker": int(data.get("docker", 0)),
        "containers": data.get("containers", ""),
        "container_stats": data.get("container_stats", []),
    }

def main():
    global client
    # Discovery einmalig
    if not SERVERS:
        logging.warning("No servers configured; exiting")
        return
    logging.info("Configured servers: %s", [s["name"] for s in SERVERS])
    if client:
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
        if client and not client.is_connected():
            logging.warning("MQTT client disconnected; switching to log output")
            client.loop_stop()
            client = None

        web_stats = []
        for s in SERVERS:
            try:
                payload = sample_server(s)
                cont_stats = payload.get("container_stats", [])
                mqtt_payload = payload.copy()
                for k in DISABLED_ENTITIES:
                    mqtt_payload.pop(k, None)
                if client:
                    ensure_container_discovery(s["name"], cont_stats)
                    for c in cont_stats:
                        cname = _sanitize(c.get("name", ""))
                        for metric in ("cpu", "mem"):
                            key = f"container_{cname}_{metric}"
                            if key in DISABLED_ENTITIES:
                                continue
                            mqtt_payload[key] = c.get(metric, 0)
                web_stats.append({"name": s["name"], **payload})
                if client:
                    info = client.publish(
                        f"vserver_ssh/{s['name']}/state", json.dumps(mqtt_payload), retain=False
                    )
                    if info.rc == mqtt.MQTT_ERR_SUCCESS:
                        logging.info("Published stats for %s: %s", s["name"], mqtt_payload)
                    else:
                        logging.error(
                            "Failed to publish stats for %s: %s",
                            s["name"],
                            mqtt.error_string(info.rc),
                        )
                else:
                    logging.info("Stats for %s: %s", s["name"], mqtt_payload)
            except Exception as e:
                # bei Fehler zumindest Uptime/Temp leer publishen, damit Entity weiterlebt
                err = {
                    "cpu": 0,
                    "mem": 0,
                    "disk": 0,
                    "uptime": 0,
                    "temp": None,
                    "net_in": 0,
                    "net_out": 0,
                    "ram": 0,
                    "cores": 0,
                    "os": "",
                    "pkg_count": 0,
                    "pkg_list": "",
                    "docker": 0,
                    "containers": "",
                    "container_stats": [],
                }
                err_payload = err.copy()
                for k in DISABLED_ENTITIES:
                    err_payload.pop(k, None)
                web_stats.append({"name": s["name"], **err})
                if client:
                    client.publish(
                        f"vserver_ssh/{s['name']}/state", json.dumps(err_payload), retain=False
                    )
                logging.warning("Failed to collect stats for %s: %s", s["name"], e)

        try:
            with open(WEB_STATS_PATH, "w", encoding="utf-8") as f:
                json.dump(web_stats, f)
        except Exception as exc:
            logging.error("Failed to write %s: %s", WEB_STATS_PATH, exc)

        # Intervall einhalten
        sleep_for = INTERVAL - (time.time() - start)
        if sleep_for > 0:
            time.sleep(sleep_for)

if __name__ == "__main__":
    main()
