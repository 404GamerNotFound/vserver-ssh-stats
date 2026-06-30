# VServer SSH Stats 1.4.16

## BREAKING CHANGES: SSH HOST KEY VERIFICATION IS NOW REQUIRED

This release removes automatic acceptance of unknown SSH host keys. Every SSH connection now requires one or more pinned OpenSSH `SHA256:` **server host-key fingerprints**.

This is not the private SSH key used to log in. It is the fingerprint of the monitored server's SSH host key and protects the connection against man-in-the-middle attacks.

Existing server entries without a pinned fingerprint will stop collecting data after upgrading. Remote actions for those entries will also be blocked until the server configuration is updated.

### Required Upgrade Steps

1. Obtain the host-key fingerprints through a trusted channel, preferably directly on the monitored server:

   ```bash
   for key in /etc/ssh/ssh_host_*_key.pub; do ssh-keygen -lf "$key" -E sha256; done
   ```

2. In Home Assistant, open **Settings > Devices & services > VServer SSH Stats > Configure**.
3. Edit every existing server and enter one or more verified `SHA256:...` fingerprints, one per line.
4. Save the configuration and reload or restart Home Assistant.

Missing, invalid, unknown, or changed host keys are rejected before SSH authentication. Service calls targeting an ad-hoc host must provide `host_key_fingerprints`, unless the host and port match a configured server with stored fingerprints.

If a server host key is legitimately rotated, verify the new fingerprint through a trusted channel and update the configured pin before collection can resume.

## Summary

Version 1.4.16 is a major monitoring and security update. It adds process, storage-health, software-RAID, network-connection, Docker-resource, and configurable TCP-port metrics while hardening SSH and privileged collector behavior.

## Added

### Process Monitoring

- Total, running, and zombie process counts from `/proc`.
- A zombie-process warning binary sensor.
- Highest process count observed for the current system boot while the integration is running.
- Robust `/proc/<pid>/stat` parsing for process names containing spaces or parentheses.

### SMART and NVMe Health

- Optional slow storage-health collector using `smartctl` or `nvme smart-log`.
- Per-drive temperature, wear, media-error, sector-error, power-on-hour, and overall SMART status sensors.
- A separate child device in Home Assistant for every detected physical drive.
- Stable storage identities based on drive serial numbers when available.
- Aggregate SMART-failure warning and failed-device count.
- Explicit counters for detected, successfully collected, and unreadable storage devices.
- Partial collection errors are reported instead of presenting incomplete data as healthy.
- Configurable storage collection interval, defaulting to `3600` seconds. Set it to `0` to disable storage-health collection.

### Software RAID

- Software-RAID discovery and state parsing from `/proc/mdstat`.
- Optional extended array information from `mdadm --detail --export`.
- Binary sensors for degraded arrays and active rebuilds.
- Rebuild progress and estimated remaining-time sensors.
- Support for non-numeric Linux MD device names in addition to names such as `md0`.

### Network Connections

- Established TCP and TCPv6 connection counts.
- TCP and TCPv6 `TIME_WAIT` connection counts.
- Socket and TCP socket usage from `/proc/net/sockstat` and `/proc/net/sockstat6`.
- Optional conntrack count, configured maximum, and utilization percentage.
- Conntrack warning when utilization reaches 80 percent.
- User-configurable TCP port checks performed from Home Assistant, including response time and error details.
- Dynamic `port_<port>_open` connectivity binary sensors.

### Docker Resource Limits

- Per-container memory usage and configured memory limit in bytes.
- Memory-limit utilization percentage.
- Per-container binary sensor indicating that the configured memory limit has been reached.
- Unlimited containers are reported as unknown for limit utilization instead of as a zero-byte limit.
- Per-container PID count.
- Cumulative CPU-throttled period and throttled-time metrics from cgroups v1 or v2.
- Docker disk usage for images, containers, local volumes, and build cache through `docker system df`.
- Additional Docker health-score reasons when a container approaches or reaches its memory limit.

## Changed

- Replaced Paramiko's automatic host-key acceptance with strict fingerprint pinning for collectors and all remote actions.
- Added SSH host-key fingerprint fields to setup, server editing, translations, service schemas, buttons, and Docker actions.
- Added process, RAID, SMART, conntrack, and Docker-limit conditions to the host health score.
- Diagnostic warning sensors now preserve an `unknown` state when the source metric is unavailable instead of incorrectly reporting a cleared warning.
- Slow package, Docker, and storage collectors continue to run independently from the fast base poll.
- Remote actions now respect explicit OS profiles without cross-OS fallback. Linux package actions no longer continue into Windows PowerShell commands when the Linux command fails.
- Action buttons in auto-detect mode reuse the OS already reported by the collector, so recognized Debian/Linux hosts call Linux package commands instead of later falling through to `powershell.exe`.
- Individual SMART, NVMe, and mdadm commands are limited to a maximum of 20 seconds.
- SMART/NVMe and mdadm commands first run as the SSH user and only fall back to non-interactive `sudo -n` when required.
- The collector no longer changes RAPL, powercap, `/sys`, or device permissions with `sudo chmod`. Unreadable metrics remain unavailable.
- Docker CPU-throttling counters use `total_increasing` state semantics in Home Assistant.
- Diagnostics now include the configured storage collection interval.
- Integration version updated to `1.4.16`.

## Security Notes

- Verify host-key fingerprints directly on the server or through another trusted channel. Do not trust an unverified `ssh-keyscan` result by itself.
- Use a dedicated, restricted SSH monitoring account.
- Only grant passwordless sudo access to the exact read-only commands required for `smartctl`, `nvme`, or `mdadm`.
- Docker socket access and `sudo docker` are effectively root-equivalent. Grant these permissions only when that trust level is acceptable.
- The storage collector never modifies disk state, SMART settings, RAID configuration, or filesystem permissions.

Example read-only sudoers rules, with paths adjusted for the target distribution:

```sudoers
<monitoring-user> ALL=(root) NOPASSWD: /usr/sbin/smartctl -a /dev/*
<monitoring-user> ALL=(root) NOPASSWD: /usr/sbin/nvme smart-log /dev/*
<monitoring-user> ALL=(root) NOPASSWD: /usr/sbin/mdadm --detail --export /dev/md*
```

## Compatibility and Requirements

- The new process, RAID, socket, conntrack, cgroup, and storage-health metrics require a Linux target with `/proc` and `/sys`.
- Windows support remains experimental and continues to expose a smaller metric set.
- SMART/SATA support requires `smartmontools` when those metrics are enabled.
- Native NVMe fallback support requires `nvme-cli`.
- Extended RAID details require `mdadm`.
- Docker metrics require access to the Docker daemon. CPU-throttling metrics additionally require readable container cgroup data.
- The highest process count is an in-memory value observed by the integration, not a kernel-maintained historical maximum. It resets after a Home Assistant or integration restart and when a server reboot is detected.

## Documentation

- Updated English and German documentation for all new metrics, security requirements, optional tools, sudo guidance, and collection intervals.
- Updated configuration and service translations in English, German, Spanish, and French.
- Added migration instructions for mandatory SSH host-key fingerprints.
- Updated release and manifest references to version `1.4.16`.

## Validation

- 57 automated tests pass.
- Ruff, ShellCheck, Bash syntax, YAML, JSON, and Git diff validation pass.
- Added regression coverage for SSH host-key validation, process-state parsing, partial storage reads, storage child devices, Docker limits, CPU throttling, collector output stability, OS-specific remote action command selection, and auto-detected action-button OS handling.

## Upgrade Notes

- Complete the SSH host-key migration before expecting collection or remote actions to resume.
- Restart Home Assistant after updating the integration.
- The first storage-health collection may take longer than a regular update because it runs through the independent slow collector.
- Technical metrics and warning entities are grouped as diagnostic entities where appropriate.
- Existing locally renamed entities retain their current entity IDs.

## Support the Project

If this integration saves you time, feel free to support development with a donation:

[PayPal - Donate](https://www.paypal.com/paypalme/TonyBrueser)

Thank you for your support!
