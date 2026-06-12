REMOTE_SCRIPT = r'''
set -e
export LC_ALL=C
export LANG=C

trim_comma() {
  # Remove trailing comma for aggregated strings
  printf '%s' "$1" | sed 's/,$//'
}

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

number_or_null() {
  if [ -n "$1" ]; then
    printf '%s' "$1"
  else
    printf 'null'
  fi
}

run_limited() {
  seconds="$1"
  shift
  if command -v timeout >/dev/null 2>&1; then
    timeout "$seconds" "$@"
  else
    "$@"
  fi
}

positive_timeout() {
  value="$1"
  fallback="$2"
  case "$value" in
    ''|*[!0-9]*) printf '%s' "$fallback" ;;
    *)
      if [ "$value" -gt 0 ]; then
        printf '%s' "$value"
      else
        printf '%s' "$fallback"
      fi
      ;;
  esac
}

collector_mode="${VSERVER_SSH_STATS_MODE:-full}"
pkg_timeout=$(positive_timeout "${VSERVER_SSH_STATS_PKG_TIMEOUT:-}" 6)
docker_timeout=$(positive_timeout "${VSERVER_SSH_STATS_DOCKER_TIMEOUT:-}" 5)

read_power_metrics() {
  power_energy_file=""

  # Check and fix read permissions on Intel RAPL powercap directories
  changed=0
  for rapl in /sys/class/powercap/*/energy_uj; do
    [[ -e "$rapl" ]] || continue;
    [[ -r "$rapl" ]] && power_energy_file=$rapl && break;
    run_limited 2 sudo -n chmod o+r /sys/class/powercap/*/energy_uj 2>/dev/null && changed=1 && power_energy_file="$rapl" && break;
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
  cpu=0
  if [ ! -r /proc/stat ]; then
    return 0
  fi
  read _cpu user nice system idle iowait irq softirq steal _guest _guest_nice < /proc/stat || return 0
  user=${user:-0}
  nice=${nice:-0}
  system=${system:-0}
  idle=${idle:-0}
  iowait=${iowait:-0}
  irq=${irq:-0}
  softirq=${softirq:-0}
  steal=${steal:-0}
  prev_total=$((user+nice+system+idle+iowait+irq+softirq+steal))
  prev_idle=$((idle+iowait))
  sleep 1
  if [ -n "$power_energy_file" ]; then
    power_energy_after=$(cat "$power_energy_file" 2>/dev/null || echo "")
  fi
  read _cpu user nice system idle iowait irq softirq steal _guest _guest_nice < /proc/stat || return 0
  user=${user:-0}
  nice=${nice:-0}
  system=${system:-0}
  idle=${idle:-0}
  iowait=${iowait:-0}
  irq=${irq:-0}
  softirq=${softirq:-0}
  steal=${steal:-0}
  total=$((user+nice+system+idle+iowait+irq+softirq+steal))
  idle_all=$((idle+iowait))
  d_total=$((total-prev_total))
  d_idle=$((idle_all-prev_idle))
  if [ "$d_total" -gt 0 ]; then
    cpu=$(( (100*(d_total - d_idle) + d_total/2) / d_total ))
  else
    cpu=0
  fi
}

read_mem_stats() {
  mem=""
  ram=""
  swap_usage_json=null
  swap_total_json=null
  if [ ! -r /proc/meminfo ]; then
    return 0
  fi
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
  if [ -n "$mem_total" ] && [ "$mem_total" -gt 0 ]; then
    [ -z "$mem_avail" ] && mem_avail=0
    mem=$(( (100*(mem_total - mem_avail) + mem_total/2) / mem_total ))
    ram=$(( (mem_total + 512) / 1024 ))
  else
    mem=""
    ram=""
  fi
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
  disk=""
  disk_total_bytes=0
  disk_free_bytes=0
  disk_stats="[]"
  disk_lines=""
  root_size=""
  root_avail=""
  if command -v df >/dev/null 2>&1; then
    set +e
    disk_lines=$(df -PB1 --output=source,target,size,avail -x tmpfs -x devtmpfs -x squashfs -x overlay 2>/dev/null | tail -n +2 |
awk '{gsub(/\\040/," ",$2); printf "%s\t%s\t%s\t%s\n", $1, $2, $3, $4}')
    disk_status=$?
    set -e
    if [ $disk_status -ne 0 ] || [ -z "$disk_lines" ]; then
      set +e
      disk_lines=$(df -B1 --output=source,target,size,avail -x tmpfs -x devtmpfs -x squashfs -x overlay 2>/dev/null | tail -n +2 |
        awk '{gsub(/\\040/," ",$2); printf "%s\t%s\t%s\t%s\n", $1, $2, $3, $4}')
      disk_status=$?
      set -e
    fi
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
    tab=$(echo -ne '\t')
    oldifs=$IFS
    IFS=$tab
    disk_entries=""
    while read -r source target size avail; do
      [ -z "$source" ] && continue
      [ -z "$size" ] && continue
      [ -z "$avail" ] && avail=0
      disk_total_bytes=$((disk_total_bytes + size))
      disk_free_bytes=$((disk_free_bytes + avail))
      if [ "$target" = "/" ]; then
        root_size=$size
        root_avail=$avail
      fi
      source_json=$(json_escape "$source")
      target_json=$(json_escape "$target")
      disk_entries="$disk_entries{\"name\":\"$source_json\",\"mount\":\"$target_json\",\"total\":$size,\"free\":$avail},"
    done < <(echo "$disk_lines")
    IFS=$oldifs
    if [ -n "$disk_entries" ]; then
      disk_stats="[${disk_entries%,}]"
    fi
  fi
  if [ -n "$root_size" ] && [ "$root_size" -gt 0 ]; then
    disk=$(( (100*(root_size - root_avail) + root_size/2) / root_size ))
  elif [ "$disk_total_bytes" -gt 0 ]; then
    disk=$(( (100*(disk_total_bytes - disk_free_bytes) + disk_total_bytes/2) / disk_total_bytes ))
  fi
  disk_stats_json=$disk_stats
  disk_total_bytes_json=$disk_total_bytes
}

read_load_and_freq() {
  load_1=""
  load_5=""
  load_15=""
  if [ -r /proc/loadavg ]; then
    read load_1 load_5 load_15 _ < /proc/loadavg || true
  fi
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
  pkg_count=""
  pkg_list=""
  pkg_update_lines=""
  pkg_updates_complete=0
  updates=""
  pkg_status=1
  set +e
  if command -v apt-get >/dev/null 2>&1; then
    pkg_update_lines=$(run_limited "$pkg_timeout" apt-get -s upgrade 2>/dev/null)
    pkg_status=$?
    if [ "$pkg_status" -eq 0 ]; then
      updates=$(printf '%s\n' "$pkg_update_lines" | awk '/^Inst /{print $2}')
    fi
  elif command -v dnf >/dev/null 2>&1; then
    pkg_update_lines=$(run_limited "$pkg_timeout" dnf -q check-update --refresh 2>/dev/null)
    pkg_status=$?
    if [ "$pkg_status" -eq 0 ] || [ "$pkg_status" -eq 100 ]; then
      updates=$(printf '%s\n' "$pkg_update_lines" | awk '/^[[:alnum:].-]+[[:space:]]/ {print $1}')
    fi
  elif command -v yum >/dev/null 2>&1; then
    pkg_update_lines=$(run_limited "$pkg_timeout" yum -q check-update 2>/dev/null)
    pkg_status=$?
    if [ "$pkg_status" -eq 0 ] || [ "$pkg_status" -eq 100 ]; then
      updates=$(printf '%s\n' "$pkg_update_lines" | awk '/^[[:alnum:].-]+[[:space:]]/ {print $1}')
    fi
  elif command -v pacman >/dev/null 2>&1; then
    pkg_update_lines=$(run_limited "$pkg_timeout" pacman -Qu 2>/dev/null)
    pkg_status=$?
    if [ "$pkg_status" -eq 0 ]; then
      updates=$(printf '%s\n' "$pkg_update_lines" | awk '{print $1}')
    fi
  elif command -v zypper >/dev/null 2>&1; then
    pkg_update_lines=$(run_limited "$pkg_timeout" zypper --quiet lu 2>/dev/null)
    pkg_status=$?
    if [ "$pkg_status" -eq 0 ] || [ "$pkg_status" -eq 100 ]; then
      updates=$(printf '%s\n' "$pkg_update_lines" | awk '/^[[:alnum:]].*\|/{print $3}')
    fi
  elif command -v apk >/dev/null 2>&1; then
    pkg_update_lines=$(run_limited "$pkg_timeout" apk version -l '<' 2>/dev/null)
    pkg_status=$?
    if [ "$pkg_status" -eq 0 ]; then
      updates=$(printf '%s\n' "$pkg_update_lines" | awk '{print $1}')
    fi
  else
    updates=""
    pkg_status=0
  fi
  set -e

  if [ "$pkg_status" -eq 0 ] || [ "$pkg_status" -eq 100 ]; then
    pkg_updates_complete=1
    pkg_count=$(echo "$updates" | grep -c . || true)
    if [ -n "$updates" ]; then
      pkg_list=$(trim_comma "$(echo "$updates" | head -n 10 | tr '\n' ',' )")
    fi
  fi
  pkg_list_json=$(json_escape "$pkg_list")
}

read_docker_stats() {
  docker=""
  containers=""
  container_stats="[]"
  docker_stats_complete=0
  if ! command -v docker >/dev/null 2>&1; then
    docker=0
    docker_stats_complete=1
  else
    set +e
    run_limited "$docker_timeout" docker info >/dev/null 2>&1
    docker_info_status=$?
    set -e
    if [ "$docker_info_status" -eq 0 ]; then
      set +e
      docker=1
      docker_stats_complete=1
      container_names=$(run_limited "$docker_timeout" docker ps --format '{{.Names}}' 2>/dev/null)
      container_names_status=$?
      containers=$(printf '%s\n' "$container_names" | tr '\n' ',' )
      containers=$(trim_comma "$containers")
      containers=${containers//,/, }
      stats_raw=$(run_limited "$docker_timeout" docker stats --no-stream --format '{{.Name}}|{{.CPUPerc}}|{{.MemPerc}}' 2>/dev/null)
      stats_status=$?
      stats_lines=$(printf '%s\n' "$stats_raw" | sed 's/%//g')
      ps_lines=$(run_limited "$docker_timeout" docker ps --format '{{.ID}}|{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}' 2>/dev/null)
      ps_status=$?
      inspect_lines=""
      inspect_status=0
      if [ -n "$ps_lines" ]; then
        container_ids=$(printf '%s\n' "$ps_lines" | awk -F'|' '{print $1}' | tr '\n' ' ')
        inspect_lines=$(run_limited "$docker_timeout" docker inspect --format '{{.Id}}|{{.RestartCount}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $container_ids 2>/dev/null)
        inspect_status=$?
      fi
      if [ "$container_names_status" -ne 0 ] || [ "$stats_status" -ne 0 ] || [ "$ps_status" -ne 0 ] || [ "$inspect_status" -ne 0 ]; then
        docker_stats_complete=0
      fi
      if [ -n "$ps_lines" ]; then
        container_entries=""
        while IFS='|' read -r container_id name image status ports; do
          [ -z "$name" ] && continue
          stats_match=$(printf '%s\n' "$stats_lines" |
            awk -F'|' -v n="$name" '$1 == n {printf "%.2f|%.2f", $2+0, $3+0; exit}')
          container_cpu=0
          container_mem=0
          if [ -n "$stats_match" ]; then
            container_cpu=${stats_match%%|*}
            container_mem=${stats_match#*|}
          fi
          inspect_match=$(printf '%s\n' "$inspect_lines" |
            awk -F'|' -v id="$container_id" 'index($1, id) == 1 {print $2 "|" $3; exit}')
          restart_count=""
          health_state=""
          if [ -n "$inspect_match" ]; then
            restart_count=${inspect_match%%|*}
            health_state=${inspect_match#*|}
          fi
          restart_count=${restart_count//[^0-9]/}
          restart_count_json=$(number_or_null "$restart_count")
          name_json=$(json_escape "$name")
          image_json=$(json_escape "$image")
          status_json=$(json_escape "$status")
          ports_json=$(json_escape "$ports")
          health_json=$(json_escape "$health_state")
          container_entries="$container_entries{\"name\":\"$name_json\",\"cpu\":$container_cpu,\"mem\":$container_mem,\"image\":\"$image_json\",\"status\":\"$status_json\",\"restart_count\":$restart_count_json,\"ports\":\"$ports_json\",\"health_state\":\"$health_json\"},"
        done < <(printf '%s\n' "$ps_lines")
        if [ -n "$container_entries" ]; then
          container_stats="[${container_entries%,}]"
        else
          container_stats="[]"
        fi
      else
        container_stats="[]"
      fi
      set -e
    elif [ "$docker_info_status" -eq 124 ] || [ "$docker_info_status" -eq 137 ]; then
      docker=""
      docker_stats_complete=0
    else
      docker=0
      docker_stats_complete=1
    fi
  fi
  containers_json=$(json_escape "$containers")
  container_stats_json=$container_stats
}

read_mac_addresses() {
  mac_address=""
  mac_entries=""
  seen_macs=","
  default_iface=""
  if command -v ip >/dev/null 2>&1; then
    default_iface=$(ip route show default 2>/dev/null | awk '{print $5; exit}')
  fi

  add_mac() {
    candidate=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')
    case "$candidate" in
      ""|"00:00:00:00:00:00") return ;;
    esac
    if ! printf '%s' "$candidate" | grep -Eq '^[0-9a-f]{2}(:[0-9a-f]{2}){5}$'; then
      return
    fi
    case "$seen_macs" in
      *",$candidate,"*) return ;;
    esac
    seen_macs="$seen_macs$candidate,"
    [ -z "$mac_address" ] && mac_address="$candidate"
    mac_entries="$mac_entries\"$candidate\","
  }

  if [ -n "$default_iface" ] && [ -r "/sys/class/net/$default_iface/address" ]; then
    add_mac "$(cat "/sys/class/net/$default_iface/address" 2>/dev/null || echo "")"
  fi

  for netdev in /sys/class/net/*; do
    [ -e "$netdev" ] || continue
    iface=$(basename "$netdev")
    [ "$iface" = "lo" ] && continue
    [ -r "$netdev/address" ] || continue
    add_mac "$(cat "$netdev/address" 2>/dev/null || echo "")"
  done

  if [ -n "$mac_entries" ]; then
    mac_addresses_json="[${mac_entries%,}]"
  else
    mac_addresses_json="[]"
  fi
  mac_address_json=$(json_escape "$mac_address")
}

read_top_processes() {
  top_processes_json="[]"
  if command -v ps >/dev/null 2>&1; then
    set +e
    top_process_lines=$(ps -eo pid=,comm=,pcpu=,pmem= --sort=-pcpu 2>/dev/null | head -n 5 |
      awk '{pid=$1; process_cpu=$(NF-1)+0; process_mem=$NF+0; command=$2; printf "%s\t%s\t%.2f\t%.2f\n", pid, command, process_cpu, process_mem}')
    top_process_status=$?
    set -e
    if [ $top_process_status -eq 0 ] && [ -n "$top_process_lines" ]; then
      tab=$(echo -ne '\t')
      oldifs=$IFS
      IFS=$tab
      top_process_entries=""
      while read -r pid command process_cpu process_mem; do
        [ -z "$pid" ] && continue
        command_json=$(json_escape "$command")
        top_process_entries="$top_process_entries{\"pid\":$pid,\"command\":\"$command_json\",\"cpu\":$process_cpu,\"mem\":$process_mem},"
      done < <(echo "$top_process_lines")
      IFS=$oldifs
      if [ -n "$top_process_entries" ]; then
        top_processes_json="[${top_process_entries%,}]"
      fi
    fi
  fi
}

read_service_status() {
  sock_cmd=""
  if command -v ss >/dev/null 2>&1; then
    sock_cmd="ss -tuln"
  elif command -v netstat >/dev/null 2>&1; then
    sock_cmd="netstat -tuln"
  fi

  if [ -n "$sock_cmd" ] && $sock_cmd 2>/dev/null | grep -E ':22[[:space:]]' >/dev/null; then
    ssh_enabled="yes"
  else
    ssh_enabled="no"
  fi

  if [ -n "$sock_cmd" ] && $sock_cmd 2>/dev/null | grep -E ':(80|443)[[:space:]]' >/dev/null; then
    web="yes"
  else
    web="no"
  fi

  if [ -n "$sock_cmd" ] && $sock_cmd 2>/dev/null | grep -E ':5900[[:space:]]' >/dev/null; then
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
  rx=0
  tx=0
  if [ -r /proc/net/dev ]; then
    rx=$(awk -F'[: ]+' '/:/{if($1!="lo"){rx+=$3; tx+=$11}} END{print rx+0}' /proc/net/dev 2>/dev/null || echo 0)
    tx=$(awk -F'[: ]+' '/:/{if($1!="lo"){rx+=$3; tx+=$11}} END{print tx+0}' /proc/net/dev 2>/dev/null || echo 0)
  fi
}

read_boot_and_kernel_status() {
  reboot_required=0
  last_boot=""
  kernel_version=$(uname -r 2>/dev/null || echo "")

  if [ -f /var/run/reboot-required ]; then
    reboot_required=1
  fi

  if [ -r /proc/uptime ] && command -v date >/dev/null 2>&1; then
    set +e
    uptime_seconds=$(awk '{print int($1)}' /proc/uptime 2>/dev/null)
    now_seconds=$(date +%s 2>/dev/null)
    set -e
    if [ -n "$uptime_seconds" ] && [ -n "$now_seconds" ]; then
      boot_epoch=$((now_seconds - uptime_seconds))
      last_boot=$(date -u -d "@$boot_epoch" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "")
    fi
  fi

  last_boot_json=$(json_escape "$last_boot")
  kernel_version_json=$(json_escape "$kernel_version")
}

read_primary_ip() {
  primary_ip=""
  if command -v ip >/dev/null 2>&1; then
    set +e
    primary_ip=$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')
    set -e
  fi
  if [ -z "$primary_ip" ] && command -v hostname >/dev/null 2>&1; then
    set +e
    primary_ip=$(hostname -I 2>/dev/null | awk '{print $1}')
    set -e
  fi
  primary_ip_json=$(json_escape "$primary_ip")
}

collect_security_updates() {
  security_updates=""
  security_status=1
  security_lines=""
  set +e
  if [ -n "$pkg_update_lines" ]; then
    security_updates=$(printf '%s\n' "$pkg_update_lines" | awk '/^Inst / && ($0 ~ /security|Security|\/.*-security/) {count++} END{print count+0}')
    security_status=0
  elif command -v apt-get >/dev/null 2>&1; then
    security_lines=$(run_limited "$pkg_timeout" apt-get -s upgrade 2>/dev/null)
    security_status=$?
    if [ "$security_status" -eq 0 ]; then
      security_updates=$(printf '%s\n' "$security_lines" | awk '/^Inst / && ($0 ~ /security|Security|\/.*-security/) {count++} END{print count+0}')
    fi
  elif command -v dnf >/dev/null 2>&1; then
    security_lines=$(run_limited "$pkg_timeout" dnf -q updateinfo list security 2>/dev/null)
    security_status=$?
    if [ "$security_status" -eq 0 ] || [ "$security_status" -eq 100 ]; then
      security_updates=$(printf '%s\n' "$security_lines" | awk 'NF {count++} END{print count+0}')
    fi
  elif command -v yum >/dev/null 2>&1; then
    security_lines=$(run_limited "$pkg_timeout" yum -q updateinfo list security 2>/dev/null)
    security_status=$?
    if [ "$security_status" -eq 0 ] || [ "$security_status" -eq 100 ]; then
      security_updates=$(printf '%s\n' "$security_lines" | awk 'NF {count++} END{print count+0}')
    fi
  elif command -v zypper >/dev/null 2>&1; then
    security_lines=$(run_limited "$pkg_timeout" zypper --quiet lu --category security 2>/dev/null)
    security_status=$?
    if [ "$security_status" -eq 0 ] || [ "$security_status" -eq 100 ]; then
      security_updates=$(printf '%s\n' "$security_lines" | awk '/^[[:alnum:]].*\|/ {count++} END{print count+0}')
    fi
  else
    security_updates=0
    security_status=0
  fi
  set -e
  if [ "$security_status" -ne 0 ] && [ "$security_status" -ne 100 ]; then
    security_updates=""
  fi
}

read_systemd_failures() {
  failed_systemd_units=0
  failed_systemd_units_json="[]"
  if command -v systemctl >/dev/null 2>&1; then
    set +e
    failed_units=$(run_limited 4 systemctl --failed --no-legend --plain 2>/dev/null | awk '{print $1}' | head -n 20)
    set -e
    if [ -n "$failed_units" ]; then
      failed_systemd_units=$(printf '%s\n' "$failed_units" | grep -c . || true)
      unit_entries=""
      while IFS= read -r unit; do
        [ -z "$unit" ] && continue
        unit_json=$(json_escape "$unit")
        unit_entries="$unit_entries\"$unit_json\","
      done < <(printf '%s\n' "$failed_units")
      if [ -n "$unit_entries" ]; then
        failed_systemd_units_json="[${unit_entries%,}]"
      fi
    fi
  fi
}

read_journal_errors() {
  journal_errors=0
  if command -v journalctl >/dev/null 2>&1; then
    set +e
    journal_errors=$(run_limited 4 journalctl -p err --since "15 min ago" --no-pager -q 2>/dev/null | grep -c .)
    set -e
    journal_errors=${journal_errors:-0}
  fi
}

read_root_filesystem_status() {
  root_fs_readonly=0
  if [ -r /proc/mounts ]; then
    root_mount_options=$(awk '$2 == "/" {print $4; exit}' /proc/mounts 2>/dev/null || echo "")
    case ",$root_mount_options," in
      *,ro,*) root_fs_readonly=1 ;;
    esac
  fi
}

read_disk_io_bytes() {
  disk_read_bytes=0
  disk_write_bytes=0
  for stat_file in /sys/block/*/stat; do
    [ -r "$stat_file" ] || continue
    device=$(basename "$(dirname "$stat_file")")
    case "$device" in
      loop*|ram*|zram*) continue ;;
    esac
    read _reads _reads_merged sectors_read _read_ms _writes _writes_merged sectors_written _write_ms _ios _io_ms _weighted_ms < "$stat_file" || continue
    sectors_read=${sectors_read:-0}
    sectors_written=${sectors_written:-0}
    disk_read_bytes=$((disk_read_bytes + sectors_read * 512))
    disk_write_bytes=$((disk_write_bytes + sectors_written * 512))
  done
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

# Prepare JSON-safe fallbacks for numeric values
prepare_numeric_json_values() {
  cpu_json=$(number_or_null "$cpu")
  mem_json=$(number_or_null "$mem")
  disk_json=$(number_or_null "$disk")
  disk_total_bytes_json=$(number_or_null "$disk_total_bytes")
  uptime_json=$(number_or_null "$uptime")
  rx_json=$(number_or_null "$rx")
  tx_json=$(number_or_null "$tx")
  ram_json=$(number_or_null "$ram")
  cores_json=$(number_or_null "$cores")
  load_1_json=$(number_or_null "$load_1")
  load_5_json=$(number_or_null "$load_5")
  load_15_json=$(number_or_null "$load_15")
  pkg_count_json=$(number_or_null "$pkg_count")
  docker_json=$(number_or_null "$docker")
  swap_usage_json=$(number_or_null "$swap_usage_json")
  swap_total_json=$(number_or_null "$swap_total_json")
  reboot_required_json=$(number_or_null "$reboot_required")
  security_updates_json=$(number_or_null "$security_updates")
  failed_systemd_units_json_count=$(number_or_null "$failed_systemd_units")
  journal_errors_json=$(number_or_null "$journal_errors")
  root_fs_readonly_json=$(number_or_null "$root_fs_readonly")
  disk_read_bytes_json=$(number_or_null "$disk_read_bytes")
  disk_write_bytes_json=$(number_or_null "$disk_write_bytes")
}

print_package_json() {
  pkg_count_json=$(number_or_null "$pkg_count")
  security_updates_json=$(number_or_null "$security_updates")
  pkg_updates_complete_json=$(number_or_null "$pkg_updates_complete")
  printf '{"pkg_count":%s,"pkg_list":"%s","security_updates":%s,"pkg_updates_complete":%s}\n' \
    "$pkg_count_json" "$pkg_list_json" "$security_updates_json" "$pkg_updates_complete_json"
}

print_docker_json() {
  docker_json=$(number_or_null "$docker")
  docker_stats_complete_json=$(number_or_null "$docker_stats_complete")
  printf '{"docker":%s,"containers":"%s","container_stats":%s,"docker_stats_complete":%s}\n' \
    "$docker_json" "$containers_json" "$container_stats_json" "$docker_stats_complete_json"
}

init_package_defaults() {
  pkg_count=""
  pkg_list=""
  pkg_update_lines=""
  pkg_updates_complete=""
  pkg_list_json=""
  security_updates=""
}

init_docker_defaults() {
  docker=""
  containers=""
  container_stats="[]"
  docker_stats_complete=""
  containers_json=""
  container_stats_json="[]"
}

case "$collector_mode" in
  packages)
    collect_pkg_updates
    collect_security_updates
    print_package_json
    exit 0
    ;;
  docker)
    read_docker_stats
    print_docker_json
    exit 0
    ;;
esac

# Run collectors (order matters for power deltas)
read_power_metrics
read_cpu_stats
read_mem_stats
read_disk_stats
uptime=$(awk '{print int($1)}' /proc/uptime 2>/dev/null || echo "")
cores=$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo "")
read_load_and_freq
read_os_info
if [ "$collector_mode" = "full" ]; then
  collect_pkg_updates
  read_docker_stats
else
  init_package_defaults
  init_docker_defaults
fi
read_mac_addresses
read_top_processes
read_service_status
read_temperature
read_network_bytes
read_boot_and_kernel_status
read_primary_ip
if [ "$collector_mode" = "full" ]; then
  collect_security_updates
fi
read_systemd_failures
read_journal_errors
read_root_filesystem_status
read_disk_io_bytes
compute_power
prepare_numeric_json_values

# restore permissions iff changed
[[ "${changed:-0}" -eq 1 ]] && run_limited 2 sudo -n chmod o-r /sys/class/powercap/*/energy_uj 2>/dev/null

printf '{"cpu":%s,"mem":%s,"disk":%s,"disk_capacity_total":%s,"disk_stats":%s,"uptime":%s,"temp":%s,"rx":%s,"tx":%s,"ram":%s,"cores":%s,"load_1":%s,"load_5":%s,"load_15":%s,"cpu_freq":%s,"os":"%s","pkg_count":%s,"pkg_list":"%s","docker":%s,"containers":"%s","container_stats":%s,"mac_address":"%s","mac_addresses":%s,"top_processes":%s,"vnc":"%s","web":"%s","ssh":"%s","power_w":%s,"energy_uj":%s,"energy_range_uj":%s,"swap_usage":%s,"swap_total":%s,"reboot_required":%s,"security_updates":%s,"last_boot":"%s","kernel_version":"%s","primary_ip":"%s","failed_systemd_units":%s,"failed_systemd_units_list":%s,"journal_errors":%s,"root_fs_readonly":%s,"disk_read_bytes":%s,"disk_write_bytes":%s}\n' \
  "$cpu_json" "$mem_json" "$disk_json" "$disk_total_bytes_json" "$disk_stats_json" "$uptime_json" "$temp_json" "$rx_json" "$tx_json" "$ram_json" "$cores_json" "$load_1_json" \
  "$load_5_json" "$load_15_json" "$cpu_freq_json" "$os_json" "$pkg_count_json" "$pkg_list_json" "$docker_json" "$containers_json" "$container_stats_json" \
  "$mac_address_json" "$mac_addresses_json" "$top_processes_json" "$vnc" "$web" "$ssh_enabled" "$power_w_json" "$energy_counter_json" "$energy_range_json" "$swap_usage_json" "$swap_total_json" \
  "$reboot_required_json" "$security_updates_json" "$last_boot_json" "$kernel_version_json" "$primary_ip_json" "$failed_systemd_units_json_count" "$failed_systemd_units_json" \
  "$journal_errors_json" "$root_fs_readonly_json" "$disk_read_bytes_json" "$disk_write_bytes_json"
'''
