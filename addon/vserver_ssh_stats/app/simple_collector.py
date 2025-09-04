import getpass
import json
import re
import time
from typing import Any, Dict, Optional
from urllib import error, request

import paramiko

from net_cache import NetStatsCache
from remote_script import REMOTE_SCRIPT

INTERVAL_DEFAULT = 30

net_cache = NetStatsCache()


def _sanitize(name: str) -> str:
    """Return a lowercase, safe name for keys."""
    return re.sub(r"[^a-zA-Z0-9_]+", "_", name).lower()

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

def sample(host: str, username: str, password: Optional[str], key: Optional[str], port: int) -> Dict[str, Any]:
    out = run_ssh(host=host, username=username, password=password, key=key, port=port, cmd=REMOTE_SCRIPT).strip()
    data = json.loads(out)
    now = time.time()
    net_in, net_out = net_cache.compute(host, data["rx"], data["tx"], now)
    cont_stats = data.get("container_stats", [])
    result = {
        "cpu": int(data["cpu"]),
        "mem": int(data["mem"]),
        "disk": int(data["disk"]),
        "uptime": int(data["uptime"]),
        "temp": (None if data["temp"] is None else float(data["temp"])),
        "net_in": round(net_in, 2),
        "net_out": round(net_out, 2),
        "ram": int(data.get("ram", 0)),
        "cores": int(data.get("cores", 0)),
        "os": data.get("os", ""),
        "pkg_count": int(data.get("pkg_count", 0)),
        "pkg_list": data.get("pkg_list", ""),
        "docker": int(data.get("docker", 0)),
        "containers": data.get("containers", ""),
        "container_stats": cont_stats,
    }
    for c in cont_stats:
        cname = _sanitize(c.get("name", ""))
        result[f"container_{cname}_cpu"] = c.get("cpu", 0)
        result[f"container_{cname}_mem"] = c.get("mem", 0)
    return result

def main():
    servers = []
    while True:
        host = input("Server host (leave blank to finish): ").strip()
        if not host:
            break
        username = input("Username: ").strip()
        password = getpass.getpass("Password (leave blank for key): ")
        key = ""
        if not password:
            key = input("Path to private key file: ").strip()
        port_input = input("Port [22]: ").strip()
        port = int(port_input) if port_input else 22
        name_input = input(f"Server name [{host}]: ").strip()
        name = name_input or host.replace('.', '_')
        servers.append({
            "name": name,
            "host": host,
            "username": username,
            "password": password or None,
            "key": key or None,
            "port": port,
        })

    if not servers:
        print("No servers configured, exiting.")
        return
    ha_url = input("Home Assistant URL (e.g. http://homeassistant.local:8123) [skip]: ").strip()
    ha_token = ""
    if ha_url:
        ha_token = getpass.getpass("Long-lived access token: ").strip()
    interval_input = input("Interval seconds [30]: ").strip()
    interval = max(5, int(interval_input)) if interval_input else INTERVAL_DEFAULT

    while True:
        for srv in servers:
            try:
                stats = sample(
                    srv["host"], srv["username"], srv.get("password"), srv.get("key"), srv["port"]
                )
                output = {"name": srv["name"], **stats}
                print(json.dumps(output))
                if ha_url and ha_token:
                    send_to_home_assistant(ha_url, ha_token, srv["name"], stats)
            except Exception as e:
                print(f"Error collecting stats for {srv['name']}: {e}")
        time.sleep(interval)

def send_to_home_assistant(base_url: str, token: str, name: str, data: Dict[str, Any]):
    base = base_url.rstrip('/')
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    units = {
        "cpu": "%",
        "mem": "%",
        "disk": "%",
        "net_in": "B/s",
        "net_out": "B/s",
        "uptime": "s",
        "temp": "°C",
        "ram": "MB",
    }
    for key, value in data.items():
        entity = f"sensor.{name}_{key}"
        state = value if value is not None else "unknown"
        unit = units.get(key)
        if unit is None and key.startswith("container_"):
            unit = "%"
        body = json.dumps({
            "state": state,
            "attributes": {
                "unit_of_measurement": unit,
                "friendly_name": f"{name} {key}"
            }
        }).encode()
        req = request.Request(
            f"{base}/api/states/{entity}",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            request.urlopen(req, timeout=10).read()
        except error.URLError as e:
            print(f"Failed to post {entity}: {e}")

if __name__ == "__main__":
    main()
