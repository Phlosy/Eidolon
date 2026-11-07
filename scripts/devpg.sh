#!/usr/bin/env bash
# Local PostgreSQL for Eidolon v2 without Docker: a private cluster under .run/pg on
# port 5433 with user/password eidolon/eidolon and databases eidolon + eidolon_test.
# Matches the default EIDOLON_V2_DATABASE_URL and the docker-compose "postgres" service,
# so tests and dev run identically either way.
#
#   scripts/devpg.sh start|stop|status|reset|psql
#
# Binaries: $PGBIN, else `pg_config --bindir`, else Homebrew postgresql@16/@14.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PGDATA="${EIDOLON_PGDATA:-$ROOT/.run/pg}"
PGPORT="${EIDOLON_PGPORT:-5433}"
PGLOG="$ROOT/.run/pg.log"
# Unix socket dir; some mounted filesystems refuse socket permissions, so allow override.
PGSOCKET="${EIDOLON_PGSOCKET:-$ROOT/.run}"

find_bin() {
  if [[ -n "${PGBIN:-}" && -x "$PGBIN/pg_ctl" ]]; then echo "$PGBIN"; return; fi
  if command -v pg_config >/dev/null 2>&1; then pg_config --bindir; return; fi
  for v in 17 16 15 14; do
    for p in /opt/homebrew/opt/postgresql@$v/bin /usr/local/opt/postgresql@$v/bin /usr/lib/postgresql/$v/bin; do
      [[ -x "$p/pg_ctl" ]] && { echo "$p"; return; }
    done
  done
  echo "ERROR: no PostgreSQL binaries found; set PGBIN or install postgresql@16 (brew) / use \`make pg-up\` (docker)" >&2
  exit 1
}

BIN="$(find_bin)"
mkdir -p "$ROOT/.run"

init() {
  if [[ ! -f "$PGDATA/PG_VERSION" ]]; then
    "$BIN/initdb" -D "$PGDATA" -U eidolon -A trust -E UTF8 --locale=C >/dev/null
    cat >>"$PGDATA/postgresql.conf" <<EOF
port = $PGPORT
listen_addresses = '127.0.0.1'
unix_socket_directories = '$PGSOCKET'
fsync = off
synchronous_commit = off
full_page_writes = off
shared_buffers = 128MB
EOF
  fi
}

running() { "$BIN/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1; }

start() {
  init
  if running; then echo "postgres already running on 127.0.0.1:$PGPORT"; return; fi
  "$BIN/pg_ctl" -D "$PGDATA" -l "$PGLOG" -w start >/dev/null
  local psql="$BIN/psql -h 127.0.0.1 -p $PGPORT -U eidolon -d postgres -qtA"
  $psql -c "ALTER USER eidolon WITH PASSWORD 'eidolon'" >/dev/null
  for db in eidolon eidolon_test; do
    [[ "$($psql -c "SELECT 1 FROM pg_database WHERE datname='$db'")" == "1" ]] || $psql -c "CREATE DATABASE $db" >/dev/null
  done
  echo "postgres ready: postgresql+psycopg://eidolon:eidolon@127.0.0.1:$PGPORT/eidolon"
}

case "${1:-}" in
  start) start ;;
  stop) running && "$BIN/pg_ctl" -D "$PGDATA" -m fast stop >/dev/null; echo "stopped" ;;
  status) running && echo "running on 127.0.0.1:$PGPORT" || { echo "stopped"; exit 1; } ;;
  reset) running && "$BIN/pg_ctl" -D "$PGDATA" -m fast stop >/dev/null; rm -rf "$PGDATA"; start ;;
  psql) shift; exec "$BIN/psql" -h 127.0.0.1 -p "$PGPORT" -U eidolon -d "${1:-eidolon}" ;;
  *) echo "usage: $0 start|stop|status|reset|psql [db]"; exit 2 ;;
esac
