# VServer SSH Stats – Complemento de Home Assistant

![Logo de VServer SSH Stats](images/logo/logo.png)

## Descripción general
El complemento **VServer SSH Stats** para Home Assistant te permite supervisar servidores Linux remotos (vServers, Raspberry Pi o máquinas dedicadas) sin instalar agentes adicionales en las máquinas objetivo.

El complemento se conecta mediante **SSH** (usando dirección IP, nombre de usuario y contraseña o clave SSH) y recopila métricas del sistema directamente de `/proc`, `df` y otras interfaces estándar de Linux.
Las métricas se publican en Home Assistant mediante **MQTT Discovery**, por lo que aparecen como sensores nativos.

Alternativamente, la integración de HACS puede consultar tus servidores directamente por SSH y crear los sensores sin necesidad de MQTT ni del add-on.

Esto permite obtener información en tiempo real sobre CPU, memoria, disco, tiempo de actividad, rendimiento de red y temperatura de todos tus servidores en los paneles de Home Assistant.

Además de la recopilación de estadísticas, el complemento incluye ahora un terminal web interactivo y un servicio de Home Assistant para ejecutar comandos ad hoc en tus servidores.

---

## Características
- No se requiere instalación de software en el servidor de destino (solo acceso SSH).
- Soporta múltiples servidores con configuración individual.
- Configurable a través de la interfaz de Home Assistant (config flow).
- Soporta autenticación por contraseña y por clave SSH.
- Terminal interactivo accesible desde la interfaz web del complemento.
- Servicios de Home Assistant y entidades de botón para ejecutar comandos remotos, actualizar paquetes y reiniciar.
- Detecta automáticamente hosts con SSH en la red local para una configuración rápida, manteniendo la posibilidad de configuración manual.
- Recopila:
  - Uso de CPU (%)
  - Uso de memoria (%)
  - RAM total (MB)
  - Uso de disco (% para `/`)
  - Rendimiento de red (bytes/s de entrada y salida)
  - Tiempo de actividad (segundos)
  - Temperatura (°C, si está disponible)
  - Núcleos de CPU
  - Carga promedio (1/5/15 min)
  - Frecuencia de CPU (MHz)
  - Versión del sistema operativo
  - Paquetes instalados (cantidad y lista)
  - Detección de Docker, contenedores en ejecución y uso por contenedor (CPU y memoria)
  - Estado de soporte VNC
  - Estado de servidor web HTTP/HTTPS
  - Estado de servicio SSH
- **MQTT Discovery** automática para una fácil integración con Home Assistant.
- Intervalo de actualización configurable (por defecto: 30 segundos).
- Interfaz web ligera opcional que puede mostrarse en la barra lateral de Home Assistant, ahora con una pestaña de contenedores Docker.
- Servicios para obtener la IP local del servidor, el tiempo de actividad, listar conexiones SSH activas, ejecutar comandos, actualizar paquetes y reiniciar el host.

### Uso independiente sin MQTT

Si deseas recopilar estadísticas sin MQTT, ejecuta `app/simple_collector.py`. El script permite introducir uno o varios servidores (pulsa Enter en el campo de host para finalizar). Para cada servidor solicita host, nombre de usuario y una contraseña o la ruta a una clave SSH más un puerto opcional, y luego imprime cada 30 segundos una línea JSON con el nombre del servidor y los valores de CPU, memoria, disco, red, tiempo de actividad y temperatura.

Opcionalmente puedes introducir la URL base de Home Assistant y un token de acceso de larga duración. Si se proporcionan, el script creará sensores como `sensor.<name>_cpu`, `sensor.<name>_mem`, etc. mediante la API REST de Home Assistant para que los valores aparezcan en la interfaz sin MQTT.

El colector principal (`app/collector.py`) también admite un modo ligero sin MQTT: simplemente ejecútalo sin la variable de entorno `MQTT_HOST`. En ese caso, las estadísticas recopiladas se registran en la consola en lugar de publicarse en un broker.

---

## Instalación

### A través de HACS (Home Assistant Community Store)
1. Asegúrate de que [HACS](https://hacs.xyz) esté instalado en Home Assistant.
2. En HACS, añade `https://github.com/404GamerNotFound/vserver-ssh-stats` como repositorio personalizado (tipo: integración).
3. Busca **VServer SSH Stats** e instala la integración.
4. Reinicia Home Assistant para cargar la nueva integración.

Ejemplo de HACS:

![Ejemplo de HACS](images/screeshots/Screenshot5.png)

### Instalación manual del complemento
1. Copia la carpeta del complemento `addon/vserver_ssh_stats` en tu repositorio local de complementos de Home Assistant (por ejemplo, `/addons/vserver_ssh_stats`).

2. En Home Assistant:
   - Ve a **Ajustes → Add-ons → Add-on Store**.
   - Haz clic en el menú de tres puntos → **Repositories**.
   - Añade la ruta a tu repositorio local de complementos o el repositorio Git que contiene este complemento.

3. El complemento **VServer SSH Stats** debería aparecer ahora en la lista. Haz clic en **Install**.

4. Configura el complemento (ver abajo).

5. Inicia el complemento.

6. Tras un breve período, nuevas entidades (sensores) aparecerán automáticamente en Home Assistant mediante MQTT Discovery.

---

## Configuración

La configuración se almacena en `options.json` (editable mediante la interfaz del complemento).

Ejemplo:

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

### Opciones
- **mqtt_host** – Nombre de host/IP de tu broker MQTT (normalmente `homeassistant`).
- **mqtt_port** – Puerto del broker MQTT (predeterminado: `1883`).
- **mqtt_user / mqtt_pass** – Credenciales MQTT.
- **interval_seconds** – Intervalo de sondeo en segundos (mínimo 5).
- **disabled_entities** – Lista de claves de sensores que se desactivarán (por ejemplo, `cpu`, `mem`). Todos activados por defecto.
- **servers** – Lista de servidores a supervisar:
  - `name` – Nombre amigable (usado como prefijo de entidad).
  - `host` – Dirección IP o nombre de host del servidor.
  - `username` – Nombre de usuario SSH.
  - `password` – Contraseña SSH (opcional si se usa `key`).
  - `key` – Ruta a un archivo de clave privada SSH (opcional).
  - `port` – (Opcional) Puerto SSH (por defecto `22`).

### Desactivar entidades

Añade las claves de sensores no deseados en `disabled_entities` para evitar su creación y publicación. Por ejemplo, para desactivar los sensores de temperatura y lista de paquetes:

```yaml
disabled_entities:
  - temp
  - pkg_list
```

---

## Entidades creadas

Para cada servidor estarán disponibles las siguientes entidades:

- `sensor.<name>_cpu` – Uso de CPU (%)
- `sensor.<name>_mem` – Uso de memoria (%)
- `sensor.<name>_disk` – Uso de disco (%)
- `sensor.<name>_net_in` – Tráfico de entrada (bytes/s)
- `sensor.<name>_net_out` – Tráfico de salida (bytes/s)
- `sensor.<name>_uptime` – Tiempo de actividad (segundos)
- `sensor.<name>_temp` – Temperatura (°C, si está disponible)
- `sensor.<name>_ram` – RAM total (MB)
- `sensor.<name>_cores` – Núcleos de CPU
- `sensor.<name>_load_1` – Carga promedio 1 min
- `sensor.<name>_load_5` – Carga promedio 5 min
- `sensor.<name>_load_15` – Carga promedio 15 min
- `sensor.<name>_cpu_freq` – Frecuencia de CPU (MHz)
- `sensor.<name>_os` – Versión del sistema operativo
- `sensor.<name>_pkg_count` – Cantidad de actualizaciones pendientes
- `sensor.<name>_pkg_list` – Actualizaciones pendientes (primeras 10)
- `sensor.<name>_docker` – 1 si Docker está instalado, 0 en caso contrario
- `sensor.<name>_containers` – Contenedores Docker en ejecución (lista separada por comas)
- `sensor.<name>_vnc` – "sí" si se detecta un servidor VNC
- `sensor.<name>_web` – "sí" si escucha un servicio HTTP o HTTPS
- `sensor.<name>_ssh` – "sí" si el servicio SSH está activo
- Para cada contenedor en ejecución: `sensor.<name>_container_<container>_cpu` (uso de CPU %) y `sensor.<name>_container_<container>_mem` (uso de memoria %)

---

## Ejemplo de panel Lovelace

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

## Notas de seguridad
- Se recomienda crear un usuario dedicado y restringido para la supervisión por SSH (con acceso de solo lectura a `/proc` y `df`).
- Se admite autenticación por contraseña, pero se recomienda encarecidamente la **autenticación por clave SSH** para uso en producción.
- El tráfico de red entre Home Assistant y tus servidores no está cifrado a menos que habilites TLS para MQTT.

---

## Requisitos
- Home Assistant con broker MQTT (Mosquitto integrado o externo).
- Acceso SSH a los servidores monitorizados.
- Servidores de destino basados en Linux (cualquier distribución con `/proc` y `df`).

---

## Licencia
Este proyecto está licenciado bajo la **Licencia MIT**.

---

## Autor
**Tony Brüser**
Autor original y mantenedor de este complemento.
