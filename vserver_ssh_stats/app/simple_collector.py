import json
import time
import getpass
from typing import Dict, Any
import paramiko
from urllib import request, error

INTERVAL_DEFAULT = 30

# State for network rate calculations
_last_net: Dict[str, Dict[str, int]] = {}
_last_ts: Dict[str, float] = {}

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

if [ -n "$temp" ]; then temp_json=$temp; else temp_json=null; fi
printf '{"cpu":%s,"mem":%s,"disk":%s,"uptime":%s,"temp":%s,"rx":%s,"tx":%s}\n' \
  "$cpu" "$mem" "$disk" "$uptime" "$temp_json" "$rx" "$tx"
'''

def sample(host: str, username: str, password: str, port: int) -> Dict[str, Any]:
    out = run_ssh(host=host, username=username, password=password, port=port, cmd=REMOTE_SCRIPT).strip()
    data = json.loads(out)
    now = time.time()
    last = _last_net.get(host)
    last_ts = _last_ts.get(host)
    net_in = net_out = 0.0
    if last and last_ts:
        dt = max(1e-6, now - last_ts)
        net_in = max(0.0, (data["rx"] - last["rx"]) / dt)
        net_out = max(0.0, (data["tx"] - last["tx"]) / dt)
    _last_net[host] = {"rx": data["rx"], "tx": data["tx"]}
    _last_ts[host] = now
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
    servers = []
    while True:
        host = input("Server host (leave blank to finish): ").strip()
        if not host:
            break
        username = input("Username: ").strip()
        password = getpass.getpass("Password: ")
        port_input = input("Port [22]: ").strip()
        port = int(port_input) if port_input else 22
        name_input = input(f"Server name [{host}]: ").strip()
        name = name_input or host.replace('.', '_')
        servers.append({
            "name": name,
            "host": host,
            "username": username,
            "password": password,
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
                stats = sample(srv["host"], srv["username"], srv["password"], srv["port"])
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
    }
    for key, value in data.items():
        entity = f"sensor.{name}_{key}"
        state = value if value is not None else "unknown"
        body = json.dumps({
            "state": state,
            "attributes": {
                "unit_of_measurement": units.get(key),
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
