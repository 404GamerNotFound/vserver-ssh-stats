# VServer SSH Stats – Home Assistant Add-on

[Deutsch](README.de.md) | [Español](README.es.md) | [Français](README.fr.md)

## Overview
The **VServer SSH Stats** add-on for Home Assistant allows you to monitor remote Linux servers (vServers, Raspberry Pi, or dedicated machines) without installing any additional agents on the target machines.  

The add-on connects via **SSH** (using IP address, username, and password or SSH key) and collects system metrics directly from `/proc`, `df`, and other standard Linux interfaces.  
The metrics are then published to Home Assistant via **MQTT Discovery**, so they appear as native sensors.

This makes it possible to get real-time CPU, memory, disk, uptime, network throughput, and temperature information from all your servers inside Home Assistant dashboards.

---

## Features
- No software installation required on the target server (only SSH access).
- Supports multiple servers with individual configuration.
- Supports password and SSH key authentication.
- Collects:
  - CPU usage (%)
  - Memory usage (%)
  - Total RAM (MB)
  - Disk usage (% for `/`)
  - Network throughput (bytes/s, in and out)
  - Uptime (seconds)
  - Temperature (°C, if available)
  - CPU cores
  - Operating system version
  - Installed packages (count and list)
- Automatic **MQTT Discovery** for easy integration with Home Assistant.
- Configurable update interval (default: 30 seconds).
- Optional lightweight web interface that can be shown in the Home Assistant sidebar.

### Standalone Usage Without MQTT

If you want to gather stats without using MQTT, run `app/simple_collector.py`. The script lets you enter one or more servers (press Enter on the host prompt to finish). For each server it asks for host, username, and either a password or the path to an SSH key plus optional port, then prints a JSON line including the server name with CPU, memory, disk, network, uptime and temperature every 30 seconds.

Optionally you can enter your Home Assistant base URL and a long-lived access token. When provided, the script will create sensors like `sensor.<name>_cpu`, `sensor.<name>_mem`, etc., via the Home Assistant REST API for each server so the values show up in the UI without MQTT.

The main collector (`app/collector.py`) also supports a lightweight mode without MQTT: simply run it without the `MQTT_HOST` environment variable. In that case the collected statistics are logged to the console instead of being published to a broker.


---

## Installation

### Via HACS (Home Assistant Community Store)
1. Ensure [HACS](https://hacs.xyz) is installed in Home Assistant.
2. In HACS, add `https://github.com/404GamerNotFound/vserver-ssh-stats` as a custom repository (type: integration).
3. Search for **VServer SSH Stats** and install the integration.
4. Restart Home Assistant to load the new integration.

### Manual Add-on Installation
1. Copy the add-on folder `vserver_ssh_stats` into your local Home Assistant add-on repository
   (e.g. `/addons/vserver_ssh_stats`).

2. In Home Assistant:
   - Navigate to **Settings → Add-ons → Add-on Store**.
   - Click the three-dot menu → **Repositories**.
   - Add your local add-on repository path or Git repository containing this add-on.

3. The add-on **VServer SSH Stats** should now appear in the list. Click **Install**.

4. Configure the add-on (see below).

5. Start the add-on.

6. After a short while, new entities (sensors) will automatically appear in Home Assistant via MQTT Discovery.

---

## Configuration

The configuration is stored in `options.json` (editable via the add-on UI).  

Example:

```yaml
mqtt_host: homeassistant
mqtt_port: 1883
mqtt_user: mqttuser
mqtt_pass: mqttpassword
interval_seconds: 30
servers:
  - name: "pi5"
    host: "192.168.1.10"
    username: "tony"
    password: "supersecret"
  - name: "vps1"
    host: "203.0.113.42"
    username: "root"
    key: "/config/ssh/id_rsa"
    port: 22
```

### Options
- **mqtt_host** – Hostname/IP of your MQTT broker (usually `homeassistant`).  
- **mqtt_port** – Port of the MQTT broker (default: `1883`).  
- **mqtt_user / mqtt_pass** – MQTT credentials.  
- **interval_seconds** – Polling interval in seconds (minimum 5).  
- **servers** – List of servers to monitor:  
  - `name` – Friendly name (used as entity prefix).  
  - `host` – IP address or hostname of the server.
  - `username` – SSH username.
  - `password` – SSH password (optional if `key` is used).
  - `key` – Path to an SSH private key file (optional).
  - `port` – (Optional) SSH port (default `22`).

---

## Entities Created

For each server, the following entities will be available:

- `sensor.<name>_cpu` – CPU usage (%)  
- `sensor.<name>_mem` – Memory usage (%)  
- `sensor.<name>_disk` – Disk usage (%)
- `sensor.<name>_net_in` – Network inbound (bytes/s)
- `sensor.<name>_net_out` – Network outbound (bytes/s)
- `sensor.<name>_uptime` – Uptime (seconds)
- `sensor.<name>_temp` – Temperature (°C, if available)
- `sensor.<name>_ram` – Total RAM (MB)
- `sensor.<name>_cores` – CPU cores
- `sensor.<name>_os` – Operating system version
- `sensor.<name>_pkg_count` – Installed package count
- `sensor.<name>_pkg_list` – Installed packages (first 10)

---

## Example Lovelace Dashboard

```yaml
type: vertical-stack
cards:
  - type: gauge
    name: VPS1 CPU
    entity: sensor.vps1_cpu
  - type: gauge
    name: VPS1 Memory
    entity: sensor.vps1_mem
  - type: entities
    title: VPS1 Details
    entities:
      - sensor.vps1_disk
      - sensor.vps1_net_in
      - sensor.vps1_net_out
      - sensor.vps1_uptime
      - sensor.vps1_temp
```

## Security Notes
- It is recommended to create a dedicated, restricted user for SSH monitoring (with read-only access to `/proc` and `df`).  
- SSH password authentication is supported, but **SSH key authentication** is strongly recommended for production use.  
- Network traffic between Home Assistant and your servers is unencrypted unless you enable TLS for MQTT.  

---

## Requirements
- Home Assistant with MQTT broker (built-in Mosquitto or external).  
- SSH access to the monitored servers.  
- Linux-based target servers (any distro with `/proc` and `df`).  

---

## License
This project is licensed under the **MIT License**.

---

## Author
**Tony Brüser**  
Original author and maintainer of this add-on.  
