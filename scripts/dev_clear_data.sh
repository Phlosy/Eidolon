#!/usr/bin/env bash
# 清除本地开发数据（sqlite / workspaces / employees）—— 只允许删除**本项目仓库内**
# apps/server/data 下的内容；保留 .env（密钥/配置不动）。
#
# 安全设计（与 devctl.sh 同风格：破坏性路径只允许显式确认）：
#   1. 必须确认：DATA_CONFIRM=yes（CI/一次性）或交互输入 y；否则退出 1，什么都不删。
#   2. --dry-run：列出将要删除的内容，结尾输出 `NO DATA HAS BEEN MODIFIED.`，不删除。
#   3. 拒绝误删：目标必须是仓库内的目录（默认 apps/server/data）；若 .env 里
#      EIDOLON_DATABASE_URL 指向仓库外的数据库，直接拒绝（本地只清 SQLite 文件）。
#   4. 服务先停（由 make dev-clear-data 前置依赖 stop 保证），避免占用锁。
#
# 用法：
#   make dev-clear-data DATA_CONFIRM=yes            # 停服务 → 确认 → 清 apps/server/data
#   make dev-clear-data DRY_RUN=1                   # 只列出将删除的内容（演示/审计）
#   DATA_TARGET=/tmp/scratch bash scripts/dev_clear_data.sh --confirm=yes   # 测试/高级
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_DIR="$ROOT/apps/server"
DEFAULT_TARGET="$SERVER_DIR/data"

CONFIRM=""
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --confirm=*) CONFIRM="${arg#--confirm=}" ;;
  esac
done
DATA_CONFIRM="${DATA_CONFIRM:-$CONFIRM}"
[ "${DRY_RUN:-0}" = "1" ] && DRY_RUN=1

TARGET="${DATA_TARGET:-$DEFAULT_TARGET}"

die() { echo "ERROR: $*" >&2; exit 1; }

# 目标必须落在仓库内（防止 DATA_TARGET=/ 或仓库根这类误删）。
# DEV_CLEAR_DATA_ALLOW_OUTSIDE=1 仅供测试（tmp 目录不在仓库内）；其余守卫不豁免。
if [[ "$TARGET" != "$ROOT"* ]] && [ "${DEV_CLEAR_DATA_ALLOW_OUTSIDE:-0}" != "1" ]; then
  die "DATA_TARGET 必须在仓库内（当前 '$TARGET' 不在 $ROOT 下）—— 拒绝"
fi
[ "$TARGET" = "$ROOT" ] && die "不能清除仓库根目录"
[ "$TARGET" = "$SERVER_DIR" ] && die "不能清除 apps/server 根目录"

# 默认目标时才做数据库防误删：.env 里 EIDOLON_DATABASE_URL 若指向仓库外 ⇒ 拒绝
if [ "$TARGET" = "$DEFAULT_TARGET" ] && [ -f "$ROOT/.env" ]; then
  DB_URL="$(grep -E '^EIDOLON_DATABASE_URL=' "$ROOT/.env" | head -1 | cut -d= -f2-)"
  if [ -n "$DB_URL" ] && [ "$DB_URL" != 'sqlite:///./data/eidolon.db' ]; then
    # 相对 sqlite 路径锚定到 apps/server 后才能算"仓库内"
    if [[ "$DB_URL" != sqlite:///./data/* ]]; then
      die "EIDOLON_DATABASE_URL='$DB_URL' 不在 apps/server/data 下 —— 拒绝清除（本地工具只清 SQLite 文件）"
    fi
  fi
fi

if [ ! -d "$TARGET" ]; then
  echo "数据目录不存在，无需清除：$TARGET"
  exit 0
fi

# 列出将要清除的内容（含隐藏文件）
ENTRIES=()
while IFS= read -r entry; do ENTRIES+=("$entry"); done < <(find "$TARGET" -mindepth 1 -maxdepth 1 | sort)
if [ "${#ENTRIES[@]}" -eq 0 ]; then
  echo "数据目录为空，无需清除：$TARGET"
  exit 0
fi

echo "将清除以下本地开发数据（${TARGET}）："
for entry in "${ENTRIES[@]}"; do
  printf "  - %s\n" "${entry#"$TARGET"/}"
done
echo "（.env 保留：密钥与配置不动）"

if [ "$DRY_RUN" = "1" ]; then
  echo
  echo "NO DATA HAS BEEN MODIFIED."
  exit 0
fi

# 确认门禁：DATA_CONFIRM=yes 或交互输入 y
if [ "$DATA_CONFIRM" != "yes" ]; then
  if [ -t 0 ]; then
    printf '输入 y 确认清除（否则按回车取消）：'
    read -r reply || reply=""
    [ "$reply" = "y" ] || { echo "已取消（未修改任何数据）。"; exit 1; }
  else
    die "必须显式确认：DATA_CONFIRM=yes（或 --confirm=yes）—— 拒绝默认执行"
  fi
fi

find "$TARGET" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
echo "已清除本地开发数据：${TARGET}（.env 保留）"
echo "下次 make run 会重新初始化一个空库。"