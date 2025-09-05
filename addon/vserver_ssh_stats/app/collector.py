import json
import logging
import os
import re
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set

import paho.mqtt.client as mqtt
import paramiko

from net_cache import NetStatsCache
from remote_script import REMOTE_SCRIPT

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

# Verfolgte Container pro Server für MQTT Discovery
_container_discovered: Dict[str, Set[str]] = defaultdict(set)
_sensor_discovered: Dict[str, Set[str]] = defaultdict(set)
net_cache = NetStatsCache()


def _sanitize(name: str) -> str:
    """Return a lowercase, MQTT/HA friendly name."""
    return re.sub(r"[^a-zA-Z0-9_]+", "_", name).lower()


def _flatten_sensors(data: Any) -> Dict[str, float]:
    """Flatten lm-sensors JSON output."""
    result: Dict[str, float] = {}

    def _recurse(prefix: str, obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                ksan = _sanitize(str(k))
                new_prefix = f"{prefix}_{ksan}" if prefix else ksan
                _recurse(new_prefix, v)
        else:
            try:
                result[f"sensor_{prefix}"] = float(obj)
            except (TypeError, ValueError):
                pass

    _recurse("", data)
    return result


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
        ("swap", "%", None, False),
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
        ("vnc", None, None, True),
        ("web", None, None, True),
        ("ssh", None, None, True),
        ("local_ip", None, None, True),
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


def ensure_sensor_discovery(name: str, sample: Dict[str, Any]) -> None:
    """Publish discovery topics for hardware sensors."""
    if not client:
        return
    known = _sensor_discovered[name]
    for key in sample.keys():
        if not key.startswith("sensor_") or key in known or key in DISABLED_ENTITIES:
            continue
        known.add(key)
        lower = key.lower()
        unit = None
        device_class = None
        if "temp" in lower:
            unit = "°C"
            device_class = "temperature"
        elif "fan" in lower:
            unit = "RPM"
        elif "power" in lower:
            unit = "W"
        elif lower.startswith("sensor_in") or "volt" in lower:
            unit = "V"
        publish_discovery(name, key, unit, device_class)

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
        # Some package manager operations may need more time on slower hosts.
        # Give the remote script a generous timeout so we still get metrics
        # instead of failing the entire update cycle.
        stdin, stdout, stderr = ssh.exec_command(cmd, timeout=60)
        out = stdout.read().decode("utf-8", "ignore")
        err = stderr.read().decode("utf-8", "ignore")
        if err and not out:
            raise RuntimeError(err.strip())
        return out
    finally:
        ssh.close()

# Remote-Script: CPU (über /proc/stat doppelt), Mem (/proc/meminfo), Disk (df /), Uptime, Temp (thermal_zone0), Net (bytes um Interface-summen)
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
    net_in, net_out = net_cache.compute(srv["name"], data["rx"], data["tx"], now)
    sensors = _flatten_sensors(data.get("sensors", {}))

    # Antwort reduzieren
    result = {
        "cpu": int(data["cpu"]),
        "mem": int(data["mem"]),
        "swap": int(data.get("swap", 0)),
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
        "vnc": data.get("vnc", ""),
        "web": data.get("web", ""),
        "ssh": data.get("ssh", ""),
        "local_ip": data.get("local_ip", ""),
        "container_stats": data.get("container_stats", []),
    }
    result.update(sensors)
    return result

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
            initial = sample_server(s)
            if client:
                ensure_sensor_discovery(s["name"], initial)
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
                    ensure_sensor_discovery(s["name"], payload)
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
                    "swap": 0,
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
                    "vnc": "",
                    "web": "",
                    "ssh": "",
                    "local_ip": "",
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
