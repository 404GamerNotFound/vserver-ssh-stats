REMOTE_SCRIPT = r'''
set -e
export LC_ALL=C
export LANG=C

trim_comma() {
  # Remove trailing comma for aggregated strings
  printf '%s' "$1" | sed 's/,$//'
}

json_escape() {
  printf '%s' "$1" | sed 's/"/\\"/g'
}

read_power_metrics() {
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
}

read_cpu_stats() {
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
}

read_mem_stats() {
  mem_total=""
  mem_avail=""
  swap_total=""
  swap_free=""
  while IFS=: read -r key value; do
    case "$key" in
      MemTotal) mem_total=${value//[^0-9]/} ;;
      MemAvailable) mem_avail=${value//[^0-9]/} ;;
      MemFree) [ -z "$mem_avail" ] && mem_avail=${value//[^0-9]/} ;;
      SwapTotal) swap_total=${value//[^0-9]/} ;;
      SwapFree) swap_free=${value//[^0-9]/} ;;
    esac
  done < /proc/meminfo
  mem=$(( (100*(mem_total - mem_avail) + mem_total/2) / mem_total ))
  ram=$(( (mem_total + 512) / 1024 ))
  swap_usage_json=null
  swap_total_json=null
  if [ -n "$swap_total" ] && [ "$swap_total" -gt 0 ]; then
    swap_used=$((swap_total - swap_free))
    swap_usage=$(( (100*swap_used + swap_total/2) / swap_total ))
    swap_usage_json=$swap_usage
    swap_total_json=$((swap_total * 1024))
  fi
}

read_disk_stats() {
  disk_total_bytes=0
  disk_stats="[]"
  disk_lines=""
  if command -v df >/dev/null 2>&1; then
    set +e
    disk_lines=$(df -PB1 --output=source,target,size,avail -x tmpfs -x devtmpfs -x squashfs -x overlay 2>/dev/null | tail -n +2 |
awk '{gsub(/\\040/," ",$2); printf "%s\t%s\t%s\t%s\n", $1, $2, $3, $4}')
    disk_status=$?
    set -e
    if [ $disk_status -ne 0 ] || [ -z "$disk_lines" ]; then
      set +e
      disk_lines=$(df -P 2>/dev/null | tail -n +2 | awk '{if($1 !~ "^/") next; gsub(/\\040/," ",$6); size=$2*1024; avail=$4*1024;
printf "%s\t%s\t%.0f\t%.0f\n", $1, $6, size, avail}')
      disk_status=$?
      set -e
      if [ $disk_status -ne 0 ]; then
        disk_lines=""
      fi
    fi
  fi

  if [ -n "$disk_lines" ]; then
    tab=$(printf '\t')
    oldifs=$IFS
    IFS=$tab
    disk_entries=""
    while read -r source target size avail; do
      [ -z "$source" ] && continue
      [ -z "$size" ] && continue
      disk_total_bytes=$((disk_total_bytes + size))
      source_json=$(json_escape "$source")
      target_json=$(json_escape "$target")
      disk_entries="$disk_entries{\"name\":\"$source_json\",\"mount\":\"$target_json\",\"total\":$size,\"free\":$avail},"
    done <<'DISKLINES'
$disk_lines
DISKLINES
    IFS=$oldifs
    if [ -n "$disk_entries" ]; then
      disk_stats="[${disk_entries%,}]"
    fi
  fi
  disk_stats_json=$disk_stats
  disk_total_bytes_json=$disk_total_bytes
}

read_load_and_freq() {
  read load_1 load_5 load_15 _ < /proc/loadavg
  cpu_freq=""
  if [ -f /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq ]; then
    cpu_freq=$(awk '{printf "%.0f", $1/1000}' /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null)
  elif command -v lscpu >/dev/null 2>&1; then
    cpu_freq=$(lscpu 2>/dev/null | awk -F: '/CPU max MHz/ {gsub(/ /,"",$2); print int($2+0)}')
  fi
  if [ -n "$cpu_freq" ]; then cpu_freq_json=$cpu_freq; else cpu_freq_json=null; fi
}

read_os_info() {
  os=$( (grep '^PRETTY_NAME' /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"') || uname -sr )
  os_json=$(json_escape "$os")
}

collect_pkg_updates() {
  pkg_count=0
  pkg_list=""
  if command -v apt-get >/dev/null 2>&1; then
    updates=$(apt-get -s upgrade 2>/dev/null | awk '/^Inst /{print $2}')
  elif command -v dnf >/dev/null 2>&1; then
    updates=$(dnf -q check-update --refresh 2>/dev/null | awk '/^[[:alnum:].-]+[[:space:]]/ {print $1}')
  elif command -v yum >/dev/null 2>&1; then
    updates=$(yum -q check-update 2>/dev/null | awk '/^[[:alnum:].-]+[[:space:]]/ {print $1}')
  elif command -v pacman >/dev/null 2>&1; then
    updates=$(pacman -Qu 2>/dev/null | awk '{print $1}')
  elif command -v zypper >/dev/null 2>&1; then
    updates=$(zypper --quiet lu 2>/dev/null | awk '/^[[:alnum:]].*\|/{print $3}')
  elif command -v apk >/dev/null 2>&1; then
    updates=$(apk version -l '<' 2>/dev/null | awk '{print $1}')
  else
    updates=""
  fi

  if [ -n "$updates" ]; then
    pkg_count=$(echo "$updates" | grep -c . || true)
    pkg_list=$(trim_comma "$(echo "$updates" | head -n 10 | tr '\n' ',' )")
  fi
  pkg_list_json=$(json_escape "$pkg_list")
}

read_docker_stats() {
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    docker=1
    containers=$(docker ps --format '{{.Names}}' 2>/dev/null | tr '\n' ',' )
    containers=$(trim_comma "$containers")
    stats=$(docker stats --no-stream --format '{{.Name}}:{{.CPUPerc}}:{{.MemPerc}}' 2>/dev/null | sed 's/%//g' |
awk -F: '{cpu=$2+0; mem=$3+0; printf "{\"name\":\"%s\",\"cpu\":%.2f,\"mem\":%.2f},", $1, cpu, mem}')
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
  containers_json=$(json_escape "$containers")
  container_stats_json=$container_stats
}

read_service_status() {
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
}

read_temperature() {
  temp=""
  if [ -f /sys/class/thermal/thermal_zone0/temp ]; then
    t=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo "")
    if [ -n "$t" ]; then temp=$(awk -v v="$t" 'BEGIN{printf "%.1f", (v>=1000?v/1000:v)}'); fi
  fi
  if [ -n "$temp" ]; then temp_json=$temp; else temp_json=null; fi
}

read_network_bytes() {
  rx=$(awk -F'[: ]+' '/:/{if($1!="lo"){rx+=$3; tx+=$11}} END{print rx+0}' /proc/net/dev)
  tx=$(awk -F'[: ]+' '/:/{if($1!="lo"){rx+=$3; tx+=$11}} END{print tx+0}' /proc/net/dev)
}

compute_power() {
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
}

# Run collectors (order matters for power deltas)
read_power_metrics
read_cpu_stats
read_mem_stats
read_disk_stats
uptime=$(awk '{print int($1)}' /proc/uptime)
cores=$(nproc)
read_load_and_freq
read_os_info
collect_pkg_updates
read_docker_stats
read_service_status
read_temperature
read_network_bytes
compute_power

printf '{"cpu":%s,"mem":%s,"disk":%s,"disk_capacity_total":%s,"disk_stats":%s,"uptime":%s,"temp":%s,"rx":%s,"tx":%s,"ram":%s,"cores":%s,"load_1":%s,"load_5":%s,"load_15":%s,"cpu_freq":%s,"os":"%s","pkg_count":%s,"pkg_list":"%s","docker":%s,"containers":"%s","container_stats":%s,"vnc":"%s","web":"%s","ssh":"%s","power_w":%s,"energy_uj":%s,"energy_range_uj":%s,"swap_usage":%s,"swap_total":%s}\n' \
  "$cpu" "$mem" "$disk" "$disk_total_bytes_json" "$disk_stats_json" "$uptime" "$temp_json" "$rx" "$tx" "$ram" "$cores" "$load_1" \
  "$load_5" "$load_15" "$cpu_freq_json" "$os_json" "$pkg_count" "$pkg_list_json" "$docker" "$containers_json" "$container_stats_json" \
  "$vnc" "$web" "$ssh_enabled" "$power_w_json" "$energy_counter_json" "$energy_range_json" "$swap_usage_json" "$swap_total_json"
'''
