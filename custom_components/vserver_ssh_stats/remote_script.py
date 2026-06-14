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
storage_timeout=$(positive_timeout "${VSERVER_SSH_STATS_STORAGE_TIMEOUT:-}" 15)
docker_quick_timeout=$docker_timeout
if [ "$docker_quick_timeout" -gt 30 ]; then
  docker_quick_timeout=30
fi

human_bytes() {
  raw=${1//[[:space:]]/}
  [[ "$raw" =~ ^([0-9]+([.][0-9]+)?)([KMGTPE]?i?B)?$ ]] || return 0
  value=${BASH_REMATCH[1]}
  unit=${BASH_REMATCH[3]}
  factor=1
  case "$unit" in
    kB|KB) factor=1000 ;;
    MB) factor=1000000 ;;
    GB) factor=1000000000 ;;
    TB) factor=1000000000000 ;;
    KiB) factor=1024 ;;
    MiB) factor=1048576 ;;
    GiB) factor=1073741824 ;;
    TiB) factor=1099511627776 ;;
  esac
  awk -v value="$value" -v factor="$factor" 'BEGIN {printf "%.0f", value*factor}'
}

read_power_metrics() {
  power_energy_file=""
  for rapl in /sys/class/powercap/*/energy_uj; do
    [[ -r "$rapl" ]] || continue
    power_energy_file=$rapl
    break
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

read_cpu_throttle() {
  throttle_periods=""
  throttle_usec=""
  container_pid="$1"
  [ -n "$container_pid" ] && [ "$container_pid" -gt 0 ] 2>/dev/null || return 0
  cgroup_path=$(awk -F: '$1 == "0" {print $3; exit} $2 ~ /(^|,)cpu(,|$)/ {print $3; exit}' "/proc/$container_pid/cgroup" 2>/dev/null)
  [ -n "$cgroup_path" ] || return 0
  for cpu_stat in "/sys/fs/cgroup$cgroup_path/cpu.stat" "/sys/fs/cgroup/cpu$cgroup_path/cpu.stat" "/sys/fs/cgroup/cpu,cpuacct$cgroup_path/cpu.stat"; do
    [ -r "$cpu_stat" ] || continue
    throttle_periods=$(awk '$1 == "nr_throttled" {print $2; exit}' "$cpu_stat" 2>/dev/null)
    throttle_usec=$(awk '
      $1 == "throttled_usec" {print $2; exit}
      $1 == "throttled_time" {printf "%.0f", $2/1000; exit}
    ' "$cpu_stat" 2>/dev/null)
    break
  done
}

read_docker_stats() {
  docker=""
  containers=""
  container_stats="[]"
  docker_stats_complete=0
  docker_stats_partial=0
  docker_images_size_bytes=""
  docker_containers_size_bytes=""
  docker_volumes_size_bytes=""
  docker_build_cache_size_bytes=""
  docker_command=(docker)
  if ! command -v docker >/dev/null 2>&1; then
    docker=0
    docker_stats_complete=1
  else
    set +e
    run_limited "$docker_quick_timeout" "${docker_command[@]}" info >/dev/null 2>&1
    docker_info_status=$?
    if [ "$docker_info_status" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
      docker_command=(sudo -n docker)
      run_limited "$docker_quick_timeout" "${docker_command[@]}" info >/dev/null 2>&1
      docker_info_status=$?
    fi
    set -e
    if [ "$docker_info_status" -eq 0 ]; then
      set +e
      docker=1
      ps_lines=$(run_limited "$docker_quick_timeout" "${docker_command[@]}" ps -a --format '{{.ID}}|{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}' 2>/dev/null)
      ps_status=$?
      if [ "$ps_status" -eq 0 ]; then
        docker_stats_complete=1
      fi
      containers=$(printf '%s\n' "$ps_lines" | awk -F'|' 'NF {print $2}' | tr '\n' ',' )
      containers=$(trim_comma "$containers")
      containers=${containers//,/, }
      stats_lines=""
      inspect_lines=""
      if [ -n "$ps_lines" ]; then
        container_ids=$(printf '%s\n' "$ps_lines" | awk -F'|' '{print $1}' | tr '\n' ' ')
        running_container_ids=$(printf '%s\n' "$ps_lines" |
          awk -F'|' '$4 ~ /^(Up|Restarting)/ {printf "%s ", $1}')
        stats_status=0
        if [ -n "$running_container_ids" ]; then
          read -r -a running_container_id_args <<< "$running_container_ids"
          stats_raw=$(run_limited "$docker_timeout" "${docker_command[@]}" stats --no-stream --format '{{.ID}}|{{.Name}}|{{.CPUPerc}}|{{.MemPerc}}|{{.MemUsage}}|{{.PIDs}}' "${running_container_id_args[@]}" 2>/dev/null)
          stats_status=$?
          if [ "$stats_status" -eq 0 ] && ! printf '%s\n' "$stats_raw" |
            awk -F'|' '
              function value(raw) {
                gsub(/%/, "", raw)
                gsub(/,/, ".", raw)
                gsub(/[[:space:]]/, "", raw)
                return raw ~ /^[0-9]+([.][0-9]+)?$/ ? raw + 0 : -1
              }
              value($3) > 0 || value($4) > 0 { found=1 }
              END { exit found ? 0 : 1 }'; then
            sleep 1
            retry_stats_raw=$(run_limited "$docker_timeout" "${docker_command[@]}" stats --no-stream --format '{{.ID}}|{{.Name}}|{{.CPUPerc}}|{{.MemPerc}}|{{.MemUsage}}|{{.PIDs}}' "${running_container_id_args[@]}" 2>/dev/null)
            retry_stats_status=$?
            if [ "$retry_stats_status" -eq 0 ] && [ -n "$retry_stats_raw" ]; then
              stats_raw=$retry_stats_raw
            else
              stats_status=$retry_stats_status
            fi
          fi
          stats_lines=$stats_raw
        fi
        read -r -a container_id_args <<< "$container_ids"
        inspect_lines=$(run_limited "$docker_quick_timeout" "${docker_command[@]}" inspect --format '{{.Id}}|{{.RestartCount}}|{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}|{{.HostConfig.RestartPolicy.Name}}|{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{index .Config.Labels "com.docker.swarm.service.name"}}|{{.State.Pid}}|{{.HostConfig.Memory}}' "${container_id_args[@]}" 2>/dev/null)
        inspect_status=$?
        if [ "$stats_status" -ne 0 ] || [ "$inspect_status" -ne 0 ]; then
          docker_stats_partial=1
        fi
      fi
      if [ -n "$ps_lines" ]; then
        container_entries=""
        while IFS='|' read -r container_id name image status ports; do
          [ -z "$name" ] && continue
          stats_match=$(printf '%s\n' "$stats_lines" |
            awk -F'|' -v id="$container_id" -v n="$name" '
              function percentage(value) {
                gsub(/%/, "", value)
                gsub(/,/, ".", value)
                gsub(/[[:space:]]/, "", value)
                if (value ~ /^[0-9]+([.][0-9]+)?$/) {
                  return sprintf("%.2f", value + 0)
                }
                return "null"
              }
              $1 == id || $2 == n {
                print percentage($3) "|" percentage($4) "|" $5 "|" $6
                exit
              }')
          container_cpu=null
          container_mem=null
          container_mem_usage=""
          container_pids=""
          if [ -n "$stats_match" ]; then
            IFS='|' read -r container_cpu container_mem container_mem_usage_raw container_pids <<< "$stats_match"
            container_mem_usage=${container_mem_usage_raw%%/*}
            container_mem_usage=$(human_bytes "$container_mem_usage")
          fi
          inspect_match=$(printf '%s\n' "$inspect_lines" |
            awk -F'|' -v id="$container_id" 'index($1, id) == 1 {print $2 "|" $3 "|" $4 "|" $5 "|" $6 "|" $7 "|" $8 "|" $9 "|" $10; exit}')
          restart_count=""
          running_state=""
          health_state=""
          restart_policy=""
          compose_project=""
          compose_service=""
          swarm_service=""
          container_pid=""
          container_memory_limit=""
          if [ -n "$inspect_match" ]; then
            restart_count=${inspect_match%%|*}
            inspect_remainder=${inspect_match#*|}
            running_state=${inspect_remainder%%|*}
            inspect_remainder=${inspect_remainder#*|}
            health_state=${inspect_remainder%%|*}
            inspect_remainder=${inspect_remainder#*|}
            restart_policy=${inspect_remainder%%|*}
            inspect_remainder=${inspect_remainder#*|}
            compose_project=${inspect_remainder%%|*}
            inspect_remainder=${inspect_remainder#*|}
            compose_service=${inspect_remainder%%|*}
            inspect_remainder=${inspect_remainder#*|}
            swarm_service=${inspect_remainder%%|*}
            inspect_remainder=${inspect_remainder#*|}
            container_pid=${inspect_remainder%%|*}
            container_memory_limit=${inspect_remainder#*|}
          fi
          restart_count=${restart_count//[^0-9]/}
          restart_count_json=$(number_or_null "$restart_count")
          container_pid=${container_pid//[^0-9]/}
          container_memory_limit=${container_memory_limit//[^0-9]/}
          container_pids=${container_pids//[^0-9]/}
          read_cpu_throttle "$container_pid"
          case "$running_state" in
            true|false) running_json=$running_state ;;
            *) running_json=null ;;
          esac
          container_id_json=$(json_escape "$container_id")
          name_json=$(json_escape "$name")
          image_json=$(json_escape "$image")
          status_json=$(json_escape "$status")
          ports_json=$(json_escape "$ports")
          health_json=$(json_escape "$health_state")
          restart_policy_json=$(json_escape "$restart_policy")
          compose_project_json=$(json_escape "$compose_project")
          compose_service_json=$(json_escape "$compose_service")
          swarm_service_json=$(json_escape "$swarm_service")
          container_entries="$container_entries{\"id\":\"$container_id_json\",\"name\":\"$name_json\",\"cpu\":$container_cpu,\"mem\":$container_mem,\"memory_usage_bytes\":$(number_or_null "$container_mem_usage"),\"memory_limit_bytes\":$(number_or_null "$container_memory_limit"),\"pids\":$(number_or_null "$container_pids"),\"cpu_throttled_periods\":$(number_or_null "$throttle_periods"),\"cpu_throttled_usec\":$(number_or_null "$throttle_usec"),\"image\":\"$image_json\",\"status\":\"$status_json\",\"restart_count\":$restart_count_json,\"ports\":\"$ports_json\",\"health_state\":\"$health_json\",\"running\":$running_json,\"restart_policy\":\"$restart_policy_json\",\"compose_project\":\"$compose_project_json\",\"compose_service\":\"$compose_service_json\",\"swarm_service\":\"$swarm_service_json\"},"
        done < <(printf '%s\n' "$ps_lines")
        if [ -n "$container_entries" ]; then
          container_stats="[${container_entries%,}]"
        else
          container_stats="[]"
        fi
      else
        container_stats="[]"
      fi
      docker_disk_lines=$(run_limited "$docker_quick_timeout" "${docker_command[@]}" system df --format '{{.Type}}|{{.Size}}|{{.Reclaimable}}' 2>/dev/null)
      while IFS='|' read -r docker_disk_type docker_disk_size _docker_reclaimable; do
        docker_disk_bytes=$(human_bytes "$docker_disk_size")
        case "$docker_disk_type" in
          Images) docker_images_size_bytes=$docker_disk_bytes ;;
          Containers) docker_containers_size_bytes=$docker_disk_bytes ;;
          "Local Volumes") docker_volumes_size_bytes=$docker_disk_bytes ;;
          "Build Cache") docker_build_cache_size_bytes=$docker_disk_bytes ;;
        esac
      done < <(printf '%s\n' "$docker_disk_lines")
      set -e
    else
      docker=1
      docker_stats_partial=1
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

parse_process_state() {
  local stat_line="$1"
  local stat_remainder
  parsed_process_state=""
  case "$stat_line" in
    *") "*)
      stat_remainder=${stat_line##*) }
      parsed_process_state=${stat_remainder%% *}
      ;;
  esac
}

read_process_state() {
  process_total=0
  process_running=0
  process_zombies=0
  for stat_path in /proc/[0-9]*/stat; do
    [ -r "$stat_path" ] || continue
    stat_line=$(cat "$stat_path" 2>/dev/null || echo "")
    [ -n "$stat_line" ] || continue
    parse_process_state "$stat_line"
    process_state=$parsed_process_state
    case "$process_state" in
      R|S|D|Z|T|t|X|x|K|W|P|I) ;;
      *) continue ;;
    esac
    process_total=$((process_total + 1))
    [ "$process_state" = "R" ] && process_running=$((process_running + 1))
    [ "$process_state" = "Z" ] && process_zombies=$((process_zombies + 1))
  done
  return 0
}

read_connection_metrics() {
  tcp_established=0
  tcp_time_wait=0
  for tcp_file in /proc/net/tcp /proc/net/tcp6; do
    [ -r "$tcp_file" ] || continue
    established=$(awk 'NR > 1 && $4 == "01" {count++} END {print count+0}' "$tcp_file" 2>/dev/null || echo 0)
    time_wait=$(awk 'NR > 1 && $4 == "06" {count++} END {print count+0}' "$tcp_file" 2>/dev/null || echo 0)
    tcp_established=$((tcp_established + established))
    tcp_time_wait=$((tcp_time_wait + time_wait))
  done

  sockets_used=""
  tcp_sockets_in_use=""
  if [ -r /proc/net/sockstat ]; then
    sockets_used=$(awk '$1 == "sockets:" {for(i=2;i<=NF;i++) if($i=="used") print $(i+1)}' /proc/net/sockstat 2>/dev/null)
    tcp_sockets_in_use=$(awk '$1 == "TCP:" {for(i=2;i<=NF;i++) if($i=="inuse") print $(i+1)}' /proc/net/sockstat 2>/dev/null)
  fi
  if [ -r /proc/net/sockstat6 ]; then
    tcp6_sockets_in_use=$(awk '$1 == "TCP6:" {for(i=2;i<=NF;i++) if($i=="inuse") print $(i+1)}' /proc/net/sockstat6 2>/dev/null)
    if [ -n "$tcp6_sockets_in_use" ]; then
      tcp_sockets_in_use=$((${tcp_sockets_in_use:-0} + tcp6_sockets_in_use))
    fi
  fi

  conntrack_count=""
  conntrack_max=""
  [ -r /proc/sys/net/netfilter/nf_conntrack_count ] && conntrack_count=$(cat /proc/sys/net/netfilter/nf_conntrack_count 2>/dev/null || echo "")
  [ -r /proc/sys/net/netfilter/nf_conntrack_max ] && conntrack_max=$(cat /proc/sys/net/netfilter/nf_conntrack_max 2>/dev/null || echo "")
  return 0
}

read_software_raid() {
  software_raid_arrays=0
  software_raid_degraded=0
  software_raid_rebuild_active=0
  software_raid_rebuild_progress=""
  software_raid_rebuild_remaining_minutes=""
  raid_arrays_json="[]"
  [ -r /proc/mdstat ] || return 0

  raid_lines=$(awk '
    function emit() {
      if (name != "") print name "\t" state "\t" level "\t" members "\t" degraded "\t" rebuild "\t" progress "\t" finish
    }
    /^md[^[:space:]]+[[:space:]]*:/ {
      emit(); name=$1; state=$3; level=$4; members=""; degraded=(state=="active" || state=="read-auto" ? 0 : 1); rebuild=0; progress=""; finish=""; if ($0 ~ /\(F\)/) degraded=1; next
    }
    name != "" {
      if (match($0, /\[[U_]+\]/)) {
        members=substr($0, RSTART, RLENGTH)
        if (index(members, "_") > 0) degraded=1
      }
      if ($0 ~ /(recovery|resync|reshape)[[:space:]]*=/) {
        rebuild=1
        if (match($0, /[0-9]+([.][0-9]+)?%/)) progress=substr($0, RSTART, RLENGTH-1)
        if (match($0, /finish=[0-9]+([.][0-9]+)?min/)) finish=substr($0, RSTART+7, RLENGTH-10)
      }
    }
    END {emit()}
  ' /proc/mdstat 2>/dev/null)

  raid_entries=""
  tab=$(echo -ne '\t')
  while IFS="$tab" read -r raid_name raid_state raid_level raid_members raid_degraded raid_rebuild raid_progress raid_finish; do
    [ -n "$raid_name" ] || continue
    software_raid_arrays=$((software_raid_arrays + 1))
    [ "$raid_degraded" = "1" ] && software_raid_degraded=1
    if [ "$raid_rebuild" = "1" ]; then
      software_raid_rebuild_active=1
      [ -z "$software_raid_rebuild_progress" ] && software_raid_rebuild_progress=$raid_progress
      [ -z "$software_raid_rebuild_remaining_minutes" ] && software_raid_rebuild_remaining_minutes=$raid_finish
    fi
    raid_name_json=$(json_escape "$raid_name")
    raid_state_json=$(json_escape "$raid_state")
    raid_level_json=$(json_escape "$raid_level")
    raid_members_json=$(json_escape "$raid_members")
    raid_progress_json=$(number_or_null "$raid_progress")
    raid_finish_json=$(number_or_null "$raid_finish")
    raid_entries="$raid_entries{\"name\":\"$raid_name_json\",\"state\":\"$raid_state_json\",\"level\":\"$raid_level_json\",\"members\":\"$raid_members_json\",\"degraded\":$raid_degraded,\"rebuild_active\":$raid_rebuild,\"rebuild_progress\":$raid_progress_json,\"rebuild_remaining_minutes\":$raid_finish_json},"
  done < <(printf '%s\n' "$raid_lines")
  [ -n "$raid_entries" ] && raid_arrays_json="[${raid_entries%,}]"
  return 0
}

read_storage_health() {
  storage_devices_json="[]"
  raid_details_json="[]"
  storage_tools_available=0
  storage_stats_complete=1
  storage_stats_partial=0
  storage_devices_seen=0
  storage_devices_collected=0
  storage_device_errors=0
  storage_entries=""

  for block_path in /sys/block/*; do
    [ -e "$block_path" ] || continue
    device_name=$(basename "$block_path")
    case "$device_name" in
      loop*|ram*|zram*|md*|dm-*) continue ;;
    esac
    storage_devices_seen=$((storage_devices_seen + 1))
    device_path="/dev/$device_name"
    model=$(cat "$block_path/device/model" 2>/dev/null | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' || echo "")
    serial=$(cat "$block_path/device/serial" 2>/dev/null | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' || echo "")
    protocol="smart"
    smart_output=""
    smart_command_status=127

    if command -v smartctl >/dev/null 2>&1; then
      storage_tools_available=1
      set +e
      smart_output=$(run_limited "$storage_timeout" smartctl -a "$device_path" 2>&1)
      smart_command_status=$?
      if ! printf '%s\n' "$smart_output" | grep -Eq 'SMART support is|SMART overall-health|SMART Health Status|SMART Attributes Data Structure|NVMe|Critical Warning|Temperature:|Device Model:|Model Number:'; then
        if command -v sudo >/dev/null 2>&1; then
          smart_output=$(run_limited "$storage_timeout" sudo -n smartctl -a "$device_path" 2>&1)
          smart_command_status=$?
        fi
      fi
      set -e
    elif [[ "$device_name" == nvme* ]] && command -v nvme >/dev/null 2>&1; then
      storage_tools_available=1
      protocol="nvme"
      set +e
      smart_output=$(run_limited "$storage_timeout" nvme smart-log "$device_path" 2>&1)
      smart_command_status=$?
      if [ "$smart_command_status" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
        smart_output=$(run_limited "$storage_timeout" sudo -n nvme smart-log "$device_path" 2>&1)
        smart_command_status=$?
      fi
      set -e
    fi

    if ! printf '%s\n' "$smart_output" | grep -Eq 'SMART support is|SMART overall-health|SMART Health Status|SMART Attributes Data Structure|NVMe|Critical Warning|critical_warning|Temperature:|temperature[[:space:]]*:|percentage_used|media_errors|Device Model:|Model Number:'; then
      if [ "$smart_command_status" -ne 127 ]; then
        storage_device_errors=$((storage_device_errors + 1))
        storage_stats_partial=1
      fi
      continue
    fi
    storage_devices_collected=$((storage_devices_collected + 1))
    smart_status="unknown"
    temperature=""
    wear_percent=""
    media_errors=""
    reallocated_sectors=""
    pending_sectors=""
    uncorrectable_sectors=""
    power_on_hours=""
    if [ -n "$smart_output" ]; then
      smart_health=$(printf '%s\n' "$smart_output" | awk -F: '/SMART overall-health self-assessment test result|SMART Health Status/ {sub(/^[[:space:]]*/, "", $2); print $2; exit}')
      critical_warning=$(printf '%s\n' "$smart_output" | awk -F: '/^critical_warning|^Critical Warning/ {gsub(/[[:space:]]/, "", $2); print $2; exit}')
      case "$smart_health" in
        *PASSED*|*Passed*|*OK*) smart_status="passed" ;;
        *FAILED*|*Failed*|*BAD*) smart_status="failed" ;;
      esac
      case "$critical_warning" in
        0|0x0|0x00) [ "$smart_status" = "unknown" ] && smart_status="passed" ;;
        "") ;;
        *) smart_status="failed" ;;
      esac
      temperature=$(printf '%s\n' "$smart_output" | awk '
        /Temperature_Celsius|Airflow_Temperature_Cel/ {print $NF; exit}
        /^Temperature:|^temperature[[:space:]]*:/ {for(i=2;i<=NF;i++) if($i ~ /^[0-9]+([.][0-9]+)?$/) {print $i; exit}}
        /^Current Drive Temperature:/ {for(i=4;i<=NF;i++) if($i ~ /^[0-9]+$/) {print $i; exit}}
      ' | head -n 1)
      wear_percent=$(printf '%s\n' "$smart_output" | awk -F: '/^Percentage Used|^percentage_used/ {gsub(/[^0-9.]/, "", $2); print $2; exit}')
      if [ -z "$wear_percent" ]; then
        wear_percent=$(printf '%s\n' "$smart_output" | awk '
          $2 ~ /^(Media_Wearout_Indicator|Percent_Lifetime_Remain|SSD_Life_Left)$/ && $4 ~ /^[0-9]+$/ {
            used=100-$4
            if (used < 0) used=0
            if (used > 100) used=100
            print used
            exit
          }
        ')
      fi
      media_errors=$(printf '%s\n' "$smart_output" | awk -F: '/^Media and Data Integrity Errors|^media_errors/ {gsub(/[^0-9]/, "", $2); print $2; exit}')
      reallocated_sectors=$(printf '%s\n' "$smart_output" | awk '/Reallocated_Sector_Ct/ {print $NF; exit}')
      pending_sectors=$(printf '%s\n' "$smart_output" | awk '/Current_Pending_Sector/ {print $NF; exit}')
      uncorrectable_sectors=$(printf '%s\n' "$smart_output" | awk '/Offline_Uncorrectable/ {print $NF; exit}')
      power_on_hours=$(printf '%s\n' "$smart_output" | awk '
        /Power_On_Hours/ {print $NF; exit}
        /^Power On Hours:|^power_on_hours[[:space:]]*:/ {for(i=NF;i>=2;i--) if($i ~ /^[0-9]+$/) {print $i; exit}}
      ' | head -n 1)
      [ -z "$model" ] && model=$(printf '%s\n' "$smart_output" | awk -F: '/^(Device Model|Model Number):/ {sub(/^[[:space:]]*/, "", $2); print $2; exit}')
      [ -z "$serial" ] && serial=$(printf '%s\n' "$smart_output" | awk -F: '/^Serial Number:/ {sub(/^[[:space:]]*/, "", $2); print $2; exit}')
    fi

    temperature=${temperature//[^0-9.]/}
    wear_percent=${wear_percent//[^0-9.]/}
    media_errors=${media_errors//[^0-9]/}
    reallocated_sectors=${reallocated_sectors//[^0-9]/}
    pending_sectors=${pending_sectors//[^0-9]/}
    uncorrectable_sectors=${uncorrectable_sectors//[^0-9]/}
    power_on_hours=${power_on_hours//[^0-9]/}
    device_name_json=$(json_escape "$device_name")
    device_path_json=$(json_escape "$device_path")
    model_json=$(json_escape "$model")
    serial_json=$(json_escape "$serial")
    protocol_json=$(json_escape "$protocol")
    smart_status_json=$(json_escape "$smart_status")
    storage_entries="$storage_entries{\"name\":\"$device_name_json\",\"path\":\"$device_path_json\",\"model\":\"$model_json\",\"serial\":\"$serial_json\",\"protocol\":\"$protocol_json\",\"smart_status\":\"$smart_status_json\",\"temperature\":$(number_or_null "$temperature"),\"wear_percent\":$(number_or_null "$wear_percent"),\"media_errors\":$(number_or_null "$media_errors"),\"reallocated_sectors\":$(number_or_null "$reallocated_sectors"),\"pending_sectors\":$(number_or_null "$pending_sectors"),\"uncorrectable_sectors\":$(number_or_null "$uncorrectable_sectors"),\"power_on_hours\":$(number_or_null "$power_on_hours")},"
  done
  [ -n "$storage_entries" ] && storage_devices_json="[${storage_entries%,}]"

  if command -v mdadm >/dev/null 2>&1; then
    raid_detail_entries=""
    for md_sys_path in /sys/block/md*; do
      [ -e "$md_sys_path" ] || continue
      md_name=$(basename "$md_sys_path")
      md_path="/dev/$md_name"
      [ -e "$md_path" ] || continue
      set +e
      md_detail=$(run_limited "$storage_timeout" mdadm --detail --export "$md_path" 2>/dev/null)
      if [ -z "$md_detail" ] && command -v sudo >/dev/null 2>&1; then
        md_detail=$(run_limited "$storage_timeout" sudo -n mdadm --detail --export "$md_path" 2>/dev/null)
      fi
      set -e
      [ -n "$md_detail" ] || continue
      md_state=$(printf '%s\n' "$md_detail" | awk -F= '$1=="MD_STATE" {print $2; exit}')
      md_level=$(printf '%s\n' "$md_detail" | awk -F= '$1=="MD_LEVEL" {print $2; exit}')
      md_devices=$(printf '%s\n' "$md_detail" | awk -F= '$1=="MD_DEVICES" {print $2; exit}')
      md_active=$(printf '%s\n' "$md_detail" | awk -F= '$1=="MD_ACTIVE_DEVICES" {print $2; exit}')
      md_failed=$(printf '%s\n' "$md_detail" | awk -F= '$1=="MD_FAILED_DEVICES" {print $2; exit}')
      raid_detail_entries="$raid_detail_entries{\"name\":\"$(json_escape "$md_name")\",\"state\":\"$(json_escape "$md_state")\",\"level\":\"$(json_escape "$md_level")\",\"devices\":$(number_or_null "$md_devices"),\"active_devices\":$(number_or_null "$md_active"),\"failed_devices\":$(number_or_null "$md_failed")},"
    done
    [ -n "$raid_detail_entries" ] && raid_details_json="[${raid_detail_entries%,}]"
  fi
  return 0
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
  process_total_json=$(number_or_null "$process_total")
  process_running_json=$(number_or_null "$process_running")
  process_zombies_json=$(number_or_null "$process_zombies")
  tcp_established_json=$(number_or_null "$tcp_established")
  tcp_time_wait_json=$(number_or_null "$tcp_time_wait")
  sockets_used_json=$(number_or_null "$sockets_used")
  tcp_sockets_in_use_json=$(number_or_null "$tcp_sockets_in_use")
  conntrack_count_json=$(number_or_null "$conntrack_count")
  conntrack_max_json=$(number_or_null "$conntrack_max")
  software_raid_arrays_json=$(number_or_null "$software_raid_arrays")
  software_raid_degraded_json=$(number_or_null "$software_raid_degraded")
  software_raid_rebuild_active_json=$(number_or_null "$software_raid_rebuild_active")
  software_raid_rebuild_progress_json=$(number_or_null "$software_raid_rebuild_progress")
  software_raid_rebuild_remaining_minutes_json=$(number_or_null "$software_raid_rebuild_remaining_minutes")
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
  docker_stats_partial_json=$(number_or_null "$docker_stats_partial")
  printf '{"docker":%s,"containers":"%s","container_stats":%s,"docker_stats_complete":%s,"docker_stats_partial":%s,"docker_images_size_bytes":%s,"docker_containers_size_bytes":%s,"docker_volumes_size_bytes":%s,"docker_build_cache_size_bytes":%s}\n' \
    "$docker_json" "$containers_json" "$container_stats_json" "$docker_stats_complete_json" "$docker_stats_partial_json" \
    "$(number_or_null "$docker_images_size_bytes")" "$(number_or_null "$docker_containers_size_bytes")" "$(number_or_null "$docker_volumes_size_bytes")" "$(number_or_null "$docker_build_cache_size_bytes")"
}

print_storage_json() {
  printf '{"storage_devices":%s,"raid_details":%s,"storage_tools_available":%s,"storage_stats_complete":%s,"storage_stats_partial":%s,"storage_devices_seen":%s,"storage_devices_collected":%s,"storage_device_errors":%s}\n' \
    "$storage_devices_json" "$raid_details_json" "$storage_tools_available" "$storage_stats_complete" "$storage_stats_partial" \
    "$storage_devices_seen" "$storage_devices_collected" "$storage_device_errors"
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
  docker_stats_partial=""
  docker_images_size_bytes=""
  docker_containers_size_bytes=""
  docker_volumes_size_bytes=""
  docker_build_cache_size_bytes=""
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
  storage)
    read_storage_health
    print_storage_json
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
read_process_state
read_connection_metrics
read_software_raid
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

printf '{"cpu":%s,"mem":%s,"disk":%s,"disk_capacity_total":%s,"disk_stats":%s,"uptime":%s,"temp":%s,"rx":%s,"tx":%s,"ram":%s,"cores":%s,"load_1":%s,"load_5":%s,"load_15":%s,"cpu_freq":%s,"os":"%s","pkg_count":%s,"pkg_list":"%s","docker":%s,"containers":"%s","container_stats":%s,"mac_address":"%s","mac_addresses":%s,"top_processes":%s,"process_total":%s,"process_running":%s,"process_zombies":%s,"tcp_established":%s,"tcp_time_wait":%s,"sockets_used":%s,"tcp_sockets_in_use":%s,"conntrack_count":%s,"conntrack_max":%s,"software_raid_arrays":%s,"software_raid_degraded":%s,"software_raid_rebuild_active":%s,"software_raid_rebuild_progress":%s,"software_raid_rebuild_remaining_minutes":%s,"raid_arrays":%s,"vnc":"%s","web":"%s","ssh":"%s","power_w":%s,"energy_uj":%s,"energy_range_uj":%s,"swap_usage":%s,"swap_total":%s,"reboot_required":%s,"security_updates":%s,"last_boot":"%s","kernel_version":"%s","primary_ip":"%s","failed_systemd_units":%s,"failed_systemd_units_list":%s,"journal_errors":%s,"root_fs_readonly":%s,"disk_read_bytes":%s,"disk_write_bytes":%s}\n' \
  "$cpu_json" "$mem_json" "$disk_json" "$disk_total_bytes_json" "$disk_stats_json" "$uptime_json" "$temp_json" "$rx_json" "$tx_json" "$ram_json" "$cores_json" "$load_1_json" \
  "$load_5_json" "$load_15_json" "$cpu_freq_json" "$os_json" "$pkg_count_json" "$pkg_list_json" "$docker_json" "$containers_json" "$container_stats_json" \
  "$mac_address_json" "$mac_addresses_json" "$top_processes_json" "$process_total_json" "$process_running_json" "$process_zombies_json" \
  "$tcp_established_json" "$tcp_time_wait_json" "$sockets_used_json" "$tcp_sockets_in_use_json" "$conntrack_count_json" "$conntrack_max_json" \
  "$software_raid_arrays_json" "$software_raid_degraded_json" "$software_raid_rebuild_active_json" "$software_raid_rebuild_progress_json" "$software_raid_rebuild_remaining_minutes_json" "$raid_arrays_json" \
  "$vnc" "$web" "$ssh_enabled" "$power_w_json" "$energy_counter_json" "$energy_range_json" "$swap_usage_json" "$swap_total_json" \
  "$reboot_required_json" "$security_updates_json" "$last_boot_json" "$kernel_version_json" "$primary_ip_json" "$failed_systemd_units_json_count" "$failed_systemd_units_json" \
  "$journal_errors_json" "$root_fs_readonly_json" "$disk_read_bytes_json" "$disk_write_bytes_json"
'''
