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
  Port, Benutzername, Passwort, SSH-Schlüssel, Zielsystem, überwachten TCP-Ports, Historien-Aufbewahrung und Polling-Timeouts.
- Unterstützt Passwort- und SSH-Schlüssel-Authentifizierung.
- Home-Assistant-Services und Schaltflächen zum Ausführen von Befehlen, Paket-Updates und Reboots.
- Benutzerdefinierte Befehlssensoren mit eigenem Abfrageintervall und Timeout je Sensor.
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
  - Anzahl aller, laufender und Zombie-Prozesse sowie den höchsten beobachteten Prozesswert seit dem letzten Boot
  - Etablierte und TIME-WAIT-TCP-Verbindungen, Socket-Nutzung und optionale Conntrack-Auslastung
  - Software-RAID-Zustand, Degraded-/Rebuild-Warnungen, Fortschritt, Restzeit und optionale `mdadm`-Details
  - Optionale SMART-/NVMe-Untergeräte mit Temperatur, Verschleiß, Medien-/Sektorfehlern, Betriebsstunden und expliziten Teilfehler-Zählern
  - Docker-Speicherverbrauch/-limit, Limit-Auslastung, Binary-Sensor für erreichtes Limit, PID-Anzahl, CPU-Throttling sowie Speicherbedarf von Images, Containern, Volumes und Build-Cache
  - Systemd-Fehler, fehlgeschlagene Units und Journal-Fehler der letzten 15 Minuten
  - Disk-I/O-Lese- und Schreibrate
  - VNC-Unterstützung
  - HTTP/HTTPS-Webserver-Status
  - SSH aktiviert
  - Benutzerdefinierte TCP-Port-Erreichbarkeit aus Sicht von Home Assistant
- Konfigurierbares Aktualisierungsintervall (Standard: 30 Sekunden).
- Konfigurierbare SSH-Verbindungs- und Sammelbefehls-Timeouts.
- Paketmetriken laufen in einem separaten Intervall (Standard: 43200 Sekunden / 12 Stunden).
- Docker-Metriken laufen in einem separaten Intervall (Standard: 1800 Sekunden / 30 Minuten).
- SMART-/NVMe-Metriken laufen in einem separaten Intervall (Standard: 3600 Sekunden, `0` deaktiviert die Abfrage).
- Langsame Paket-, Docker- und Storage-Teilabfragen nutzen ein eigenes Timeout (Standard: 180 Sekunden); einzelne Storage-Werkzeugaufrufe sind zusätzlich auf 20 Sekunden begrenzt.
- Pro Server konfigurierbare Historien-Aufbewahrung für den Recorder-Purge-Helfer (Standard: 10 Tage).
- Dienste zum Abrufen der lokalen IP-Adresse, der Uptime, Liste aktiver SSH-Verbindungen, zum Ausführen von Befehlen, Aktualisieren von Paketlisten, Upgraden von Paketen, Neustarten des Hosts, Neustarten von Diensten, Docker-Container-Aktionen, Docker-Prune, Cache-Cleanup, Historien-Bereinigung, Diagnosereport und Log-Tail.
- Statussensoren für das letzte Paketupdate und den letzten Neustart mit Zeitstempel, Erfolgsmeldung und Befehlsausgabe als Attribute.
- Zusammenfassender `health_status`-Sensor mit `ok`, `warning`, `critical` oder `offline` sowie Score und Gründen als Attribute.
- Technische Metadaten werden als Home-Assistant-Diagnoseentitäten markiert, damit operative Sensoren übersichtlicher bleiben.
- Meldet erkannte Netzwerk-MAC-Adressen an die Home-Assistant-Geräteregistrierung, sodass Entitäten bei gleicher MAC
  mit vorhandenen UniFi-Network-Geräten zusammengeführt werden können.

## Benutzerdefinierte Befehlssensoren

Unter **Geräte & Dienste > VServer SSH Stats > Konfigurieren** können benutzerdefinierte
Befehlssensoren hinzugefügt, bearbeitet und entfernt werden. Pro Sensor werden ein Name, ein
bereits konfigurierter Server, der Shell-Befehl, das Abfrageintervall in Sekunden und ein
Befehls-Timeout festgelegt. Das Standardintervall beträgt `3600` Sekunden, das Mindestintervall
`5` Sekunden. Der Standard-Timeout beträgt `30` Sekunden und kann maximal `3600` Sekunden
betragen. Cron-Ausdrücke werden nicht unterstützt.

Technisch erhält jeder benutzerdefinierte Sensor einen eigenen Update-Coordinator. Lang laufende
oder selten ausgeführte Befehle blockieren dadurch weder die normale Serverabfrage noch andere
Befehlssensoren. Verwendet werden SSH-Zugangsdaten, Port, Verbindungs-Timeout und die gepinnten
Host-Key-Fingerprints des ausgewählten Servers. Eine stabile interne ID sorgt dafür, dass die
Home-Assistant-Entität beim Umbenennen erhalten bleibt. Wird die Hostadresse eines Servers
geändert, werden zugehörige Sensorzuordnungen aktualisiert; beim Entfernen eines Servers werden
seine Befehlssensoren mit entfernt.

Rein numerische Ausgaben wie `632` werden als numerischer Sensorzustand veröffentlicht. Text und
mehrzeilige Ergebnisse werden bis zum Home-Assistant-Limit von 255 Zeichen als Zustand verwendet;
die vollständige gespeicherte Ausgabe steht zusätzlich im Attribut `output`. Pro Ausführung
werden maximal 16 KiB Ausgabe gespeichert. SSH-Fehler, Timeouts und Exit-Codes ungleich null
setzen ausschließlich den betroffenen Sensor bis zur nächsten erfolgreichen Ausführung auf
`unavailable`.

Die Befehle sind explizit vertrauenswürdige Konfiguration und unterliegen nicht der Allowlist des
Ad-hoc-Dienstes `run_command`. Sie laufen mit den Rechten des ausgewählten SSH-Benutzers. Empfohlen
wird ein eigener, eingeschränkter Monitoring-Benutzer; benötigte erhöhte Leserechte sollten nur
über eng begrenzte, nicht-interaktive `sudo -n`-Regeln vergeben werden.

## Dienste & Events

Die Integration stellt Home-Assistant-Dienste für Remote-Aktionen bereit:

- `vserver_ssh_stats.get_local_ip` – Lokale IP-Adresse des Servers abrufen.
- `vserver_ssh_stats.get_uptime` – Uptime in Sekunden abrufen.
- `vserver_ssh_stats.list_connections` – Aktive SSH-Sitzungen auflisten.
- `vserver_ssh_stats.refresh` – Sofortige Aktualisierung eines oder aller konfigurierten Server anstoßen.
- `vserver_ssh_stats.purge_history_keep_days` – Recorder-Historie eines konfigurierten Servers bereinigen und die gewünschte Anzahl aktueller Tage behalten.
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

Die Historien-Aufbewahrung ersetzt nicht die globale Home-Assistant-Recorder-Konfiguration. Der neue Button
**Alte Historie bereinigen** und der Dienst `purge_history_keep_days` rufen `recorder.purge_entities` für alle
Entitäten des ausgewählten Servers und seiner Untergeräte auf. Ohne explizites `keep_days` wird der pro Server
konfigurierte Wert verwendet.

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
- `sensor.<name>_collection_time_ms` – Laufzeit der schnellen Basis-Datensammlung (ms)
- `sensor.<name>_package_collection_time_ms` – Laufzeit der Paketmetriken-Datensammlung (ms)
- `sensor.<name>_package_collection_error` – Letzter Fehler der Paketmetriken-Datensammlung
- `sensor.<name>_docker_collection_time_ms` – Laufzeit der Docker-Metriken-Datensammlung (ms)
- `sensor.<name>_docker_collection_error` – Letzter Fehler der Docker-Metriken-Datensammlung
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
- Die Prüfung des SSH-Host-Keys ist verpflichtend. Für jeden Server müssen ein oder mehrere verifizierte OpenSSH-`SHA256:`-Fingerprints hinterlegt werden. Paramiko bricht die Verbindung vor der Authentifizierung ab, wenn der Fingerprint fehlt oder sich geändert hat.
- Remote-Aktionen wie Paketaktualisierungen und Neustarts nutzen `sudo`. Stellen Sie sicher, dass das entfernte Konto `apt-get`, `dnf`, `yum` und `reboot` ohne Passwortabfrage ausführen darf (z. B. durch gezielte Einträge in der `/etc/sudoers`). Dokumentieren oder härten Sie diese Rechte pro Server ab, bevor Sie die Buttons/Services einsetzen.
- Für SMART/NVMe und ausführliche RAID-Daten werden optional `smartctl`, `nvme` und `mdadm` verwendet. Die Integration versucht die Abfrage zuerst ohne erhöhte Rechte und danach mit `sudo -n`. Erlauben Sie nur die benötigten read-only Befehle.
- Der Collector verändert keine Rechte unter `/proc`, `/sys`, an Geräten oder an Powercap-Dateien. Nicht lesbare Messwerte bleiben unverfügbar.
- Einzelne SMART-/NVMe-/mdadm-Aufrufe sind auf maximal 20 Sekunden begrenzt. Fehlende Werkzeuge, nicht lesbare Geräte und Teilergebnisse werden getrennt gemeldet.
- Zugriff auf den Docker-Socket und `sudo docker` sind praktisch root-gleichwertig. Gewähren Sie diese Rechte nur einem dedizierten, vertrauenswürdigen Konto und nicht unkontrollierten Automationen.

Die Fingerprints sollten über einen vertrauenswürdigen Kanal direkt auf dem Zielserver ermittelt werden:

```bash
for key in /etc/ssh/ssh_host_*_key.pub; do ssh-keygen -lf "$key" -E sha256; done
```

Die ausgegebenen `SHA256:...`-Werte werden im Konfigurationsdialog einzeln pro Zeile eingetragen. Einträge aus älteren Versionen müssen über **Geräte & Dienste > VServer SSH Stats > Konfigurieren > Bestehenden Server bearbeiten** einmal ergänzt werden. Bis dahin werden SSH-Abfragen aus Sicherheitsgründen blockiert. Bei freien Dienstaufrufen für nicht konfigurierte Hosts muss das Feld `host_key_fingerprints` mitgegeben werden.

Eine Beispiel-Konfiguration für `/etc/sudoers.d/<Ihr-user-name-für-VServer-SSH-Stats>`
```
# Monitoring-User <Ihr user name für VServer-SSH-Stats>: wenige spezifische CMDs ohne Passwort:
<Ihr vserver-user name> ALL=(root) NOPASSWD: /usr/bin/apt update

# Auf echten Produktionssystemen sollte apt upgrade nicht hierüber ausgeführt werden.
# Nutzen Sie statt dessen die Buttons in der HA-UI, um den Prozess gezielt steuern zu können.
<Ihr vserver-user name> ALL=(root) NOPASSWD: /usr/bin/apt upgrade
<Ihr vserver-user name> ALL=(root) NOPASSWD: /sbin/reboot

# Optionale, ausschließlich lesende Storage-Abfragen. Pfade lokal mit command -v prüfen.
<Ihr vserver-user name> ALL=(root) NOPASSWD: /usr/sbin/smartctl -a /dev/*
<Ihr vserver-user name> ALL=(root) NOPASSWD: /usr/sbin/nvme smart-log /dev/*
<Ihr vserver-user name> ALL=(root) NOPASSWD: /usr/sbin/mdadm --detail --export /dev/md*
```

---

## Release-Management
- Aktuelle Manifest-Version: **v1.4.34** (siehe `manifest.json`).
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
