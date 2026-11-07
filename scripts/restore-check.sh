#!/usr/bin/env bash
# restore-check.sh — restore a scripts/backup.sh directory into a *scratch* database and
# prove it is whole: hash chains verify, ledgers verify, and every projection rebuilt
# from the restored facts equals what the dump contained.
#
#   scripts/restore-check.sh BACKUP_DIR TARGET_DB_URL [BLOB_DIR]
#
# TARGET_DB_URL must name a database whose name ends in "_restore" (it is dropped and
# recreated). BLOB_DIR (default: a temp dir) receives the blob archive. Needs pg_restore
# + psql locally or in the PostgreSQL container (EIDOLON_PG_CONTAINER / compose "postgres").
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP="${1:?usage: restore-check.sh BACKUP_DIR TARGET_DB_URL [BLOB_DIR]}"
TARGET="${2:?usage: restore-check.sh BACKUP_DIR TARGET_DB_URL [BLOB_DIR]}"
BLOB_DIR="${3:-$(mktemp -d)}"
[[ -f "$BACKUP/db.dump" ]] || { echo "ERROR: $BACKUP/db.dump missing" >&2; exit 1; }
PG_URL="${TARGET/postgresql+psycopg:/postgresql:}"
DBNAME="${PG_URL##*/}"; DBNAME="${DBNAME%%\?*}"
[[ "$DBNAME" == *_restore ]] || { echo "ERROR: target database must end in _restore (got $DBNAME)" >&2; exit 1; }
ADMIN_URL="${PG_URL%/*}/postgres"

run_pg() {  # run_pg <tool> <args...> — locally or inside the container
  if command -v "$1" >/dev/null 2>&1; then "$@"; return; fi
  local container="${EIDOLON_PG_CONTAINER:-}"
  if [[ -z "$container" ]]; then
    container="$(docker compose -f "$ROOT/docker-compose.yml" ps -q postgres 2>/dev/null || true)"
  fi
  [[ -n "$container" ]] || { echo "ERROR: no $1 and no PostgreSQL container (set EIDOLON_PG_CONTAINER)" >&2; exit 1; }
  docker exec -i "$container" "$@"
}
inner() {  # rewrite a URL for use inside the container (server is local there)
  python3 - "$1" <<'PY'
import sys
from urllib.parse import urlsplit, urlunsplit
u = urlsplit(sys.argv[1])
auth = u.username + (":" + u.password if u.password else "")
print(urlunsplit((u.scheme, f"{auth}@127.0.0.1:5432", u.path, u.query, "")))
PY
}
if command -v psql >/dev/null 2>&1; then A="$ADMIN_URL"; T="$PG_URL"; else A="$(inner "$ADMIN_URL")"; T="$(inner "$PG_URL")"; fi

echo "recreating $DBNAME"
run_pg psql --quiet --dbname="$A" -c "DROP DATABASE IF EXISTS \"$DBNAME\";" -c "CREATE DATABASE \"$DBNAME\";"
echo "restoring $BACKUP/db.dump"
if command -v pg_restore >/dev/null 2>&1; then
  pg_restore --no-owner --no-privileges --dbname="$T" "$BACKUP/db.dump"
else
  run_pg pg_restore --no-owner --no-privileges --dbname="$T" < "$BACKUP/db.dump"
fi
if [[ -f "$BACKUP/blobs.tar.gz" ]]; then
  mkdir -p "$BLOB_DIR" && tar -C "$BLOB_DIR" -xzf "$BACKUP/blobs.tar.gz"
  echo "blobs restored to $BLOB_DIR ($(find "$BLOB_DIR" -type f | wc -l | tr -d ' ') files)"
fi
echo "self-check against $DBNAME"
PYBIN="${PYBIN:-$(command -v python)}"
if [[ -n "${CONDA_PREFIX:-}" && -x "$CONDA_PREFIX/bin/python" ]]; then PYBIN="$CONDA_PREFIX/bin/python"; fi
for c in /Users/*/miniconda3/envs/eidolon/bin/python; do [[ -x "$c" ]] && PYBIN="$c"; done
cd "$ROOT/apps/server"
EIDOLON_V2_DATABASE_URL="$TARGET" EIDOLON_V2_TRANSCRIPT_DIR="$BLOB_DIR" "$PYBIN" -m eidolon restore-check
