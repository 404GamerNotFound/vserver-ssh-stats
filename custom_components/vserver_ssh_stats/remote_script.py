REMOTE_SCRIPT = r'''
set -e
export LC_ALL=C
export LANG=C

# Power (best-effort via powercap)
power_energy_file=""
for path in /sys/class/powercap/*/energy_uj; do
  if [ -r "$path" ]; then
    power_energy_file=$path
    break
  fi
done
power_energy_before=""
power_energy_after=""
power_energy_range=""
if [ -n "$power_energy_file" ]; then
  power_energy_before=$(cat "$power_energy_file" 2>/dev/null || echo "")
  dir=$(dirname "$power_energy_file")
  if [ -r "$dir/max_energy_range_uj" ]; then
    power_energy_range=$(cat "$dir/max_energy_range_uj" 2>/dev/null || echo "")
  elif [ -r "$dir/energy_range_uj" ]; then
    power_energy_range=$(cat "$dir/energy_range_uj" 2>/dev/null || echo "")
  fi
fi

# CPU %
read cpu user nice system idle iowait irq softirq steal guest < /proc/stat
prev_total=$((user+nice+system+idle+iowait+irq+softirq+steal))
prev_idle=$((idle+iowait))
sleep 1
if [ -n "$power_energy_file" ]; then
  power_energy_after=$(cat "$power_energy_file" 2>/dev/null || echo "")
fi
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
# RAM total MB
ram=$(( (mem_total + 512) / 1024 ))

# DISK % (Root)
disk=$(df -P / | awk 'NR==2 {print $5}' | tr -d '%')

# DISK capacity overview (sum of non-virtual filesystems) and per-disk stats
disk_total_bytes=0
disk_stats="[]"
disk_lines=$(df -PB1 --output=source,target,size,avail -x tmpfs -x devtmpfs -x squashfs -x overlay 2>/dev/null | tail -n +2 | awk '{gsub(/\\040/," ",$2); printf "%s\t%s\t%s\t%s\n", $1, $2, $3, $4}')
if [ -n "$disk_lines" ]; then
  tab=$(printf '\t')
  oldifs=$IFS
  IFS=$tab
  disk_entries=""
  while read -r source target size avail; do
    [ -z "$source" ] && continue
    if [ -z "$size" ]; then continue; fi
    disk_total_bytes=$((disk_total_bytes + size))
    source_json=$(printf '%s' "$source" | sed 's/"/\\\\"/g')
    target_json=$(printf '%s' "$target" | sed 's/"/\\\\"/g')
    disk_entries="$disk_entries{\"name\":\"$source_json\",\"mount\":\"$target_json\",\"total\":$size,\"free\":$avail},"
  done <<EOF
$disk_lines
EOF
  IFS=$oldifs
  if [ -n "$disk_entries" ]; then
    disk_stats="[${disk_entries%,}]"
  fi
fi
disk_stats_json=$disk_stats
disk_total_bytes_json=$disk_total_bytes

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

# NET (Summen Bytes RX/TX über alle nicht-lo Interfaces)
rx=$(awk -F'[: ]+' '/:/{if($1!="lo"){rx+=$3; tx+=$11}} END{print rx+0}' /proc/net/dev)
tx=$(awk -F'[: ]+' '/:/{if($1!="lo"){rx+=$3; tx+=$11}} END{print tx+0}' /proc/net/dev)

if [ -n "$temp" ]; then temp_json=$temp; else temp_json=null; fi

power_w_json=null
energy_counter_json=null
energy_range_json=null
if [ -n "$power_energy_before" ] && [ -n "$power_energy_after" ]; then
  adjusted_after=$power_energy_after
  if [ "$power_energy_after" -lt "$power_energy_before" ] && [ -n "$power_energy_range" ]; then
    adjusted_after=$((power_energy_after + power_energy_range))
  fi
  delta_energy=$((adjusted_after - power_energy_before))
  if [ "$delta_energy" -lt 0 ]; then
    delta_energy=0
  fi
  power_w=$(awk -v d="$delta_energy" 'BEGIN{printf "%.2f", d/1000000}')
  power_w_json=$power_w
  energy_counter_json=$power_energy_after
  if [ -n "$power_energy_range" ]; then
    energy_range_json=$power_energy_range
  fi
fi
printf '{"cpu":%s,"mem":%s,"disk":%s,"disk_capacity_total":%s,"disk_stats":%s,"uptime":%s,"temp":%s,"rx":%s,"tx":%s,"ram":%s,"cores":%s,"load_1":%s,"load_5":%s,"load_15":%s,"cpu_freq":%s,"os":"%s","pkg_count":%s,"pkg_list":"%s","docker":%s,"containers":"%s","container_stats":%s,"vnc":"%s","web":"%s","ssh":"%s","power_w":%s,"energy_uj":%s,"energy_range_uj":%s}\n' \
  "$cpu" "$mem" "$disk" "$disk_total_bytes_json" "$disk_stats_json" "$uptime" "$temp_json" "$rx" "$tx" "$ram" "$cores" "$load_1" "$load_5" "$load_15" "$cpu_freq_json" "$os_json" "$pkg_count" "$pkg_list_json" "$docker" "$containers_json" "$container_stats_json" "$vnc" "$web" "$ssh_enabled" "$power_w_json" "$energy_counter_json" "$energy_range_json"
'''
