#!/usr/bin/env bash
# backup.sh — one consistent backup of Eidolon v2: the PostgreSQL database (pg_dump,
# custom format) + the blob store (transcripts, artifacts) + a manifest.
#
#   scripts/backup.sh [OUT_DIR]            default OUT_DIR = backups/<UTC timestamp>
#
# Env: EIDOLON_V2_DATABASE_URL (from .env), EIDOLON_V2_TRANSCRIPT_DIR (default
# apps/server/data/transcripts). pg_dump runs locally when installed, otherwise inside
# the PostgreSQL container (EIDOLON_PG_CONTAINER, default: the compose service
# "postgres"). Restore with scripts/restore-check.sh, which also self-verifies.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# .env may hold unquoted values with spaces, so read keys instead of sourcing it.
env_value() { { grep -E "^$1=" "$ROOT/.env" 2>/dev/null || true; } | tail -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'; }
DB_URL="${EIDOLON_V2_DATABASE_URL:-$(env_value EIDOLON_V2_DATABASE_URL)}"
[[ -n "$DB_URL" ]] || { echo "ERROR: EIDOLON_V2_DATABASE_URL is required (env or .env)" >&2; exit 1; }
BLOBS="${EIDOLON_V2_TRANSCRIPT_DIR:-$(env_value EIDOLON_V2_TRANSCRIPT_DIR)}"
BLOBS="${BLOBS:-data/transcripts}"
[[ "$BLOBS" = /* ]] || BLOBS="$ROOT/apps/server/$BLOBS"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${1:-$ROOT/backups/$STAMP}"
mkdir -p "$OUT"

# SQLAlchemy URL → libpq URL (drop the "+psycopg" driver suffix).
PG_URL="${DB_URL/postgresql+psycopg:/postgresql:}"

dump() {
  if command -v pg_dump >/dev/null 2>&1; then
    pg_dump --format=custom --no-owner --no-privileges --dbname="$PG_URL" --file="$OUT/db.dump"
  else
    local container="${EIDOLON_PG_CONTAINER:-}"
    if [[ -z "$container" ]]; then
      container="$(docker compose -f "$ROOT/docker-compose.yml" ps -q postgres 2>/dev/null || true)"
    fi
    [[ -n "$container" ]] || { echo "ERROR: no pg_dump and no PostgreSQL container (set EIDOLON_PG_CONTAINER)" >&2; exit 1; }
    # inside the container the server is local: rewrite host/port, keep user/password/db
    local inner
    inner="$(python3 - "$PG_URL" <<'PY'
import sys
from urllib.parse import urlsplit, urlunsplit
u = urlsplit(sys.argv[1])
auth = u.username + (":" + u.password if u.password else "")
print(urlunsplit((u.scheme, f"{auth}@127.0.0.1:5432", u.path, u.query, "")))
PY
)"
    docker exec -i "$container" pg_dump --format=custom --no-owner --no-privileges --dbname="$inner" > "$OUT/db.dump"
  fi
}

echo "database  → $OUT/db.dump"
dump
if [[ -d "$BLOBS" ]]; then
  echo "blobs     → $OUT/blobs.tar.gz ($BLOBS)"
  tar -C "$BLOBS" -czf "$OUT/blobs.tar.gz" .
else
  echo "blobs     → (none: $BLOBS does not exist)"
fi
python3 - "$OUT" "$ROOT" "$BLOBS" <<'PY'
import hashlib, json, os, subprocess, sys
out, root, blobs = sys.argv[1:4]
def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
commit = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
manifest = {
    "format": "eidolon-backup/1",
    "created_at": os.path.basename(out),
    "git_commit": commit,
    "files": {
        name: {"sha256": sha(os.path.join(out, name)), "bytes": os.path.getsize(os.path.join(out, name))}
        for name in ("db.dump", "blobs.tar.gz") if os.path.exists(os.path.join(out, name))
    },
    "blob_root": blobs,
    "restore": "scripts/restore-check.sh <this dir> <target database url>",
}
with open(os.path.join(out, "manifest.json"), "w") as f:
    json.dump(manifest, f, indent=2)
print("manifest  →", os.path.join(out, "manifest.json"))
PY
echo "backup complete: $OUT"
