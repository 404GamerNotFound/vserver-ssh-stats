# VServer SSH Stats – Home Assistant Integration

![VServer SSH Stats Logo](images/logo/logo.png)

## Übersicht
Die **VServer SSH Stats** Integration für Home Assistant ermöglicht die Überwachung entfernter Linux-Server (vServer, Raspberry Pi oder dedizierte Maschinen), ohne zusätzliche Agenten auf den Zielrechnern zu installieren.

Die Integration verbindet sich per **SSH** (über IP-Adresse, Benutzername und Passwort oder SSH-Schlüssel) und sammelt Systemmetriken direkt aus `/proc`, `df` und anderen Standard-Linux-Schnittstellen. Die Werte erscheinen als native Sensoren in Home Assistant.

So lassen sich CPU-, Speicher-, Festplatten-, Laufzeit-, Netzwerkdurchsatz- und Temperaturinformationen in Echtzeit in Home Assistant Dashboards anzeigen.

Die Integration stellt außerdem Home-Assistant-Dienste bereit, um ad-hoc Befehle auf den Servern auszuführen.

---

## Funktionen
- Keine Softwareinstallation auf dem Zielserver erforderlich (nur SSH-Zugriff).
- Unterstützt mehrere Server mit individueller Konfiguration.
- Konfiguration über die Home Assistant Oberfläche (Config Flow).
- Bestehende Server können über die Integrationsoptionen bearbeitet, hinzugefügt oder entfernt werden, inklusive Host,
  Port, Benutzername, Passwort, SSH-Schlüssel, Zielsystem, überwachten TCP-Ports und Polling-Timeouts.
- Unterstützt Passwort- und SSH-Schlüssel-Authentifizierung.
- Home-Assistant-Services und Schaltflächen zum Ausführen von Befehlen, Paket-Updates und Reboots.
- Optionale Allowlist für `run_command`, um ad-hoc SSH-Befehle einzuschränken.
- Adaptives Polling-Backoff nach wiederholten Verbindungsfehlern.
- Automatische Erkennung von SSH-fähigen Hosts im lokalen Netzwerk zur schnellen Einrichtung, manuelle Konfiguration bleibt weiterhin möglich. Kompatible Server, die sich per Zeroconf ankündigen, erscheinen außerdem im Bereich **Entdeckt** von Home Assistant.
- Sammelt:
  - CPU-Auslastung (%)
  - Speicherauslastung (%)
  - Gesamter RAM (MB)
  - Festplattenauslastung (% für `/`)
  - Netzwerkdurchsatz (Bytes/s, ein- und ausgehend)
  - Laufzeit (Sekunden)
  - Temperatur (°C, falls verfügbar)
  - CPU-Kerne
  - Last (1/5/15 Minuten)
  - CPU-Frequenz (MHz)
  - Health-Score, Reboot-Required, Read-only-Root-Dateisystem und Temperaturstatus
  - Betriebssystem-Version
  - Letzter Boot-Zeitpunkt, Kernel-Version, primäre IP und primäre MAC-Adresse
  - Installierte Pakete (Anzahl und Liste)
  - Sicherheitsupdates separat
  - Docker-Installation, laufende Container, CPU-/Speicherauslastung je Container, Image, Status, Restart-Count, Ports und Health-State
  - Unhealthy-Container und aufsummierte Docker-Restarts
  - Automatische Erstellung neuer CPU- und Speichersensoren, sobald zusätzliche Container starten
  - Top-CPU-Prozesse mit PID, Befehl, CPU- und Speicherauslastung als Sensorattribute
  - Systemd-Fehler, fehlgeschlagene Units und Journal-Fehler der letzten 15 Minuten
  - Disk-I/O-Lese- und Schreibrate
  - VNC-Unterstützung
  - HTTP/HTTPS-Webserver-Status
  - SSH aktiviert
  - Benutzerdefinierte TCP-Port-Erreichbarkeit aus Sicht von Home Assistant
- Konfigurierbares Aktualisierungsintervall (Standard: 30 Sekunden).
- Konfigurierbare SSH-Verbindungs- und Sammelbefehls-Timeouts.
- Dienste zum Abrufen der lokalen IP-Adresse, der Uptime, Liste aktiver SSH-Verbindungen, zum Ausführen von Befehlen, Aktualisieren von Paketlisten, Upgraden von Paketen, Neustarten des Hosts, Neustarten von Diensten, Docker-Container-Aktionen, Docker-Prune, Cache-Cleanup, Diagnosereport und Log-Tail.
- Statussensoren für das letzte Paketupdate und den letzten Neustart mit Zeitstempel, Erfolgsmeldung und Befehlsausgabe als Attribute.
- Zusammenfassender `health_status`-Sensor mit `ok`, `warning`, `critical` oder `offline` sowie Score und Gründen als Attribute.
- Technische Metadaten werden als Home-Assistant-Diagnoseentitäten markiert, damit operative Sensoren übersichtlicher bleiben.
- Meldet erkannte Netzwerk-MAC-Adressen an die Home-Assistant-Geräteregistrierung, sodass Entitäten bei gleicher MAC
  mit vorhandenen UniFi-Network-Geräten zusammengeführt werden können.

## Dienste & Events

Die Integration stellt Home-Assistant-Dienste für Remote-Aktionen bereit:

- `vserver_ssh_stats.get_local_ip` – Lokale IP-Adresse des Servers abrufen.
- `vserver_ssh_stats.get_uptime` – Uptime in Sekunden abrufen.
- `vserver_ssh_stats.list_connections` – Aktive SSH-Sitzungen auflisten.
- `vserver_ssh_stats.refresh` – Sofortige Aktualisierung eines oder aller konfigurierten Server anstoßen.
- `vserver_ssh_stats.run_command` – Beliebigen Shell-Befehl remote ausführen.
- `vserver_ssh_stats.update_package_list` – Paketlisten/Metadaten aktualisieren.
- `vserver_ssh_stats.update_packages` – Systempakete aktualisieren (apt/dnf/yum).
- `vserver_ssh_stats.upgrade_packages` – Installierte Pakete upgraden.
- `vserver_ssh_stats.reboot_host` – Den Remote-Host neu starten.
- `vserver_ssh_stats.restart_service` – Einen Systemdienst neu starten.
- `vserver_ssh_stats.start_docker_container`, `stop_docker_container`, `restart_docker_container` – Docker-Container steuern.
- `vserver_ssh_stats.prune_docker` – Ungenutzte Docker-Ressourcen entfernen.
- `vserver_ssh_stats.clear_package_cache` – Paketmanager-Caches bereinigen.
- `vserver_ssh_stats.get_server_diagnostics` – Kompakten Diagnosereport abrufen.
- `vserver_ssh_stats.tail_logs` – Aktuelle Journal-/Systemlogs abrufen.

Nach Abschluss von `update_packages` oder `reboot_host` aktualisiert die Integration den passenden Statussensor und löst
ein Event mit `host`, `output` und `success` im Payload aus. Wenn in den Integrationsoptionen eine Command-Allowlist
konfiguriert ist, akzeptiert `run_command` nur exakte Einträge oder Präfixregeln mit abschließendem `*`.

## Unterstützung

Wenn dir die Integration hilft, freue ich mich über eine Spende:

[PayPal – Spenden](https://www.paypal.com/paypalme/TonyBrueser)

Vielen Dank für deine Unterstützung! Jede Spende hilft, das Projekt weiterzuentwickeln.

---

## Installation

### Über HACS (Home Assistant Community Store)
1. Stelle sicher, dass [HACS](https://hacs.xyz) in Home Assistant installiert ist.
2. Füge in HACS `https://github.com/404GamerNotFound/vserver-ssh-stats` als benutzerdefiniertes Repository (Typ: Integration) hinzu.
3. Suche nach **VServer SSH Stats** und installiere die Integration.
4. Starte Home Assistant neu, um die neue Integration zu laden.

Beispiel aus HACS:

![HACS Beispiel](images/screenshots/Screenshot5.png)

## Erstellte Entitäten

Für jeden Server sind folgende Entitäten verfügbar:

- `sensor.<name>_health_status` – Zusammengefasster Serverzustand (`ok`, `warning`, `critical` oder `offline`) mit Score und Gründen
- `sensor.<name>_health_score` – Numerischer Health-Score (0–100)
- `sensor.<name>_cpu` – CPU-Auslastung (%)
- `sensor.<name>_mem` – Speicherauslastung (%)
- `sensor.<name>_swap_usage` – Swap-Auslastung (%)
- `sensor.<name>_swap_total` – Gesamter Swap (GiB)
- `sensor.<name>_disk` – Festplattenauslastung (%)
- `sensor.<name>_disk_capacity_total` – Gesamte erkannte Festplattenkapazität (GiB)
- `sensor.<name>_disk_io_read` / `sensor.<name>_disk_io_write` – Disk-I/O in Bytes/s
- `sensor.<name>_net_in` – Netzwerkeingang (Bytes/s)
- `sensor.<name>_net_out` – Netzwerkausgang (Bytes/s)
- `sensor.<name>_ssh_connect_time_ms` – Dauer des SSH-Verbindungsaufbaus (ms)
- `sensor.<name>_collection_time_ms` – Laufzeit der vollständigen Datensammlung (ms)
- `sensor.<name>_uptime` – Laufzeit (Sekunden)
- `sensor.<name>_temp` – Temperatur (°C, falls verfügbar)
- `sensor.<name>_cpu_temperature_status` – Temperaturbewertung (`ok`, `warning`, `critical`)
- `sensor.<name>_ram` – Gesamt-RAM (MB)
- `sensor.<name>_cores` – CPU-Kerne
- `sensor.<name>_load_1` – 1‑Minuten‑Last
- `sensor.<name>_load_5` – 5‑Minuten‑Last
- `sensor.<name>_load_15` – 15‑Minuten‑Last
- `sensor.<name>_cpu_freq` – CPU‑Frequenz (MHz)
- `sensor.<name>_os` – Betriebssystem-Version
- `sensor.<name>_last_boot` – Letzter Boot-Zeitpunkt
- `sensor.<name>_kernel_version` – Kernel-Version
- `sensor.<name>_pkg_count` – Anzahl verfügbarer Updates
- `sensor.<name>_pkg_list` – Verfügbare Updates (erste 10)
- `sensor.<name>_security_updates` – Anzahl verfügbarer Sicherheitsupdates
- `sensor.<name>_docker` – 1, wenn Docker installiert ist, sonst 0
- `sensor.<name>_containers` – Laufende Docker-Container (kommagetrennte Liste), mit Image, Status, Restart-Count, Ports und Health-State in den Attributen
- `sensor.<name>_docker_unhealthy_containers` – Anzahl nicht gesunder Container
- `sensor.<name>_docker_restart_count_total` – Summe der Docker-Restarts
- `sensor.<name>_top_processes` – Top-CPU-Prozesse, Details liegen in den Sensorattributen
- `sensor.<name>_failed_systemd_units` – Anzahl fehlgeschlagener systemd-Units
- `sensor.<name>_failed_systemd_units_list` – Liste fehlgeschlagener systemd-Units
- `sensor.<name>_journal_errors` – Journal-Fehler der letzten 15 Minuten
- `sensor.<name>_network_primary_mac` – Primäre MAC-Adresse
- `sensor.<name>_primary_ip` – Primäre IP-Adresse
- `sensor.<name>_last_package_update_status` – Ergebnis des letzten Paketupdates (`success`, `failed` oder `never_run`)
- `sensor.<name>_last_package_list_update_status` – Ergebnis der letzten Paketlisten-Aktualisierung
- `sensor.<name>_last_package_upgrade_status` – Ergebnis des letzten Paket-Upgrades
- `sensor.<name>_last_reboot_status` – Ergebnis des letzten Neustarts (`success`, `failed` oder `never_run`)
- `binary_sensor.<name>_reboot_required` – Neustart erforderlich
- `binary_sensor.<name>_root_fs_readonly` – Root-Dateisystem ist read-only
- `binary_sensor.<name>_port_<port>_open` – Konfigurierter TCP-Port ist aus Sicht von Home Assistant erreichbar
- `sensor.<name>_vnc` – "ja", wenn ein VNC-Server erkannt wurde
- `sensor.<name>_web` – "ja", wenn ein HTTP- oder HTTPS-Dienst lauscht
- `sensor.<name>_ssh` – "ja", wenn der SSH-Dienst lauscht
- Für jeden erkannten Mountpoint: Sensoren für Gesamt- und freien Speicher in GiB
- Für jeden laufenden Container: `sensor.<name>_container_<container>_cpu` (CPU-Auslastung %) und `sensor.<name>_container_<container>_mem` (Speicherauslastung %)

Operative Sensoren bleiben im normalen Sensorbereich von Home Assistant. Technische Metadaten wie OS, RAM-Größe,
CPU-Kerne, Timing-Werte, Paketliste, Dienstfähigkeiten, Top-Prozesse und letzte Aktionsstatus werden als
Diagnoseentitäten markiert.

---

## Beispiel für ein Lovelace-Dashboard

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

## Ablage des SSH-Schlüssels

- Unter **Home Assistant OS** die private SSH-Schlüsseldatei in das Verzeichnis `/config/ssh/` kopieren (z. B. über das File-
  Editor-Add-on oder die Samba-Freigabe). Ein Schlüssel `id_vserver` landet so unter `/config/ssh/id_vserver`.
- Im Konfigurationsassistenten entweder den absoluten Pfad `/config/ssh/id_vserver` oder den relativen Pfad `ssh/id_vserver`
  (ausgehend vom Home-Assistant-Konfigurationsordner) eintragen. Beide Varianten werden unterstützt.
- Es muss immer die **private** Schlüsseldatei angegeben werden, nicht die `.pub`-Datei.
- Bei Home Assistant Container/Core kann auch ein beliebiger absoluter Pfad verwendet werden, auf den Home Assistant zugreifen
  darf.

## Sicherheitshinweise
- Es wird empfohlen, einen dedizierten, eingeschränkten Benutzer für das SSH-Monitoring zu erstellen (mit nur Lesezugriff auf `/proc` und `df`).
- Aufgrund der Syntax der abgesetzten Befehle muss /bin/bash oder ein kompatibler Shell für den User gewählt werden, /bin/sh versteht einige Ausdrücke nicht.
- SSH-Passwortauthentifizierung wird unterstützt, aber **SSH-Schlüssel-Authentifizierung** wird für den produktiven Einsatz dringend empfohlen.
- Remote-Aktionen wie Paketaktualisierungen und Neustarts nutzen `sudo`. Stellen Sie sicher, dass das entfernte Konto `apt-get`, `dnf`, `yum` und `reboot` ohne Passwortabfrage ausführen darf (z. B. durch gezielte Einträge in der `/etc/sudoers`). Dokumentieren oder härten Sie diese Rechte pro Server ab, bevor Sie die Buttons/Services einsetzen.

Eine Beispiel-Konfiguration für `/etc/sudoers.d/<Ihr-user-name-für-VServer-SSH-Stats>`
```
# Monitoring-User <Ihr user name für VServer-SSH-Stats>: wenige spezifische CMDs ohne Passwort:
<Ihr vserver-user name> ALL=(root) NOPASSWD: /usr/bin/apt update

# Auf echten Produktionssystemen sollte apt upgrade nicht hierüber ausgeführt werden.
# Nutzen Sie statt dessen die Buttons in der HA-UI, um den Prozess gezielt steuern zu können.
<Ihr vserver-user name> ALL=(root) NOPASSWD: /usr/bin/apt upgrade
<Ihr vserver-user name> ALL=(root) NOPASSWD: /sbin/reboot

# Leistungswerte auf jüngeren Ubuntu / Debian Systemen und evtl. weiteren
<Ihr vserver-user name> ALL=(root) NOPASSWD: /usr/bin/chmod a+r /sys/class/powercap/*/energy_uj
<Ihr vserver-user name> ALL=(root) NOPASSWD: /usr/bin/chmod a-r /sys/class/powercap/*/energy_uj
```

---

## Release-Management
- Aktuelle stabile Version: **v1.3.1** (siehe `manifest.json`).
- Erstellen Sie für jede veröffentlichte Version ein Git-Tag (z. B. `git tag v1.3.1`) sowie ein zugehöriges GitHub-Release, damit HACS Updates sauber nachvollziehen kann.
- Nutzen Sie das vorhandene Skript `scripts/bump_version.py`, um die Versionsnummer der Integration vor einer neuen Veröffentlichung zu erhöhen.
- Pflegen Sie wichtige Änderungen zusätzlich in der [`CHANGELOG.md`](CHANGELOG.md).

---

## Anforderungen
- Home Assistant.
- SSH-Zugang zu den überwachten Servern.
- Linux-basierte Zielserver (beliebige Distribution mit `/proc` und `df`).

---

## Lizenz
Dieses Projekt ist unter der **MIT-Lizenz** lizenziert.

---

## Autor
**Tony Brüser**
Originalautor und Maintainer dieser Integration.
