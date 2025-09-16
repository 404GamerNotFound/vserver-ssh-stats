# VServer SSH Stats – Integración de Home Assistant

![Logo de VServer SSH Stats](images/logo/logo.png)

## Descripción general
La **integración VServer SSH Stats** para Home Assistant te permite supervisar servidores Linux remotos (vServers, Raspberry Pi o máquinas dedicadas) sin instalar agentes adicionales en las máquinas objetivo.

La integración se conecta mediante **SSH** (usando dirección IP, nombre de usuario y contraseña o clave SSH) y recopila métricas del sistema directamente de `/proc`, `df` y otras interfaces estándar de Linux. Las métricas aparecen como sensores nativos en Home Assistant.

Esto permite obtener información en tiempo real sobre CPU, memoria, disco, tiempo de actividad, rendimiento de red y temperatura de todos tus servidores en los paneles de Home Assistant.

La integración también proporciona servicios de Home Assistant para ejecutar comandos ad hoc en tus servidores.

---

## Características
- No se requiere instalación de software en el servidor de destino (solo acceso SSH).
- Soporta múltiples servidores con configuración individual.
- Configurable a través de la interfaz de Home Assistant (config flow).
- Soporta autenticación por contraseña y por clave SSH.
- Servicios de Home Assistant y entidades de botón para ejecutar comandos remotos, actualizar paquetes y reiniciar.
- Detecta automáticamente hosts con SSH en la red local para una configuración rápida, manteniendo la posibilidad de configuración manual. Los servidores compatibles que se anuncian mediante Zeroconf también aparecen en la sección **Descubierto** de Home Assistant.
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
- Intervalo de actualización configurable (por defecto: 30 segundos).
- Servicios para obtener la IP local del servidor, el tiempo de actividad, listar conexiones SSH activas, ejecutar comandos, actualizar paquetes y reiniciar el host.

---

## Instalación

### A través de HACS (Home Assistant Community Store)
1. Asegúrate de que [HACS](https://hacs.xyz) esté instalado en Home Assistant.
2. En HACS, añade `https://github.com/404GamerNotFound/vserver-ssh-stats` como repositorio personalizado (tipo: integración).
3. Busca **VServer SSH Stats** e instala la integración.
4. Reinicia Home Assistant para cargar la nueva integración.

Ejemplo de HACS:

![Ejemplo de HACS](images/screeshots/Screenshot5.png)

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
- Las acciones remotas como las actualizaciones de paquetes y los reinicios usan `sudo`. Asegúrate de que la cuenta remota pueda ejecutar `apt-get`, `dnf`, `yum` y `reboot` sin solicitar contraseña (por ejemplo, añadiendo reglas explícitas en `/etc/sudoers`). Documenta o refuerza esos permisos en cada servidor antes de habilitar los botones/servicios.

---

## Gestión de lanzamientos
- Versión estable actual: **v1.2.8** (coincide con `manifest.json`).
- Crea una etiqueta Git (por ejemplo, `git tag v1.2.8`) y una versión en GitHub para cada lanzamiento a fin de que HACS pueda seguir las actualizaciones correctamente.
- Utiliza el script existente `scripts/bump_version.py` para incrementar la versión de la integración al preparar una nueva publicación.
- Registra los cambios relevantes en [`CHANGELOG.md`](CHANGELOG.md) junto con cada versión.

---

## Requisitos
- Home Assistant.
- Acceso SSH a los servidores monitorizados.
- Servidores de destino basados en Linux (cualquier distribución con `/proc` y `df`).

---

## Licencia
Este proyecto está licenciado bajo la **Licencia MIT**.

---

## Autor
**Tony Brüser**
Autor original y mantenedor de esta integración.
