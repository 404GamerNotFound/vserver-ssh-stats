# Feature Backlog Ideas

This backlog captures practical enhancements that could be added to the integration.

## High Impact

1. **Connection quality metrics**
   - Expose SSH connect latency and command roundtrip time sensors.
   - Useful to detect network degradation before hard outages.

2. **Filesystem coverage**
   - Track multiple mount points (for example `/`, `/var`, `/home`, `/data`) instead of only root.
   - Include per-mount used/available GiB plus utilization percent.

3. **Power and energy metrics**
   - Add optional sensors for RAPL/energy counters where supported.
   - Enable energy dashboards and anomaly alerts for servers with changing power draw.

4. **Process-level hotspot detection**
   - Optional top-N process sensor attributes (CPU and memory consumers).
   - Speeds up troubleshooting when system load spikes.

## Reliability and UX

5. **Adaptive polling/backoff**
   - Automatically increase polling interval after repeated connection errors.
   - Return to normal interval after successful reconnects.

6. **Per-host command timeout configuration**
   - Add options for SSH connect timeout and remote command timeout.
   - Prevent one slow host from stalling collector cycles.

7. **Service call result entities/events**
   - Create dedicated event payload schema and optional last-run status sensors for package update/reboot commands.
   - Easier to build robust notifications in Home Assistant.

8. **Entity categories and diagnostics metadata**
   - Mark config/informational entities with suitable Home Assistant categories.
   - Improves dashboard organization and reduces clutter in default views.

9. **Richer diagnostics export**
    - Include sanitized timing, command failures, and capability detection in diagnostics output.
    - Makes bug reports easier for maintainers to triage.

## Security and Governance

10. **Safer command policy**
    - Add optional allowlist for `run_command` service.
    - Reduces accidental execution of unsafe commands.

11. **Least-privilege setup helper**
    - Provide a guided sudoers template generator in docs/scripts.
    - Makes secure onboarding faster and less error-prone.

12. **Credential health checks**
    - Warn when password auth is used or key permissions are weak.
    - Encourage secure defaults directly in config flow.

## Nice-to-Have

13. **Template dashboard blueprint**
    - Ship reusable Lovelace dashboard templates for single and multi-host deployments.

14. **Historical trend helper sensors**
    - Expose short-term rolling averages (for example 5-minute CPU/memory) to smooth noisy metrics.

15. **Host grouping labels**
    - Add host tags (prod/stage/lab) as attributes to simplify area-based dashboards and automations.

16. **Update channels and release notification sensor**
    - Optional sensor comparing installed integration version vs latest published release.

## Suggested Implementation Order

1. Connection quality metrics + timeout controls.
2. Multi-mount filesystem metrics.
3. Security controls around remote command execution.
4. Process hotspot and energy metrics.
