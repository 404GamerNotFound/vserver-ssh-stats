REMOTE_SCRIPT = r'''
set -e
export LC_ALL=C
export LANG=C
# CPU %
read cpu user nice system idle iowait irq softirq steal guest < /proc/stat
prev_total=$((user+nice+system+idle+iowait+irq+softirq+steal))
prev_idle=$((idle+iowait))
sleep 1
read cpu user nice system idle iowait irq softirq steal guest < /proc/stat
total=$((user+nice+system+idle+iowait+irq+softirq+steal))
idle_all=$((idle+iowait))
d_total=$((total-prev_total))
d_idle=$((idle_all-prev_idle))
cpu=$(( (100*(d_total - d_idle) + d_total/2) / d_total ))

# MEM %
mem_total=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
mem_avail=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)
if [ -z "$mem_avail" ]; then mem_avail=$(awk '/MemFree/ {print $2}' /proc/meminfo); fi
mem=$(( (100*(mem_total - mem_avail) + mem_total/2) / mem_total ))
# Swap %
swap_total=$(awk '/SwapTotal/ {print $2}' /proc/meminfo)
swap_free=$(awk '/SwapFree/ {print $2}' /proc/meminfo)
if [ -n "$swap_total" ] && [ "$swap_total" -gt 0 ]; then
  swap=$(( (100*(swap_total - swap_free) + swap_total/2) / swap_total ))
else
  swap=0
fi
# RAM total MB
ram=$(( (mem_total + 512) / 1024 ))

# DISK % (Root)
disk=$(df -P / | awk 'NR==2 {print $5}' | tr -d '%')

# UPTIME (Sekunden)
uptime=$(awk '{print int($1)}' /proc/uptime)

# CPU cores
cores=$(nproc)

# Load average (1/5/15 Minuten)
read load_1 load_5 load_15 _ < /proc/loadavg

# Aktuelle CPU-Frequenz in MHz (best-effort)
cpu_freq=""
if [ -f /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq ]; then
  cpu_freq=$(awk '{printf "%.0f", $1/1000}' /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null)
fi
if [ -n "$cpu_freq" ]; then cpu_freq_json=$cpu_freq; else cpu_freq_json=null; fi

# OS (best-effort)
os=$( (grep '^PRETTY_NAME' /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"') || uname -sr )
os_json=$(printf '%s' "$os" | sed 's/"/\\"/g')

# Pending package updates (count + list up to 10)
pkg_count=0
pkg_list=""
if command -v apt-get >/dev/null 2>&1; then
  updates=$(apt-get -s upgrade 2>/dev/null | awk '/^Inst /{print $2}')
  pkg_count=$(echo "$updates" | wc -l)
  pkg_list=$(echo "$updates" | head -n 10 | tr '\n' ',' | sed 's/,$//')
elif command -v dnf >/dev/null 2>&1; then
  updates=$(dnf -q check-update --refresh 2>/dev/null | awk '/^[[:alnum:].-]+[[:space:]]/ {print $1}')
  pkg_count=$(echo "$updates" | wc -l)
  pkg_list=$(echo "$updates" | head -n 10 | tr '\n' ',' | sed 's/,$//')
elif command -v yum >/dev/null 2>&1; then
  updates=$(yum -q check-update 2>/dev/null | awk '/^[[:alnum:].-]+[[:space:]]/ {print $1}')
  pkg_count=$(echo "$updates" | wc -l)
  pkg_list=$(echo "$updates" | head -n 10 | tr '\n' ',' | sed 's/,$//')
fi
pkg_list_json=$(printf '%s' "$pkg_list" | sed 's/"/\\"/g')

# Docker (installed and running containers)
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker=1
  containers=$(docker ps --format '{{.Names}}' 2>/dev/null | tr '\n' ',' | sed 's/,$//')
  stats=$(docker stats --no-stream --format '{{.Name}}:{{.CPUPerc}}:{{.MemPerc}}' 2>/dev/null | sed 's/%//g' | awk -F: '{cpu=$2+0; mem=$3+0; printf "{\"name\":\"%s\",\"cpu\":%.2f,\"mem\":%.2f},", $1, cpu, mem}')
  if [ -n "$stats" ]; then
    container_stats="[${stats%,}]"
  else
    container_stats="[]"
  fi
else
  docker=0
  containers=""
  container_stats="[]"
fi
containers_json=$(printf '%s' "$containers" | sed 's/"/\\"/g')
container_stats_json=$container_stats

# Service checks (SSH, web, VNC)
sock_cmd=""
if command -v ss >/dev/null 2>&1; then
  sock_cmd="ss -tuln"
elif command -v netstat >/dev/null 2>&1; then
  sock_cmd="netstat -tuln"
fi

if [ -n "$sock_cmd" ] && $sock_cmd 2>/dev/null | grep -E ':22\\s' >/dev/null; then
  ssh_enabled="yes"
else
  ssh_enabled="no"
fi

if [ -n "$sock_cmd" ] && $sock_cmd 2>/dev/null | grep -E ':(80|443)\\s' >/dev/null; then
  web="yes"
else
  web="no"
fi

if [ -n "$sock_cmd" ] && $sock_cmd 2>/dev/null | grep -E ':5900\\s' >/dev/null; then
  vnc="yes"
elif command -v vncserver >/dev/null 2>&1 || command -v x11vnc >/dev/null 2>&1; then
  vnc="yes"
else
  vnc="no"
fi

# TEMP (°C, best-effort)
temp=""
if [ -f /sys/class/thermal/thermal_zone0/temp ]; then
  t=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo "")
  if [ -n "$t" ]; then temp=$(awk -v v="$t" 'BEGIN{printf "%.1f", (v>=1000?v/1000:v)}'); fi
fi

# Hardware sensors (best-effort via lm-sensors)
sensors_json="{}"
if command -v sensors >/dev/null 2>&1; then
  sj=$(sensors -j 2>/dev/null | tr -d '\n')
  if [ -n "$sj" ]; then sensors_json=$sj; fi
fi

# NET (Summen Bytes RX/TX über alle nicht-lo Interfaces)
rx=$(awk -F'[: ]+' '/:/{if($1!="lo"){rx+=$3; tx+=$11}} END{print rx+0}' /proc/net/dev)
tx=$(awk -F'[: ]+' '/:/{if($1!="lo"){rx+=$3; tx+=$11}} END{print tx+0}' /proc/net/dev)

# Lokale IP-Adresse (erste nicht-lo IPv4)
local_ip=$(hostname -I 2>/dev/null | awk '{print $1}')
if [ -z "$local_ip" ]; then
  local_ip=$(ip -4 addr show scope global | awk '/inet /{print $2}' | cut -d/ -f1 | head -n1)
fi
local_ip_json=$(printf '%s' "$local_ip" | sed 's/"/\\"/g')

if [ -n "$temp" ]; then temp_json=$temp; else temp_json=null; fi
printf '{"cpu":%s,"mem":%s,"swap":%s,"disk":%s,"uptime":%s,"temp":%s,"rx":%s,"tx":%s,"ram":%s,"cores":%s,"load_1":%s,"load_5":%s,"load_15":%s,"cpu_freq":%s,"os":"%s","pkg_count":%s,"pkg_list":"%s","docker":%s,"containers":"%s","container_stats":%s,"vnc":"%s","web":"%s","ssh":"%s","local_ip":"%s","sensors":%s}\n' \
  "$cpu" "$mem" "$swap" "$disk" "$uptime" "$temp_json" "$rx" "$tx" "$ram" "$cores" "$load_1" "$load_5" "$load_15" "$cpu_freq_json" "$os_json" "$pkg_count" "$pkg_list_json" "$docker" "$containers_json" "$container_stats_json" "$vnc" "$web" "$ssh_enabled" "$local_ip_json" "$sensors_json"
'''
