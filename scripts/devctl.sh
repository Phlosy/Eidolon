#!/bin/sh
# devctl.sh — Eidolon 开发进程管理（由 Makefile 调用，也可单独运行）
#
# 为什么需要显式的进程树清理，而不是 kill $(cat .run/*.pid)：
# `nohup … & echo $!` 记录的是**启动器**的 pid，不是真正的服务器：
#
#   /bin/sh -c 'cd apps/server && nohup uv run uvicorn … & echo $! > server.pid'
#     └ uv run                     ← $! 常常是它
#        └ python .venv/bin/uvicorn :26881   ← 真正占端口的人
#   /bin/sh -c 'cd apps/web && nohup pnpm dev … & echo $! > web.pid'
#     └ pnpm dev                   ← $! 是它
#        └ node vite.js :26880     ← 真正占端口的人（孙辈）
#
# 只杀启动器会把真服务变成孤儿继续占端口；下一次 `make run` 的新进程因
# "address already in use"（uvicorn）或 strictPort（vite）退出，却又把
# .run/*.pid 覆写成自己这个已死 pid —— 于是老孤儿永远没人能停掉，浏览器
# 访问到的仍是上一个代码版本。所以这里三路取并集：pid 文件 + 端口占用者 +
# 命令行特征，再做进程树闭包。
#
# 用法: devctl.sh stop | ps | status
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
API_PORT=${EIDOLON_API_PORT:-26881}
WEB_PORT=${EIDOLON_WEB_PORT:-26880}
RUN_DIR="$ROOT/.run"

TABLE=$(mktemp)
trap 'rm -f "$TABLE"' EXIT INT TERM

# pid ppid args…
ps -eo pid=,ppid=,args= >"$TABLE"

port_listeners() {
  # lsof 优先（macOS / 常见发行版）；缺失时退回 iproute2 的 ss。
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null || true
  elif command -v ss >/dev/null 2>&1; then
    ss -ltnp 2>/dev/null | awk -v port="$1" 'BEGIN{FS=" "} { split($4, a, ":"); if (a[length(a)] + 0 == port + 0 && match($0, /pid=[0-9]+/)) print substr($0, RSTART + 4, RLENGTH - 4) }' || true
  else
    echo "devctl: neither lsof nor ss found — port detection disabled" >&2
  fi
}

# 只匹配本仓库的开发进程，绝不误伤别的项目。
pattern_pids() {
  pgrep -f "uvicorn app\.main:app" 2>/dev/null || true
  pgrep -f "$ROOT/apps/web/node_modules/.*bin/vite" 2>/dev/null || true
  pgrep -f "cd apps/server && nohup" 2>/dev/null || true
  pgrep -f "cd apps/web && nohup" 2>/dev/null || true
}

pid_file_pids() {
  for name in server web; do
    [ -f "$RUN_DIR/$name.pid" ] && cat "$RUN_DIR/$name.pid" 2>/dev/null || true
  done
}

# 种子集合 → 进程树闭包（向下收子进程，向上只收我们自己的启动器包装）。
resolve_pids() {
  seeds=$(
    {
      pid_file_pids
      port_listeners "$API_PORT"
      port_listeners "$WEB_PORT"
      pattern_pids
    } | tr ' \n' '\n\n' | grep -E '^[0-9]+$' | sort -u | tr '\n' ' '
  )
  [ -n "$seeds" ] || return 0
  awk -v seeds="$seeds" '
    { pid = $1; ppid = $2; rest = ""; for (i = 3; i <= NF; i++) rest = rest $i " "
      args[pid] = rest; parent[pid] = ppid; alive[pid] = 1 }
    END {
      n = split(seeds, s, " ")
      for (i = 1; i <= n; i++) if (s[i] != "" && alive[s[i]]) keep[s[i]] = 1
      # 启动器特征：make 的 sh -c 包装、pnpm/npm 包装、uv run
      launcher = /^(\/bin\/)?(sh|bash|zsh)( -c)? |(^|\/)(pnpm|npm|yarn|pnpx)( |$)|(^|\/)uv( |$)/
      changed = 1
      while (changed) {
        changed = 0
        delete pending
        for (p in keep) pending[p] = 1
        for (p in pending) {
          # 向下：全部子进程
          for (c in alive) if (parent[c] == p && !(c in keep)) { keep[c] = 1; changed = 1 }
          # 向上：仅当父进程是我们的启动器包装时一起杀，避免误杀 make/终端
          pp = parent[p]
          if (pp != "" && pp != "1" && !(pp in keep) && alive[pp] && args[pp] ~ launcher) {
            keep[pp] = 1; changed = 1
          }
        }
      }
      for (p in keep) print p
    }
  ' "$TABLE" | sort -n | tr '\n' ' '
}

do_stop() {
  dry=0
  if [ "${EIDOLON_DEVCTL_DRY_RUN:-0}" = "1" ]; then dry=1; fi
  pids=$(resolve_pids | tr ' ' '\n' | grep -E '^[0-9]+$' | sort -u | tr '\n' ' ' || true)
  pids=$(echo "$pids")
  if [ -n "$pids" ]; then
    if [ $dry -eq 1 ]; then
      echo "dry-run: would stop: $pids"
    else
      echo "stopped: $pids"
      # shellcheck disable=SC2086
      kill $pids 2>/dev/null || true
      i=0
      while [ $i -lt 10 ]; do
        alive=0
        for p in $pids; do kill -0 "$p" 2>/dev/null && alive=1; done
        [ $alive -eq 0 ] && break
        sleep 1
        i=$((i + 1))
      done
      for p in $pids; do kill -9 "$p" 2>/dev/null || true; done
      sleep 1
    fi
  else
    echo "nothing running"
  fi
  if [ $dry -eq 0 ]; then rm -f "$RUN_DIR/server.pid" "$RUN_DIR/web.pid"; fi

  stale=""
  for port in "$API_PORT" "$WEB_PORT"; do
    holders=$(port_listeners "$port" | tr '\n' ' ')
    holders=$(echo "$holders")
    [ -n "$holders" ] && stale="$stale :$port held by $holders"
  done
  if [ -n "$stale" ]; then
    if [ $dry -eq 1 ]; then
      echo "dry-run: listeners left on$stale"
    else
      echo "ERROR: refusing to leave listeners behind:$stale"
      do_ps
      exit 1
    fi
  else
    echo "ports :$API_PORT :$WEB_PORT are free"
  fi
}

do_ps() {
  pids=$(resolve_pids | tr ' ' '\n' | grep -E '^[0-9]+$' | sort -u || true)
  if [ -z "$pids" ]; then
    echo "no Eidolon dev processes running"
    return 0
  fi
  echo "$pids" | tr '\n' ' ' | tr ' ' '\n' | grep -E '^[0-9]+$' >"$TABLE.pids"
  awk '
    NR == FNR { want[$1] = 1; next }
    { pid = $1; if (!(pid in want)) next; rest = ""
      for (i = 3; i <= NF; i++) rest = rest $i " "
      printf "  pid=%-7s ppid=%-7s %s\n", pid, $2, substr(rest, 1, 96) }
  ' "$TABLE.pids" "$TABLE"
  rm -f "$TABLE.pids"
  for port in "$API_PORT" "$WEB_PORT"; do
    holders=$(port_listeners "$port" | tr '\n' ' ')
    holders=$(echo "$holders")
    printf '  :%s -> %s\n' "$port" "${holders:-(no listener)}"
  done
}

do_status() {
  for spec in "backend $API_PORT" "web $WEB_PORT"; do
    set -- $spec
    holder=$(port_listeners "$2" | head -1)
    if [ -n "$holder" ]; then
      printf '%-8s :%s up (pid %s)\n' "$1" "$2" "$holder"
    else
      printf '%-8s :%s DOWN\n' "$1" "$2"
    fi
  done
  count=$(resolve_pids | tr ' ' '\n' | grep -E '^[0-9]+$' | sort -u | wc -l | tr -d ' ')
  echo "tracked processes: $count (run 'make ps' to list them)"
}

case ${1:-} in
  stop) do_stop ;;
  ps) do_ps ;;
  status) do_status ;;
  *) echo "usage: $0 {stop|ps|status}" >&2; exit 2 ;;
esac
