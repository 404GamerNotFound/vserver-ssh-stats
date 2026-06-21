# Changelog

## Unreleased
- Fixed the custom command sensor add/edit form for current Voluptuous versions by passing `vol.Strip` as a validator instead of calling it without a value.
- Added a per-server purge-history button that removes recorder history for the host and its integration-owned child-device entities.
- Deferred all initial SSH collectors until Home Assistant has fully started so recorder startup and database migrations are not competing with remote polling.
- Excluded volatile process, container, command-output, timing, and availability details from recorder persistence while keeping them visible as live entity attributes.
- Added scheduled custom command sensors that reuse a configured server connection and provide independent collection intervals, command timeouts, bounded multi-line output, and UI-based add/edit/remove management.
- Added process totals/running/zombie metrics with a per-boot observed peak, TCP/socket/conntrack metrics and capacity warning, software RAID state/rebuild metrics, optional SMART/NVMe device health collection with child devices, and Docker memory-limit utilization/PID/CPU-throttling/disk-usage metrics.
- Hardened the new collectors with partial storage-read reporting, per-command storage timeouts, serial-based storage identities, IPv6 socket accounting, an explicit per-container memory-limit warning, and read-only powercap handling without permission changes.
- Enforced SSH host-key verification with pinned OpenSSH SHA256 fingerprints for collectors and remote actions. Existing servers must be edited once to add trusted fingerprints; missing or changed keys are rejected before authentication.
- Added per-server monitored TCP ports in the UI/options flow and created `port_<port>_open` binary sensors based on reachability from Home Assistant.

## v1.3.1
- Expanded the options flow so existing servers can be edited, added, removed, or fully replaced from the integration gear menu.
- Added configurable SSH connect and collection command timeouts, plus timing sensors for SSH connection and collection duration.
- Fixed root disk usage collection and hardened CPU/memory collection against empty or zero counters.
- Aligned README release references with the manifest version.

## v1.2.28
- Deduplicated zeroconf discovery by assigning a stable unique ID, enabling the ignore button.
- Removed the manifest funding entry to satisfy hassfest validation (donation link remains in the README).

## v1.2.27
- Expanded the README with a services/events overview and donation details.
- Added a funding link so the donation button is visible in Home Assistant.

## v1.2.25
- Handle zeroconf discovery payloads delivered as objects to avoid setup errors when reconfiguring.
- Format container list strings with spaces after commas for readability.

## v1.2.24
- Added a number box input for configuring the update interval during setup and in the options flow.
- Parallelized initial coordinator refreshes to speed up startup when multiple servers are configured.

## v1.2.10
- Dynamically create container CPU and RAM sensors when new Docker containers appear on a monitored server.
- Mark removed containers as unavailable so they disappear automatically after reloading the integration configuration.
- Resolve SSH key paths relative to the Home Assistant config directory, validate that the file exists during setup, and accept
  those relative paths in service calls.
- Document where to store SSH private keys and which path to provide when using the configuration wizard.

## v1.2.9
- Provide service translation strings across all supported languages so hassfest validation passes.
- Document the 1.2.9 release in each README to keep metadata consistent with the manifest.

## v1.2.8
- Align documentation to reference the v1.2.8 release across all languages.
- Fix the screenshots asset directory name so linked images render correctly in HACS.

## v1.2.7
- Added configurable SSH port support to the onboarding flow and stored server definitions.
- Enabled multi-server setup directly from the UI and provided an options flow to edit the list later on.
- Introduced Home Assistant diagnostics support to aid HACS quality requirements.
- Documented sudo requirements for remote actions and added release management guidance.
