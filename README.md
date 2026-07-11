# VServer SSH Stats - Home Assistant Integration

[![HACS Default](https://img.shields.io/badge/HACS-Default-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/default)


[Deutsch](README.de.md) | [Español](README.es.md) | [Français](README.fr.md)

![VServer SSH Stats logo](images/logo/logo.png)

## Overview

**VServer SSH Stats** is a Home Assistant custom integration for monitoring remote Linux servers over SSH without installing an agent on the target host.

The integration connects to each configured server via SSH, runs a compact collector script, reads standard system interfaces such as `/proc`, `df`, `systemctl`, `journalctl`, Docker commands, and package-manager metadata, and exposes the result as native Home Assistant entities.

It is intended for vServers, VPS instances, Raspberry Pis, dedicated Linux hosts, and similar machines that can be reached from Home Assistant via SSH. A Windows target profile exists as an experimental fallback with a smaller metric set.

Current integration version: **1.4.16**.

## Highlights

- Agentless monitoring over SSH with password or private-key authentication.
- Multi-server setup from the Home Assistant UI.
- Automatic SSH host discovery on local networks and Zeroconf discovery support.
- Per-server options for name, host, SSH port, username, credentials, target OS, monitored TCP ports, history retention days, polling interval, SSH connect timeout, and command timeout.
- Editable options flow for changing, adding, removing, or fully replacing configured servers.
- Adaptive polling backoff after repeated collection failures.
- Native Home Assistant sensors, binary sensors, action buttons, service responses, and events.
- Scheduled custom command sensors with an independent interval and timeout per sensor.
- Optional `run_command` allowlist with exact matches and prefix rules ending in `*`.
- Device registry MAC address reporting so monitored hosts can be associated with existing network devices, for example UniFi devices, when MAC addresses match.
- Diagnostic entities for metadata and action status so operational sensors remain easier to scan.

## Collected Metrics

For each configured server, the integration can collect:

- Health status and health score with reason attributes.
- Online state, last seen time, consecutive failures, and active poll interval.
- CPU usage, CPU cores, CPU frequency, load average for 1/5/15 minutes.
- Memory usage, total RAM, swap usage, and total swap.
- Root disk usage, total detected disk capacity, per-mount total/free sensors, and disk I/O read/write rates.
- Network inbound/outbound throughput.
- Uptime, last boot timestamp, kernel version, operating system version, primary IP, and primary MAC address.
- Temperature and temperature status.
- Power in watts and accumulated energy in kWh when Intel RAPL/powercap data is readable.
- Pending package update count, package list, and security update count.
- Reboot-required state and read-only root filesystem state.
- Docker installation state, running containers, container image/status/ports/health/restart count, unhealthy container count, total restart count, and dynamic per-container CPU/memory sensors.
- Top CPU processes with PID, command, CPU usage, and memory usage attributes.
- Process totals, running and zombie counts, plus the highest count observed during the current boot.
- Established/TIME-WAIT TCP connections, socket usage, and optional conntrack count/capacity utilization.
- Linux software RAID state, degraded/rebuild warnings, rebuild progress, remaining time, and optional `mdadm` details.
- Optional SMART/NVMe health devices with temperature, wear, media errors, sector errors, power-on hours, and explicit partial/error counters.
- Docker memory usage/limits, limit utilization, a per-container limit-reached binary sensor, PID counts, cumulative CPU throttling, and disk usage for images, containers, volumes, and build cache.
- Failed systemd unit count/list and journal error count from the last 15 minutes.
- SSH, web server, and VNC capability/status checks.
- User-configured TCP port reachability from Home Assistant, including response time and error attributes.
- SSH connect time, full collection runtime, collection error, and last collection failure state.

## Installation

### HACS

1. Install [HACS](https://hacs.xyz) in Home Assistant.
2. Add `https://github.com/404GamerNotFound/vserver-ssh-stats` as a custom repository of type `Integration`.
3. Search for **VServer SSH Stats** in HACS and install it.
4. Restart Home Assistant.
5. Go to **Settings > Devices & services > Add integration** and search for **VServer SSH Stats**.

![VServer SSH Stats in HACS](images/screenshots/Screenshot5.png)

### Manual

1. Copy `custom_components/vserver_ssh_stats` into your Home Assistant `custom_components` directory.
2. Restart Home Assistant.
3. Add the integration from **Settings > Devices & services**.

## Configuration

The integration is configured through the Home Assistant UI.

During setup you provide:

- Update interval in seconds. Default: `30`.
- Server name.
- Hostname or IP address.
- SSH port. Default: `22`.
- One or more verified OpenSSH `SHA256:` host-key fingerprints.
- SSH username.
- Password or SSH private-key path.
- Target system profile: `auto`, `debian`, `raspbian`, or experimental `windows`.
- Optional monitored TCP ports, separated by commas, spaces, semicolons, or line breaks.
- History retention days for the integration's recorder purge helper. Default: `10`.
- Whether to add another server in the same integration entry.

In the integration options you can also configure:

- SSH connect timeout. Default: `10` seconds.
- Collection command timeout. Default: `45` seconds.
- Package metrics interval. Default: `43200` seconds (12 hours).
- Docker metrics interval. Default: `1800` seconds (30 minutes).
- SMART/NVMe storage metrics interval. Default: `3600` seconds; set to `0` to disable.
- Slow collector timeout for package, Docker, and storage metrics. Default: `180` seconds; individual storage tool calls are additionally capped at `20` seconds.
- `run_command` allowlist, one command per line.
- Edit an existing server.
- Add another server.
- Remove a server.
- Replace the full server list.
- Add, edit, or remove custom command sensors.

### Custom command sensors

Open the integration options and select **Add a custom command sensor**. Each sensor defines:

- A sensor name and one existing server.
- The remote shell command to execute.
- A collection interval in seconds (default `3600`, minimum `5`).
- A command timeout in seconds (default `30`, maximum `3600`).

Every custom sensor has its own update coordinator, so a slow daily command does not delay the
normal server poll or another custom sensor. It reuses the selected server's SSH credentials,
port, connect timeout, and pinned host-key fingerprints. Numeric output such as `632` becomes a
numeric sensor state. Text and multi-line output becomes the state when it fits Home Assistant's
255-character state limit; the complete retained output is also available in the `output`
attribute. Output retained by the integration is limited to 16 KiB. A timeout, SSH failure, or
non-zero command exit status marks only that custom sensor unavailable until a later successful
run.

Custom commands are explicitly trusted configuration and are not governed by the ad-hoc
`run_command` allowlist. They run with the selected SSH user's permissions. Prefer a dedicated,
restricted monitoring user and narrowly scoped non-interactive `sudo -n` rules when elevated
read access is required. Cron expressions are not supported; use the per-sensor interval in
seconds.

Private-key paths may be absolute, relative to the Home Assistant configuration directory, or use `~` for the container user's home directory. Relative paths such as `ssh/id_vserver` resolve to `/config/ssh/id_vserver` on Home Assistant OS.

SSH host-key verification is mandatory. Obtain the fingerprints through a trusted channel,
preferably directly on the monitored server:

```bash
for key in /etc/ssh/ssh_host_*_key.pub; do ssh-keygen -lf "$key" -E sha256; done
```

Paste the `SHA256:...` values into the setup form, one per line. A network-side
`ssh-keyscan` can help identify offered keys, but its result must be compared with a
trusted server-side fingerprint before it is pinned. Existing entries created before
host-key pinning must be edited once to add fingerprints; SSH collection remains blocked
until they are configured.

## Entities

Entity IDs depend on the Home Assistant entity registry and the configured server name. The examples below use common IDs with `<name>` as the server slug; Home Assistant may keep older IDs after upgrades or apply local renames.

### Core Sensors

- `sensor.<name>_health_status` - `ok`, `warning`, `critical`, or `offline`; attributes include score and reasons.
- `sensor.<name>_health_score` - Numeric health score from 0 to 100.
- `sensor.<name>_cpu` - CPU usage in percent.
- `sensor.<name>_memory` - Memory usage in percent.
- `sensor.<name>_swap_usage` - Swap usage in percent.
- `sensor.<name>_swap_total` - Total swap in GiB.
- `sensor.<name>_disk` - Root disk usage in percent.
- `sensor.<name>_disk_capacity_total` - Total detected disk capacity in GiB.
- `sensor.<name>_disk_io_read` and `sensor.<name>_disk_io_write` - Disk I/O rates in B/s.
- `sensor.<name>_network_in` and `sensor.<name>_network_out` - Network throughput in B/s.
- `sensor.<name>_uptime` - Uptime in seconds.
- `sensor.<name>_temperature` - Temperature in °C when available.
- `sensor.<name>_power` - Power in watts when RAPL/powercap data is available.
- `sensor.<name>_energy_total` - Total accumulated energy in kWh.
- `sensor.<name>_load_1`, `sensor.<name>_load_5`, `sensor.<name>_load_15` - Load averages.
- `sensor.<name>_pkg_count` - Pending package update count.
- `sensor.<name>_security_updates` - Pending security update count.
- `sensor.<name>_containers` - Running container summary with detailed container attributes.
- `sensor.<name>_docker_unhealthy_containers` - Number of unhealthy or exited containers.

### Diagnostic Sensors

- `sensor.<name>_ssh_connect_time` - SSH connection setup time in ms.
- `sensor.<name>_collection_time` - Fast base collection runtime in ms.
- `sensor.<name>_collection_error` - Last collector error text.
- `sensor.<name>_last_collection_failed` - Whether the last collection failed.
- `sensor.<name>_package_collection_time` - Package metrics collection runtime in ms.
- `sensor.<name>_package_collection_error` - Last package metrics collector error text.
- `sensor.<name>_docker_collection_time` - Docker metrics collection runtime in ms.
- `sensor.<name>_docker_collection_error` - Last Docker metrics collector error text.
- `sensor.<name>_cpu_temperature_status` - `ok`, `warning`, or `critical`.
- `sensor.<name>_ram` - Total RAM in MB.
- `sensor.<name>_cores` - CPU core count.
- `sensor.<name>_cpu_frequency` - CPU frequency in MHz.
- `sensor.<name>_os` - Operating system version.
- `sensor.<name>_last_boot` - Last boot timestamp.
- `sensor.<name>_kernel_version` - Kernel version.
- `sensor.<name>_pkg_list` - First pending package names.
- `sensor.<name>_docker_containers` - Docker installed/running indicator.
- `sensor.<name>_docker_restart_count_total` - Sum of Docker container restart counts.
- `sensor.<name>_top_processes` - Top CPU process summary with process attributes.
- `sensor.<name>_failed_systemd_units` - Failed systemd unit count.
- `sensor.<name>_failed_systemd_units_list` - Failed systemd units with list attributes.
- `sensor.<name>_journal_errors` - Journal errors from the last 15 minutes.
- `sensor.<name>_primary_mac` - Primary MAC address.
- `sensor.<name>_primary_ip` - Primary IP address.
- `sensor.<name>_vnc_supported` - VNC status.
- `sensor.<name>_web_server` - HTTP/HTTPS listener status.
- `sensor.<name>_ssh_enabled` - SSH listener status.

### Dynamic Sensors

- `sensor.<name>_disk_<disk>_total` - Per-mount total capacity in GiB.
- `sensor.<name>_disk_<disk>_free` - Per-mount free capacity in GiB.
- `sensor.<name>_container_<container>_cpu` - Per-container CPU usage in percent.
- `sensor.<name>_container_<container>_mem` - Per-container memory usage in percent.

Dynamic disk and container sensors are created when the integration sees new mounts or containers in collected data.

### Binary Sensors

- `binary_sensor.<name>_online` - Host availability based on successful collection.
- `binary_sensor.<name>_reboot_required` - Reboot-required flag.
- `binary_sensor.<name>_root_filesystem_read_only` - Root filesystem read-only flag.
- `binary_sensor.<name>_port_<port>_open` - Configured TCP port reachability from Home Assistant.

Port binary sensors expose `host`, `port`, `protocol`, `checked_from`, `response_time_ms`, and `error` attributes.

### Action Status Sensors

Each server gets diagnostic status sensors for the latest result of supported actions:

- Last package metadata refresh.
- Last package update.
- Last package upgrade.
- Last reboot.
- Last manual refresh.
- Last Docker prune.
- Last package cache cleanup.
- Last service restart.
- Last Docker container start/stop/restart.
- Last diagnostics request.
- Last log tail request.

Attributes include `success`, `last_run`, and `output`.

### Buttons

For each server, the integration creates buttons for:

- Refresh now.
- Update package list.
- Upgrade packages.
- Update packages.
- Prune Docker.
- Clear package cache.
- Reboot host.
- Purge all history.
- Purge old history using the configured retention days.

Buttons use the stored server configuration and call the matching Home Assistant service.

The retention-aware history button and service call Home Assistant's `recorder.purge_entities`
with `keep_days` for all entities belonging to the selected server and its child devices. They do
not replace Home Assistant's global recorder retention settings.

## Services

The integration registers services under the `vserver_ssh_stats` domain. Services return response data when supported by the installed Home Assistant version and also fire events for automations.

### Local Home Assistant Helper Services

These services run on the Home Assistant host, not on a monitored remote server:

- `vserver_ssh_stats.get_local_ip` - Return Home Assistant's local IP address.
- `vserver_ssh_stats.get_uptime` - Return Home Assistant host uptime in seconds.
- `vserver_ssh_stats.list_connections` - Return active SSH session IPs reported by `who` on the Home Assistant host.
- `vserver_ssh_stats.purge_history_keep_days` - Purge recorder history for a configured server while keeping recent days. Fields: `host`, optional `keep_days`.

### Refresh Service

- `vserver_ssh_stats.refresh` - Request an immediate coordinator refresh for one host or all configured hosts.

Optional field:

- `host` - Hostname or IP. If omitted, all configured servers are refreshed.

### Remote Action Services

These services connect to a remote host over SSH. Common fields are:

- `host` - Hostname or IP address.
- `username` - SSH username.
- `password` - Optional SSH password.
- `key` - Optional SSH private-key path.
- `host_key_fingerprints` - OpenSSH `SHA256:` fingerprints for ad-hoc hosts. May be omitted when `host` and `port` match a configured server with stored pins.
- `port` - SSH port. Default: `22`.
- `connect_timeout` - SSH connect timeout. Default: `10`, maximum `300`.
- `command_timeout` - Remote command timeout. Default: `300` for actions, maximum `3600`.
- `target_os` - For OS actions: `auto`, `debian`, `raspbian`, or `windows`.

Available remote services:

- `vserver_ssh_stats.run_command` - Run an arbitrary SSH command, subject to the configured allowlist.
- `vserver_ssh_stats.update_package_list` - Refresh package metadata.
- `vserver_ssh_stats.update_packages` - Update/upgrade packages with the target OS package manager.
- `vserver_ssh_stats.upgrade_packages` - Update/upgrade packages with the target OS package manager.
- `vserver_ssh_stats.reboot_host` - Reboot the host.
- `vserver_ssh_stats.restart_service` - Restart one service; requires `service`.
- `vserver_ssh_stats.start_docker_container` - Start one Docker container; requires `container`.
- `vserver_ssh_stats.stop_docker_container` - Stop one Docker container; requires `container`.
- `vserver_ssh_stats.restart_docker_container` - Restart one Docker container; requires `container`.
- `vserver_ssh_stats.prune_docker` - Run `docker system prune -f`.
- `vserver_ssh_stats.clear_package_cache` - Clear package-manager caches.
- `vserver_ssh_stats.get_server_diagnostics` - Return a compact diagnostics report.
- `vserver_ssh_stats.tail_logs` - Return recent journal or system log lines; optional `service`, optional `lines` from 1 to 1000.

Service and container names are validated and may contain letters, numbers, `.`, `_`, `-`, and `@`.

## Events

The integration fires service-specific events such as:

- `vserver_ssh_stats_local_ip`
- `vserver_ssh_stats_uptime`
- `vserver_ssh_stats_connections`
- `vserver_ssh_stats_purge_history_keep_days`
- `vserver_ssh_stats_command`
- `vserver_ssh_stats_refresh`
- `vserver_ssh_stats_update_package_list`
- `vserver_ssh_stats_update_packages`
- `vserver_ssh_stats_upgrade_packages`
- `vserver_ssh_stats_reboot`
- `vserver_ssh_stats_restart_service`
- `vserver_ssh_stats_start_docker_container`
- `vserver_ssh_stats_stop_docker_container`
- `vserver_ssh_stats_restart_docker_container`
- `vserver_ssh_stats_prune_docker`
- `vserver_ssh_stats_clear_package_cache`
- `vserver_ssh_stats_server_diagnostics`
- `vserver_ssh_stats_tail_logs`

Remote action status updates are also fired as `vserver_ssh_stats_action_status` with `host`, `action`, `status`, `success`, `output`, and `timestamp`.

## Example Dashboard

```yaml
type: vertical-stack
cards:
  - type: gauge
    name: VPS1 CPU
    entity: sensor.vps1_cpu
  - type: gauge
    name: VPS1 Memory
    entity: sensor.vps1_memory
  - type: gauge
    name: VPS1 Health
    entity: sensor.vps1_health_score
  - type: entities
    title: VPS1 Details
    entities:
      - binary_sensor.vps1_online
      - sensor.vps1_health_status
      - sensor.vps1_disk
      - sensor.vps1_network_in
      - sensor.vps1_network_out
      - sensor.vps1_uptime
      - sensor.vps1_temperature
      - sensor.vps1_security_updates
```

## Example Automations and Scripts

Sample alert automations are available in [`examples/automations/health_alerts.yaml`](examples/automations/health_alerts.yaml).

Reusable maintenance scripts are available in [`examples/scripts/maintenance_scripts.yaml`](examples/scripts/maintenance_scripts.yaml). They cover common operations such as uptime checks, SSH session listing, package updates, and reboot actions with confirmation.

Typical automation triggers include:

- `sensor.<name>_health_status` changing to `warning`, `critical`, or `offline`.
- `binary_sensor.<name>_online` turning off.
- `sensor.<name>_security_updates` rising above `0`.
- Disk, memory, CPU, or swap usage crossing your own thresholds.
- `binary_sensor.<name>_port_<port>_open` turning off.
- `vserver_ssh_stats_action_status` reporting a failed maintenance action.

## SSH Key Storage

For Home Assistant OS, place private keys below `/config/ssh/`, for example:

```text
/config/ssh/id_vserver
```

In the integration UI you may enter either:

```text
/config/ssh/id_vserver
```

or:

```text
ssh/id_vserver
```

Always reference the private key file, not the `.pub` public key. For Home Assistant Container or Core installations, any absolute path readable by the Home Assistant process can be used.

## Security Notes

- Prefer SSH private-key authentication over password authentication.
- Verify and pin each server's SSH host-key fingerprints through a trusted channel.
- Use a dedicated monitoring account where possible.
- The Linux collector expects `bash` or a compatible shell for the full metric set.
- The collector reads standard system files and runs read-only inspection commands where possible.
- The collector never changes `/proc`, `/sys`, device, or powercap permissions. Metrics that are not readable remain unavailable.
- SMART/NVMe/mdadm fallbacks use only non-interactive `sudo -n` with fixed read-only command forms and a maximum of 20 seconds per tool invocation.
- Docker socket access and `sudo docker` are effectively root-equivalent. Only enable them for a dedicated account when that trust level is acceptable; do not expose that account to untrusted automations.
- Remote maintenance actions use commands such as package-manager updates, Docker operations, service restarts, and reboot. Restrict `sudo` permissions carefully.
- `run_command` is intentionally powerful. Configure the allowlist if you expose it to automations or dashboards.
- Paramiko rejects missing or changed host keys before authentication. Ad-hoc service calls
  must provide `host_key_fingerprints` unless the host and port match a configured server.

Example sudoers rules for a dedicated monitoring user:

```sudoers
# Monitoring user for VServer SSH Stats.
<your-vserver-user> ALL=(root) NOPASSWD: /usr/bin/apt-get update
<your-vserver-user> ALL=(root) NOPASSWD: /usr/bin/apt-get -y upgrade
<your-vserver-user> ALL=(root) NOPASSWD: /sbin/reboot

# Optional read-only storage-health commands. Verify executable paths locally.
<your-vserver-user> ALL=(root) NOPASSWD: /usr/sbin/smartctl -a /dev/*
<your-vserver-user> ALL=(root) NOPASSWD: /usr/sbin/nvme smart-log /dev/*
<your-vserver-user> ALL=(root) NOPASSWD: /usr/sbin/mdadm --detail --export /dev/md*
```

Adjust paths for your distribution. For production systems, keep sudoers entries as narrow as possible and avoid broad wildcard permissions for maintenance commands.

## Requirements

- Home Assistant with custom integrations enabled.
- HACS for the recommended installation path.
- SSH access from Home Assistant to each monitored host.
- Python dependency from the manifest: `paramiko>=3.4.0`.
- Linux target with common tools such as `bash`, `/proc`, `df`, `awk`, `sed`, and optionally `systemctl`, `journalctl`, Docker, and package-manager tools.
- Optional package-manager support: `apt-get`, `dnf`, `yum`, `pacman`, `zypper`, or `apk`.
- Optional storage health support: `smartmontools` (`smartctl`), `nvme-cli`, and `mdadm`. The collector first runs these tools as the SSH user and only then tries passwordless `sudo -n`. Missing tools, inaccessible devices, and partial reads are exposed separately instead of being reported as healthy.

## Release Management

- Current manifest version: **1.4.16**.
- Update `custom_components/vserver_ssh_stats/manifest.json` for each release.
- Use `scripts/bump_version.py` when preparing a version bump.
- Add notable changes to [`CHANGELOG.md`](CHANGELOG.md).
- Use [`.github/release_template.md`](.github/release_template.md) for manually drafted GitHub releases so every release keeps the same structure and includes the donation link.
- If you use GitHub's generated release notes, [`.github/release.yml`](.github/release.yml) provides the category format. Keep the support section from the release template in the final release body.
- The `Validate Release Notes` workflow checks published or edited releases and fails when the PayPal donation link is missing.
- Create a matching Git tag and GitHub release, for example `v1.4.16`, so HACS can track updates reliably.

## Support the Project

If this integration saves you time, you can support development with a donation:

[PayPal - Donate](https://www.paypal.com/paypalme/TonyBrueser)

Thank you for your support.

## License

This project is licensed under the **MIT License**.

## Author

**Tony Brüser**  
Original author and maintainer of this integration.
