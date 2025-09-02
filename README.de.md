# VServer SSH Stats – Home Assistant Add-on

## Übersicht
Das **VServer SSH Stats** Add-on für Home Assistant ermöglicht die Überwachung entfernter Linux-Server (vServer, Raspberry Pi oder dedizierte Maschinen), ohne zusätzliche Agenten auf den Zielrechnern zu installieren.

Das Add-on verbindet sich per **SSH** (über IP-Adresse, Benutzername und Passwort oder SSH-Schlüssel) und sammelt Systemmetriken direkt aus `/proc`, `df` und anderen Standard-Linux-Schnittstellen.
Die Metriken werden anschließend über **MQTT Discovery** an Home Assistant veröffentlicht, sodass sie als native Sensoren erscheinen.

Dadurch ist es möglich, CPU-, Speicher-, Festplatten-, Laufzeit-, Netzwerkdurchsatz- und Temperaturinformationen in Echtzeit von all Ihren Servern in Home Assistant Dashboards anzuzeigen.

---

## Funktionen
- Keine Softwareinstallation auf dem Zielserver erforderlich (nur SSH-Zugriff).
- Unterstützt mehrere Server mit individueller Konfiguration.
- Konfiguration über die Home Assistant Oberfläche (Config Flow).
- Unterstützt Passwort- und SSH-Schlüssel-Authentifizierung.
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
  - Betriebssystem-Version
  - Installierte Pakete (Anzahl und Liste)
  - Docker-Installation und laufende Container
- Automatische **MQTT Discovery** für einfache Integration in Home Assistant.
- Konfigurierbares Aktualisierungsintervall (Standard: 30 Sekunden).
- Optionale leichtgewichtige Weboberfläche, die in der Home-Assistant-Seitenleiste angezeigt werden kann, jetzt mit einem Reiter für Docker-Container.

### Standalone-Nutzung ohne MQTT

Wenn du Statistiken ohne MQTT sammeln möchtest, führe `app/simple_collector.py` aus. Das Skript ermöglicht dir die Eingabe eines oder mehrerer Server (drücke Enter bei der Host-Eingabe, um abzuschließen). Für jeden Server fragt es Host, Benutzername und entweder ein Passwort oder den Pfad zu einem SSH-Schlüssel sowie optional den Port ab und gibt dann alle 30 Sekunden eine JSON-Zeile mit dem Servernamen und CPU-, Speicher-, Festplatten-, Netzwerk-, Laufzeit- und Temperaturwerten aus.

Optional kannst du deine Home Assistant Basis-URL und ein Long-Lived Access Token angeben. Wenn vorhanden, erstellt das Skript über die Home Assistant REST API Sensoren wie `sensor.<name>_cpu`, `sensor.<name>_mem` usw., damit die Werte ohne MQTT in der Oberfläche erscheinen.

Der Haupt-Collector (`app/collector.py`) unterstützt ebenfalls einen leichtgewichtigen Modus ohne MQTT: Führe ihn einfach ohne die Umgebungsvariable `MQTT_HOST` aus. In diesem Fall werden die gesammelten Statistiken in der Konsole protokolliert, anstatt an einen Broker gesendet zu werden.

---

## Installation

### Über HACS (Home Assistant Community Store)
1. Stelle sicher, dass [HACS](https://hacs.xyz) in Home Assistant installiert ist.
2. Füge in HACS `https://github.com/404GamerNotFound/vserver-ssh-stats` als benutzerdefiniertes Repository (Typ: Integration) hinzu.
3. Suche nach **VServer SSH Stats** und installiere die Integration.
4. Starte Home Assistant neu, um die neue Integration zu laden.

### Manuelle Add-on-Installation
1. Kopiere den Add-on-Ordner `addon/vserver_ssh_stats` in dein lokales Home Assistant Add-on-Repository (z. B. `/addons/vserver_ssh_stats`).

2. In Home Assistant:
   - Navigiere zu **Einstellungen → Add-ons → Add-on Store**.
   - Klicke auf das Drei-Punkte-Menü → **Repositories**.
   - Füge deinen lokalen Add-on-Repository-Pfad oder das Git-Repository hinzu, das dieses Add-on enthält.

3. Das Add-on **VServer SSH Stats** sollte nun in der Liste erscheinen. Klicke auf **Installieren**.

4. Konfiguriere das Add-on (siehe unten).

5. Starte das Add-on.

6. Nach kurzer Zeit erscheinen neue Entitäten (Sensoren) automatisch in Home Assistant über MQTT Discovery.

---

## Konfiguration

Die Konfiguration wird in `options.json` gespeichert (bearbeitbar über die Add-on-Oberfläche).

Beispiel:

```yaml
mqtt_host: homeassistant
mqtt_port: 1883
mqtt_user: mqttuser
mqtt_pass: mqttpassword
interval_seconds: 30
disabled_entities:
  - pkg_list
  - temp
servers:
  - name: "pi5"
    host: "192.168.1.10"
    username: "tony"
    password: "supersecret"
  - name: "vps1"
    host: "203.0.113.42"
    username: "root"
    key: "/config/ssh/id_rsa"
    port: 22
```

### Optionen
- **mqtt_host** – Hostname/IP deines MQTT-Brokers (normalerweise `homeassistant`).
- **mqtt_port** – Port des MQTT-Brokers (Standard: `1883`).
- **mqtt_user / mqtt_pass** – MQTT-Anmeldedaten.
- **interval_seconds** – Abfrageintervall in Sekunden (mindestens 5).
- **disabled_entities** – Liste von Sensor-Schlüsseln, die deaktiviert werden sollen (z. B. `cpu`, `mem`). Standard: alle aktiv.
- **servers** – Liste der zu überwachenden Server:
  - `name` – Anzeigename (wird als Präfix für Entitäten verwendet).
  - `host` – IP-Adresse oder Hostname des Servers.
  - `username` – SSH-Benutzername.
  - `password` – SSH-Passwort (optional, wenn `key` verwendet wird).
  - `key` – Pfad zu einer SSH-Schlüsseldatei (optional).
  - `port` – (Optional) SSH-Port (Standard `22`).

### Entitäten deaktivieren

Um bestimmte Sensoren nicht zu erstellen und zu veröffentlichen, füge deren Schlüssel zu `disabled_entities` hinzu. Beispiel: Um
Temperatur- und Paketlisten-Sensoren zu deaktivieren:

```yaml
disabled_entities:
  - temp
  - pkg_list
```

---

## Erstellte Entitäten

Für jeden Server sind folgende Entitäten verfügbar:

- `sensor.<name>_cpu` – CPU-Auslastung (%)
- `sensor.<name>_mem` – Speicherauslastung (%)
- `sensor.<name>_disk` – Festplattenauslastung (%)
- `sensor.<name>_net_in` – Netzwerkeingang (Bytes/s)
- `sensor.<name>_net_out` – Netzwerkausgang (Bytes/s)
- `sensor.<name>_uptime` – Laufzeit (Sekunden)
- `sensor.<name>_temp` – Temperatur (°C, falls verfügbar)
- `sensor.<name>_ram` – Gesamt-RAM (MB)
- `sensor.<name>_cores` – CPU-Kerne
- `sensor.<name>_load_1` – 1‑Minuten‑Last
- `sensor.<name>_load_5` – 5‑Minuten‑Last
- `sensor.<name>_load_15` – 15‑Minuten‑Last
- `sensor.<name>_cpu_freq` – CPU‑Frequenz (MHz)
- `sensor.<name>_os` – Betriebssystem-Version
- `sensor.<name>_pkg_count` – Anzahl installierter Pakete
- `sensor.<name>_pkg_list` – Installierte Pakete (erste 10)
- `sensor.<name>_docker` – 1, wenn Docker installiert ist, sonst 0
- `sensor.<name>_containers` – Laufende Docker-Container (kommagetrennte Liste)

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

## Sicherheitshinweise
- Es wird empfohlen, einen dedizierten, eingeschränkten Benutzer für das SSH-Monitoring zu erstellen (mit nur Lesezugriff auf `/proc` und `df`).
- SSH-Passwortauthentifizierung wird unterstützt, aber **SSH-Schlüssel-Authentifizierung** wird für den produktiven Einsatz dringend empfohlen.
- Der Netzwerkverkehr zwischen Home Assistant und deinen Servern ist unverschlüsselt, sofern du kein TLS für MQTT aktivierst.

---

## Anforderungen
- Home Assistant mit MQTT-Broker (eingebauter Mosquitto oder extern).
- SSH-Zugang zu den überwachten Servern.
- Linux-basierte Zielserver (beliebige Distribution mit `/proc` und `df`).

---

## Lizenz
Dieses Projekt ist unter der **MIT-Lizenz** lizenziert.

---

## Autor
**Tony Brüser**
Originalautor und Maintainer dieses Add-ons.
