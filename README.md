# VServer SSH Stats – Home Assistant Integration

[Deutsch](README.de.md) | [Español](README.es.md) | [Français](README.fr.md)

![VServer SSH Stats logo](images/logo/logo.png)

## Overview
The **VServer SSH Stats** integration for Home Assistant allows you to monitor remote Linux servers (vServers, Raspberry Pi, or dedicated machines) without installing any additional agents on the target machines.

It connects via **SSH** (using IP address, username, and password or SSH key) and collects system metrics directly from `/proc`, `df`, and other standard Linux interfaces. The metrics appear as native sensors in Home Assistant.

This makes it possible to get real-time CPU, memory, disk, uptime, network throughput, and temperature information from all your servers inside Home Assistant dashboards.

The integration also provides Home Assistant services to run ad-hoc commands on your servers.

---

## Features
- No software installation required on the target server (only SSH access).
- Supports multiple servers with individual configuration.
- Configurable via Home Assistant UI (config flow).
- Supports password and SSH key authentication.
- Home Assistant services and button entities for remote commands, package updates, and reboots.
- Automatically discovers SSH-enabled hosts on your local network for quick setup, while still allowing manual configuration. Compatible servers announcing themselves via Zeroconf also appear under Home Assistant's **Discovered** section.
- Collects:
  - CPU usage (%)
  - Memory usage (%)
  - Total RAM (MB)
  - Disk usage (% for `/`)
  - Network throughput (bytes/s, in and out)
  - Uptime (seconds)
  - Temperature (°C, if available)
  - CPU cores
  - Load average (1/5/15 min)
  - CPU frequency (MHz)
  - Operating system version
  - Installed packages (count and list)
  - Docker installation, running containers, and per-container CPU/memory usage
  - VNC support status
  - HTTP/HTTPS web server status
  - SSH enabled status
- Configurable update interval (default: 30 seconds).
- Services to fetch the server's local IP, uptime, list active SSH connections, run commands, update packages, and reboot the host.


---

## Installation

### Via HACS (Home Assistant Community Store)
1. Ensure [HACS](https://hacs.xyz) is installed in Home Assistant.
2. In HACS, add `https://github.com/404GamerNotFound/vserver-ssh-stats` as a custom repository (type: integration).
3. Search for **VServer SSH Stats** and install the integration.
4. Restart Home Assistant to load the new integration.

Example from HACS:

![VServer SSH Stats in HACS](images/screeshots/Screenshot5.png)


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
- `sensor.<name>_load_1` – 1‑minute load average
- `sensor.<name>_load_5` – 5‑minute load average
- `sensor.<name>_load_15` – 15‑minute load average
- `sensor.<name>_cpu_freq` – CPU frequency (MHz)
- `sensor.<name>_os` – Operating system version
- `sensor.<name>_pkg_count` – Pending update count
- `sensor.<name>_pkg_list` – Pending update packages (first 10)
- `sensor.<name>_docker` – 1 if Docker is installed, 0 otherwise
- `sensor.<name>_containers` – Running Docker containers (comma-separated list)
- `sensor.<name>_vnc` – "yes" if a VNC server is detected
- `sensor.<name>_web` – "yes" if an HTTP or HTTPS service is listening
- `sensor.<name>_ssh` – "yes" if the SSH service is listening
- For each running container: `sensor.<name>_container_<container>_cpu` (CPU usage %) and `sensor.<name>_container_<container>_mem` (memory usage %)

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

---

## Requirements
- Home Assistant.
- SSH access to the monitored servers.
- Linux-based target servers (any distro with `/proc` and `df`).

---

## License
This project is licensed under the **MIT License**.

---

## Author
**Tony Brüser**
Original author and maintainer of this integration.
