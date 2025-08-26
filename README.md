# VServer SSH Stats – Home Assistant Add-on

## Overview
The **VServer SSH Stats** add-on for Home Assistant allows you to monitor remote Linux servers (vServers, Raspberry Pi, or dedicated machines) without installing any additional agents on the target machines.  

The add-on connects via **SSH** (using IP address, username, and password or SSH key) and collects system metrics directly from `/proc`, `df`, and other standard Linux interfaces.  
The metrics are then published to Home Assistant via **MQTT Discovery**, so they appear as native sensors.

This makes it possible to get real-time CPU, memory, disk, uptime, network throughput, and temperature information from all your servers inside Home Assistant dashboards.

---

## Features
- No software installation required on the target server (only SSH access).  
- Supports multiple servers with individual configuration.  
- Collects:
  - CPU usage (%)
  - Memory usage (%)
  - Disk usage (% for `/`)
  - Network throughput (bytes/s, in and out)
  - Uptime (seconds)
  - Temperature (°C, if available)  
- Automatic **MQTT Discovery** for easy integration with Home Assistant.  
- Configurable update interval (default: 30 seconds).  

---

## Installation
1. Copy the add-on folder `vserver-ssh-stats` into your local Home Assistant add-on repository  
   (e.g. `/addons/vserver-ssh-stats`).  

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
    password: "anothersecret"
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
  - `password` – SSH password.  
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
