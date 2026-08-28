# AGENTS.md — Projektgedächtnis für Claude

Nicht committen (steht in `.gitignore`). Zweck: beim nächsten Chat schnell wieder produktiv sein,
ohne das ganze Repo neu zu scannen. Bei größeren Architekturänderungen bitte aktualisieren.

## Was ist das Projekt

Home Assistant Custom Integration (`custom_components/vserver_ssh_stats/`), agentless Monitoring
von Linux-Servern/VPS über SSH. Kein Agent auf dem Zielhost nötig — ein Bash-Collector-Skript wird
per SSH ausgeführt, JSON zurückgegeben, geparst, als HA-Entities exponiert. HACS-Integration,
Domain `vserver_ssh_stats`, aktueller Owner/Repo: `404GamerNotFound/vserver-ssh-stats`.

Sprachen: README.md (EN, Haupt-Doku), README.de.md, README.es.md, README.fr.md — nur README.md
wird bei Feature-Arbeit routinemäßig gepflegt, die übersetzten READMEs typischerweise nicht
(außer Versionsnummer via `scripts/bump_version.py`).

## Architektur — wer macht was

- **`remote_collector.sh`** — Bash-Skript, läuft auf dem Zielhost, gibt ein großes JSON-Objekt aus.
  Wird als String in Python eingebettet (`remote_script.py` liefert `REMOTE_SCRIPT`).
  Modi via `VSERVER_SSH_STATS_MODE`: `full` (Standard-Poll inkl. Docker/Pakete),
  `base`/unset-ish (schneller Poll ohne Docker/Pakete), `packages`, `docker`, `storage`
  (separate langsame Collectoren mit eigenem Intervall). Alles **außerhalb** des
  Mode-`case`-Blocks (journal_errors, systemd_failures, root_fs_readonly,
  failed_ssh_logins, firewall_status, fail2ban_status, disk_io) läuft bei **jedem** Poll,
  egal welcher Modus.
- **`ssh_collector.py`** — SSH-Verbindungsaufbau (Paramiko), Host-Key-Pinning via
  `ssh_security.configure_pinned_host_keys`, JSON-Parsing, Normalisierung der Rohdaten in ein
  Python-Dict (`async_sample`, `async_sample_packages/_docker/_storage`,
  `async_run_custom_command`). Windows-Fallback via PowerShell-Einzeiler
  (`WINDOWS_REMOTE_SCRIPT`) — bei neuen Feldern **immer auch dort** Default ergänzen.
- **`coordinator.py`** — `VServerCoordinator` (`DataUpdateCoordinator` pro Server) pollt via
  `async_sample`. Bewusst **resilient**: SSH-/Auth-Fehler führen NICHT automatisch zu
  `UpdateFailed`/unavailable, sondern zu `collection_error` im Datendict + `last_collection_failed`
  Flag, alte Werte bleiben sichtbar. Adaptive Backoff (`consecutive_failures`,
  `_record_failure`/`_record_success`). Auth-spezifische Fehler
  (`collection_error_is_auth`) zählen separat (`consecutive_auth_failures`) und lösen nach 3x in
  Folge `entry.async_start_reauth(...)` aus (Reauth-Flow, s.u.). Nach erfolgreichem Poll werden
  HA-Repair-Issues synchronisiert (`_sync_base_issues`, `_sync_issue`) für: RAID degraded,
  root_fs_readonly, fail2ban_elevated (≥20 gebannte IPs), smart_failure (aus dem Storage-Slow-Tier).
  `CustomCommandCoordinator` = eigener Coordinator pro benutzerdefiniertem Command-Sensor.
- **`__init__.py`** — Setup/Unload des Config Entry, alle `vserver_ssh_stats.*` Services
  (`hass.services.async_register`), Remote-Action-Commandbuilder (`_build_*_commands`,
  OS-Fallback via `_build_os_command_sequence`). **Importiert bewusst keine Sibling-Module**
  wie `.coordinator`/`.sensor` auf Modulebene (Zirkular-Import-Risiko, da diese `from . import
  DOMAIN` machen und `DOMAIN` erst in `__init__.py` definiert wird). Gemeinsame Konstanten daher in
  `util.py`, nicht in `coordinator.py`.
- **`config_flow.py`** — `ConfigFlow` (Ersteinrichtung + Zeroconf + Reauth) und
  `OptionsFlowHandler` (Edit/Add/Remove/Replace Server, Custom Sensors). Zentrale
  Formular-Schema-Funktion: `_build_server_schema` (ein Ort für alle Server-Formulare). Zentrale
  Validierung für Edit/Add/Replace: `OptionsFlowHandler._server_from_input` (async). Jede
  Server-Anlage/-Änderung führt einen **echten SSH-Verbindungstest** aus
  (`_async_test_ssh_connection`, nutzt `ssh_collector.async_run_custom_command` mit einem
  harmlosen `echo`), bevor gespeichert wird — blockierend bei Fehler. Reauth:
  `async_step_reauth`/`async_step_reauth_confirm`, holt Entry über
  `self.hass.config_entries.async_get_entry(self.context["entry_id"])` (kein
  `_get_reauth_entry()`/`async_update_reload_and_abort()` genutzt — nicht in allen HA-Versionen
  vorhanden, stattdessen manuell `async_update_entry` + `async_reload`).
- **`sensor.py` / `binary_sensor.py` / `button.py` / `switch.py`** — Entity-Plattformen.
  **Wichtig für Entity-IDs:** `entity_id = slugify(f"{server_name} {description.name}")`, NICHT
  vom internen `key`. Fallstricke: `name="Memory"` → `_memory` (nicht `_mem`); `name="Disk I/O
  Read"` → `_disk_i_o_read` (Slash wird zu `_o_`, nicht einfach entfernt) — vor dem Schreiben von
  Dashboards/Docs mit `homeassistant.util.slugify(...)` gegenprüfen, wenn unsicher.
  Container-Sensoren (`container_<name>_*`) und der Memory-Limit-Binary-Sensor exponieren
  `compose_project`/`compose_service` als Attribute.
- **`net_cache.py`** — zustandsbehaftete Caches zwischen Polls (Rate-Berechnung für
  Netzwerk/Disk-IO, kumulative Energie, Prozess-Peak, `RollingAverageCache` für 5-Min-CPU/RAM-Avg).
- **`util.py`** — geteilte Defaults/Konstanten (Timeouts, Intervalle) UND jetzt auch
  `ISSUE_SUFFIXES`, `FAIL2BAN_ELEVATED_THRESHOLD`, `AUTH_FAILURE_REAUTH_THRESHOLD` (dorthin verschoben
  wegen Zirkular-Import, s.o.).
- **`ssh_security.py`** — Host-Key-Pinning (`PinnedHostKeyPolicy`, `SSHHostKeyError`),
  Fingerprint-Parsing/-Validierung.
- **`scripts/generate_sudoers_template.py`** — generiert minimal-privilegierte Sudoers-Snippets
  passend zu den tatsächlich von der Integration ausgeführten Befehlen (Paket-Update, Reboot,
  Docker, Service-Restart, Storage-Health, Firewall-Status, Fail2ban, Journal-Read).

## Test- und Lint-Setup — wichtige Einschränkungen

- Python 3.10 in dieser Dev-Umgebung. `custom_components/vserver_ssh_stats/__init__.py` nutzt
  `from datetime import UTC` (Python 3.11+). **Das komplette Package kann hier nicht importiert
  werden** (auch nicht `config_flow.py`/`coordinator.py`/`ssh_collector.py` einzeln, da Python beim
  Import eines Submoduls immer erst `__init__.py` des Parent-Package ausführt).
  → Deshalb: keine Tests, die das Package direkt importieren. Stattdessen etabliertes Muster:
  **AST-Extraktion** einzelner reiner Funktionen aus dem Quelltext (siehe `tests/test_extended_metrics.py`,
  `tests/test_update_entity.py` für Beispiele: `ast.parse(...)`, gewünschte `FunctionDef`-Nodes
  rausfiltern, `exec(compile(...))` in eigenem Namespace).
- `paramiko` ist in dieser Test-Umgebung **nicht installiert** (auch nicht in
  `requirements_test.txt`) — Code, der paramiko direkt braucht, kann hier nicht ausgeführt/getestet
  werden, nur `py_compile` + Linting + manuelle Review.
- `remote_collector.sh`-Tests: `tests/test_remote_script.py`, laufen das Skript direkt mit `bash`
  und Shell-Function-Stubs (z. B. `fail2ban-client() { ... }`, `command -v` findet Shell-Functions
  automatisch). Muster: `VSERVER_SSH_STATS_MODE=base bash ...` zum schnellen Testen ohne Docker/Pakete.
- Standard-Testlauf:
  ```bash
  python3 -m pytest tests/ -q
  ruff check custom_components/ scripts/ tests/   # NICHT *.sh mitgeben, ruff versucht dann Python-Parsing
  bash -n custom_components/vserver_ssh_stats/remote_collector.sh
  ```
- **Fallstrick:** `custom_components/vserver_ssh_stats/__pycache__/*.pyc` sind in Git getrackt
  (Altlast, nicht vom `.gitignore`-Pattern erfasst weil bereits getrackt). Nach jedem `pytest`-Lauf
  vor dem Abschluss prüfen: `git status --short | grep pycache` → falls verändert:
  `git checkout -- custom_components/vserver_ssh_stats/__pycache__/`.
- HA-Version hier installiert: 2023.7.3 (alt, ggf. nicht repräsentativ für echte Nutzer). API-Checks
  wie `hasattr`/`inspect.signature` gegen dieses Env sind nur ein Anhaltspunkt, kein Beweis für
  Kompatibilität mit aktuellem HA. Bei riskanten neueren APIs (z. B. Reauth) defensiv/mit Fallback
  schreiben, siehe `coordinator.py::_start_reauth`.
- 4 unterstützte Sprachen für `strings.json`/`translations/`: en, de, es, fr. Beim Hinzufügen neuer
  Config-Flow-Felder/Service-Felder/Error-Codes/Issues: **immer alle 5 Dateien** synchron halten
  (`strings.json` + 4 `translations/*.json`) — bewährtes Vorgehen: kleines Python-Skript mit
  `object_pairs_hook=OrderedDict`, gezielt Keys einfügen, dann `json.dumps(..., ensure_ascii=False)`
  zurückschreiben (minimiert Diff-Rauschen ggü. kompletter Neuformatierung).

## Entity-Namen sind NICHT übersetzt

Sensor-/Binary-Sensor-/Button-Namen (`description.name`, `ACTION_BUTTONS`-Tupel etc.) sind fest
verdrahtete englische Strings, unabhängig von HA-Sprache. Übersetzt werden nur: Config-/Options-Flow
Formularfelder, Service-Namen/-Beschreibungen, Error-/Abort-Reasons, Issue-Titel/-Beschreibungen
(`strings.json` `issues`-Key). Kein `translation_key` bei normalen Sensoren nötig.

## Chronologie dieser Session (grob, neueste zuerst)

1. Docker-Compose-Attribute (`compose_project`/`compose_service`) + `test_connection`
   Button/Service (wiederverwendet `_run_remote_action`-Maschinerie, kein neuer SSH-Code).
2. Reauth-Flow (`async_step_reauth`) + HA Repairs/Issues (RAID, root_fs, fail2ban, SMART).
3. Live-SSH-Verbindungstest im Config Flow vor dem Speichern (`_async_test_ssh_connection`).
4. Update-Channel-Entity (`update.py`, GitHub-Releases-Check 1x/Tag), Host-Label-Attribut,
   Lovelace-Dashboard-Beispiele (`examples/dashboards/`), 5-Min-Rolling-Average CPU/Mem.
5. Fail2ban-Integration (Banned-IP-Count, Jails-Attribut).
6. Failed-SSH-Logins-Sensor, Firewall-Status-Sensor (ufw/firewalld/nftables/iptables),
   `scripts/generate_sudoers_template.py`.

Vorher (nicht in dieser Session, aber bereits vorhanden): kompletter ursprünglicher
`FEATURE_BACKLOG.md` (16 Punkte: Connection-Latenz, Multi-Mount-Disk, Power/Energy,
Prozess-Hotspots, adaptives Polling, Timeouts, Command-Allowlist, Entity-Categories,
Diagnostics-Export, Sudoers-Doku, RAID/SMART, Docker-Memory-Limits, u. a.) — **alle bereits
umgesetzt**, `FEATURE_BACKLOG.md` ist im Grunde abgearbeitet.

## Offene Ideen (mehrfach besprochen, noch nicht umgesetzt)

- SSL/TLS-Zertifikatsablauf-Sensor für lokale Webserver.
- Backup-Monitoring (letzter `rsync`/`borg`/`restic`-Lauf).
- 30-Tage-Uptime-Prozent-Sensor.
- Multi-Interface-Netzwerkstatistik (aktuell nur ein aggregierter In/Out-Wert).
- Unattended-Upgrades-Status.
- Server-Konfiguration Export/Import (Backup der `servers_json` als YAML).

## Bekannter offener Bug (als Background-Task geflaggt, noch nicht gefixt)

`README.md` dokumentiert `sensor.<name>_disk_io_read`/`_disk_io_write`, tatsächliche Entity-ID ist
aber `sensor.<name>_disk_i_o_read`/`_disk_i_o_write` (HA-Slugify von "I/O" → "i_o", verifiziert via
`homeassistant.util.slugify`). Task-ID `task_e3eb3502` — bei Bedarf `dismiss_task` aufrufen falls
extern schon gefixt, sonst aufgreifen.

## Arbeitsweise, die sich bewährt hat

- Kleine, additive Python-Skripte zum Editieren von `strings.json`/`translations/*.json` statt
  manueller Edits — verhindert Reihenfolge-/Encoding-Fehler über 5 Dateien hinweg.
- Neue Bash-Collector-Felder: immer 4 Stellen anfassen — Funktion definieren, in den
  unconditional-Call-Block einhängen, in `prepare_numeric_json_values` einreihen, im finalen
  `printf`-Format-String UND der Argumentliste ergänzen. Danach `bash -n` + Stub-Test.
- Vor jedem Abschluss: `py_compile` + `ruff check` (ohne `.sh`) + `pytest -q` + Pycache-Cleanup.
