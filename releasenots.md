# VServer SSH Stats — Release Notes

## Fixed

- Fixed a collector crash on Linux hosts whose `iptables -S` output contains
  only default policy lines and no custom `-A` rules.
- Default-accept iptables configurations, commonly found on fresh Proxmox VE
  and Synology DSM installations, are now handled as a normal inactive
  firewall state.
- Fixed collector aborts when an `apt-daily-upgrade` or `dnf-automatic` timer
  is inactive or unavailable. This restores Linux polling affected by the
  withdrawn 1.5.8 release.
- Hardened optional network, Fail2ban, certificate, backup, and disk-I/O
  collectors so empty optional results do not propagate a failing status to
  the main collector.
- Prevented the misleading `powershell: command not found` error that could
  surface after the Linux collector stopped before producing JSON output.

## Technical Details

The remote collector runs with `set -e`, so a helper function must return
success when an optional probe has no result. The affected collectors now use
explicit guards and successful return paths. `grep -c '^-A'` also preserves a
zero rule count without propagating its normal no-match exit code.

## Upgrade Notes

No configuration changes are required. Update the integration and reload it
to resume normal polling on affected hosts.

## Support the Project

[![Stars](https://img.shields.io/github/stars/404GamerNotFound/vserver-ssh-stats?style=for-the-badge&logo=github&logoColor=white&label=Stars&color=blue)](https://github.com/404GamerNotFound/vserver-ssh-stats/stargazers)
[![Sponsors](https://img.shields.io/github/sponsors/404GamerNotFound?style=for-the-badge&logo=github&logoColor=white&label=Sponsors&color=blue)](https://github.com/sponsors/404GamerNotFound)
[![PayPal](https://img.shields.io/badge/PayPal-ME-blue?style=for-the-badge&logo=paypal&logoColor=white)](https://www.paypal.com/paypalme/TonyBrueser)
[![Revolut](https://img.shields.io/badge/Revolut-ME-blue?style=for-the-badge&logo=revolut&logoColor=white)](https://revolut.me/tony1995)
