# VServer SSH Stats – Module complémentaire pour Home Assistant

## Vue d'ensemble
Le module complémentaire **VServer SSH Stats** pour Home Assistant permet de surveiller des serveurs Linux distants (vServers, Raspberry Pi ou machines dédiées) sans installer de logiciels supplémentaires sur les machines cibles.

Le module se connecte via **SSH** (en utilisant l'adresse IP, le nom d'utilisateur et le mot de passe ou une clé SSH) et collecte les métriques système directement depuis `/proc`, `df` et d'autres interfaces Linux standard.
Les métriques sont ensuite publiées vers Home Assistant via **MQTT Discovery**, de sorte qu'elles apparaissent comme des capteurs natifs.

Cela permet d'afficher en temps réel les informations de CPU, mémoire, disque, temps de fonctionnement, débit réseau et température de tous vos serveurs dans les tableaux de bord Home Assistant.

---

## Fonctionnalités
- Aucun logiciel à installer sur le serveur cible (simple accès SSH).
- Prise en charge de plusieurs serveurs avec configuration individuelle.
- Configurable via l'interface Home Assistant (config flow).
- Prise en charge de l'authentification par mot de passe et par clé SSH.
- Collecte :
  - Utilisation du CPU (%)
  - Utilisation de la mémoire (%)
  - RAM totale (MB)
  - Utilisation du disque (% pour `/`)
  - Débit réseau (octets/s, entrant et sortant)
  - Temps de fonctionnement (secondes)
  - Température (°C, si disponible)
  - Cœurs CPU
  - Charge moyenne (1/5/15 min)
  - Fréquence CPU (MHz)
  - Version du système d'exploitation
  - Paquets installés (nombre et liste)
  - Détection de Docker et conteneurs en cours d'exécution
- **MQTT Discovery** automatique pour une intégration facile avec Home Assistant.
- Intervalle de mise à jour configurable (par défaut : 30 secondes).
- Interface web légère optionnelle pouvant être affichée dans la barre latérale de Home Assistant, maintenant avec un onglet pour les conteneurs Docker.

### Utilisation autonome sans MQTT

Si vous souhaitez recueillir des statistiques sans MQTT, exécutez `app/simple_collector.py`. Le script permet d'entrer un ou plusieurs serveurs (appuyez sur Entrée au prompt de l'hôte pour terminer). Pour chaque serveur, il demande l'hôte, le nom d'utilisateur et soit un mot de passe soit le chemin vers une clé SSH, plus un port optionnel, puis affiche toutes les 30 secondes une ligne JSON incluant le nom du serveur avec les valeurs de CPU, mémoire, disque, réseau, temps de fonctionnement et température.

Vous pouvez facultativement entrer l'URL de base de Home Assistant et un jeton d'accès longue durée. Lorsque ces informations sont fournies, le script crée via l'API REST de Home Assistant des capteurs comme `sensor.<name>_cpu`, `sensor.<name>_mem`, etc., afin que les valeurs apparaissent dans l'interface sans MQTT.

Le collecteur principal (`app/collector.py`) prend également en charge un mode léger sans MQTT : exécutez-le simplement sans la variable d'environnement `MQTT_HOST`. Dans ce cas, les statistiques collectées sont enregistrées sur la console au lieu d'être publiées sur un broker.

---

## Installation

### Via HACS (Home Assistant Community Store)
1. Assurez-vous que [HACS](https://hacs.xyz) est installé dans Home Assistant.
2. Dans HACS, ajoutez `https://github.com/404GamerNotFound/vserver-ssh-stats` comme dépôt personnalisé (type : integration).
3. Recherchez **VServer SSH Stats** et installez l'intégration.
4. Redémarrez Home Assistant pour charger la nouvelle intégration.

### Installation manuelle du module complémentaire
1. Copiez le dossier du module `vserver_ssh_stats` dans votre dépôt local de modules complémentaires Home Assistant (par exemple `/addons/vserver_ssh_stats`).

2. Dans Home Assistant :
   - Accédez à **Paramètres → Add-ons → Add-on Store**.
   - Cliquez sur le menu à trois points → **Repositories**.
   - Ajoutez le chemin de votre dépôt local de modules ou le dépôt Git contenant ce module.

3. Le module **VServer SSH Stats** devrait maintenant apparaître dans la liste. Cliquez sur **Install**.

4. Configurez le module (voir ci-dessous).

5. Démarrez le module.

6. Après un court instant, de nouvelles entités (capteurs) apparaîtront automatiquement dans Home Assistant via MQTT Discovery.

---

## Configuration

La configuration est stockée dans `options.json` (modifiable via l'interface du module).

Exemple :

```yaml
mqtt_host: homeassistant
mqtt_port: 1883
mqtt_user: mqttuser
mqtt_pass: mqttpassword
interval_seconds: 30
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

### Options
- **mqtt_host** – Nom d'hôte/IP de votre broker MQTT (généralement `homeassistant`).
- **mqtt_port** – Port du broker MQTT (par défaut : `1883`).
- **mqtt_user / mqtt_pass** – Identifiants MQTT.
- **interval_seconds** – Intervalle d'interrogation en secondes (minimum 5).
- **servers** – Liste des serveurs à surveiller :
  - `name` – Nom convivial (utilisé comme préfixe d'entité).
  - `host` – Adresse IP ou nom d'hôte du serveur.
  - `username` – Nom d'utilisateur SSH.
  - `password` – Mot de passe SSH (facultatif si `key` est utilisé).
  - `key` – Chemin vers un fichier de clé privée SSH (facultatif).
  - `port` – (Facultatif) Port SSH (par défaut `22`).

---

## Entités créées

Pour chaque serveur, les entités suivantes seront disponibles :

- `sensor.<name>_cpu` – Utilisation du CPU (%)
- `sensor.<name>_mem` – Utilisation de la mémoire (%)
- `sensor.<name>_disk` – Utilisation du disque (%)
- `sensor.<name>_net_in` – Trafic entrant (octets/s)
- `sensor.<name>_net_out` – Trafic sortant (octets/s)
- `sensor.<name>_uptime` – Temps de fonctionnement (secondes)
- `sensor.<name>_temp` – Température (°C, si disponible)
- `sensor.<name>_ram` – RAM totale (MB)
- `sensor.<name>_cores` – Cœurs CPU
- `sensor.<name>_load_1` – Charge moyenne 1 min
- `sensor.<name>_load_5` – Charge moyenne 5 min
- `sensor.<name>_load_15` – Charge moyenne 15 min
- `sensor.<name>_cpu_freq` – Fréquence CPU (MHz)
- `sensor.<name>_os` – Version du système d'exploitation
- `sensor.<name>_pkg_count` – Nombre de mises à jour en attente
- `sensor.<name>_pkg_list` – Mises à jour en attente (10 premières)
- `sensor.<name>_docker` – 1 si Docker est installé, 0 sinon
- `sensor.<name>_containers` – Conteneurs Docker en cours d'exécution (liste séparée par des virgules)

---

## Exemple de tableau de bord Lovelace

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

## Notes de sécurité
- Il est recommandé de créer un utilisateur dédié et restreint pour la surveillance SSH (avec un accès en lecture seule à `/proc` et `df`).
- L'authentification par mot de passe est prise en charge, mais l'**authentification par clé SSH** est fortement recommandée pour un usage en production.
- Le trafic réseau entre Home Assistant et vos serveurs n'est pas chiffré à moins d'activer TLS pour MQTT.

---

## Exigences
- Home Assistant avec broker MQTT (Mosquitto intégré ou externe).
- Accès SSH aux serveurs surveillés.
- Serveurs cibles basés sur Linux (toute distribution avec `/proc` et `df`).

---

## Licence
Ce projet est sous licence **MIT**.

---

## Auteur
**Tony Brüser**
Auteur original et mainteneur de ce module.
