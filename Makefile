# Eidolon Makefile — one entrypoint for install/run/test/build.
# Config comes from .env (copied from .env.example on `make install`).

-include .env
export

API_HOST      ?= $(or $(EIDOLON_API_HOST),127.0.0.1)
API_PORT      ?= $(or $(EIDOLON_API_PORT),26881)
WEB_PORT      ?= $(or $(EIDOLON_WEB_PORT),26880)
RUNTIME_MODE  ?= $(or $(EIDOLON_RUNTIME_MODE),mock)
INV_ARGS      ?=   # 传给 make dev-inventory 的参数，如 INV_ARGS="--format json"

SERVER_DIR    := apps/server
WEB_DIR       := apps/web
RUN_DIR       := .run
DEVCTL        := $(CURDIR)/scripts/devctl.sh

# Python 工具链：后端跑在**本地 conda 环境**里（默认名 eidolon），仓库内不建 .venv。
# 环境来源显式化才有意义：conda 环境由 `make install` 按 conda-forge + Python 版本
# 创建，与 CI 用同一份 requirements.lock；仓库内的 .venv 既不进版本控制，
# 也不会告诉新同学它的解释器版本 —— 本地 3.14 / CI 3.12 的差异就是这么漏出来的。
CONDA         ?= $(shell command -v conda 2>/dev/null)
CONDA_BASE    := $(if $(CONDA),$(shell $(CONDA) info --base 2>/dev/null),)
CONDA_ENV     ?= eidolon
CONDA_PY      ?= 3.12   # 与 CI 的 python-version 对齐（.github/workflows/ci.yml）
PYBIN         := $(CONDA_BASE)/envs/$(CONDA_ENV)/bin/python
PIPBIN        := $(PYBIN) -m pip
RUFFBIN       := $(PYBIN) -m ruff
UVICORNBIN    := $(PYBIN) -m uvicorn
ALEMBICBIN    := $(PYBIN) -m alembic

.DEFAULT_GOAL := help

## help: 显示所有可用命令
.PHONY: help
help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/^## //' | column -t -s ':'

## install: 安装前后端全部依赖（conda 环境 + pnpm）
.PHONY: install
install: check-tools install-server install-web
	@[ -f .env ] || { cp .env.example .env; echo "created .env from .env.example"; }
	@echo "install done. conda env: $(CONDA_ENV) ($(PYBIN))"

.PHONY: check-tools
check-tools:
	@command -v conda >/dev/null || { echo "ERROR: conda not found. Install Miniconda (https://docs.conda.io/en/latest/miniconda.html) or pass CONDA=/path/to/conda"; exit 1; }
	@command -v pnpm >/dev/null || { echo "ERROR: pnpm not found. Install: npm i -g pnpm (or https://pnpm.io/installation)"; exit 1; }

# 所有需要解释器的目标都依赖它：环境缺失时给出一行可执行的修复命令，
# 而不是让 uvicorn/ruff 以 "no such file or directory" 的形式失败。
.PHONY: check-env
check-env: check-tools
	@[ -x $(PYBIN) ] || { echo "ERROR: conda env '$(CONDA_ENV)' missing ($(PYBIN)). Run: make install-server"; exit 1; }

.PHONY: install-server
install-server: check-tools
	# --override-channels -c conda-forge：绕开 Anaconda defaults 通道的 ToS 拦截
	# （未 `conda tos accept` 时，conda create 会直接报错退出）。
	@[ -x $(PYBIN) ] || $(CONDA) create -y -n $(CONDA_ENV) --override-channels -c conda-forge python=$(CONDA_PY)
	$(PIPBIN) install -r $(SERVER_DIR)/requirements.lock
	$(PIPBIN) install -e '$(SERVER_DIR)' --no-deps

## lock-server: 用当前 conda 环境的实测版本重写后端 requirements.lock
.PHONY: lock-server
lock-server: check-env
	@$(PIPBIN) freeze --exclude-editable | grep -Ev '^-e | @ ' | sort -f > $(SERVER_DIR)/requirements.lock
	@git diff --stat -- $(SERVER_DIR)/requirements.lock | tail -1
	@echo "已重写 $(SERVER_DIR)/requirements.lock（检查 diff 后一起提交）"

.PHONY: install-web
install-web:
	# 与 CI 完全一致：lock 与 package.json 不一致就当场报错，而不是默默改 lock。
	cd $(WEB_DIR) && pnpm install --frozen-lockfile

## run: 一次启动 Frontend + Backend（后台运行，日志在 .run/）
.PHONY: run
run: dev

## dev: 同 run
.PHONY: dev
dev: check-env stop
	@mkdir -p $(RUN_DIR)
	@cd $(SERVER_DIR) && nohup $(UVICORNBIN) app.main:app --host $(API_HOST) --port $(API_PORT) > $(CURDIR)/$(RUN_DIR)/server.log 2>&1 & echo $$! > $(CURDIR)/$(RUN_DIR)/server.pid
	@cd $(WEB_DIR) && nohup pnpm dev > $(CURDIR)/$(RUN_DIR)/web.log 2>&1 & echo $$! > $(CURDIR)/$(RUN_DIR)/web.pid
	@failed=""; \
	 up=0; for i in $$(seq 1 30); do curl -sf -o /dev/null "http://127.0.0.1:$(API_PORT)/health" && { up=1; break; }; sleep 1; done; \
	 [ "$$up" = 1 ] || failed="$$failed backend(:$(API_PORT))"; \
	 up=0; for i in $$(seq 1 30); do lsof -nP -tiTCP:$(WEB_PORT) -sTCP:LISTEN >/dev/null 2>&1 && { up=1; break; }; sleep 1; done; \
	 [ "$$up" = 1 ] || failed="$$failed web(:$(WEB_PORT))"; \
	 if [ -n "$$failed" ]; then \
	   echo "ERROR: Eidolon did not come up — not listening:$$failed"; \
	   echo "--- $(RUN_DIR)/server.log (tail) ---"; tail -n 15 $(RUN_DIR)/server.log 2>/dev/null; \
	   echo "--- $(RUN_DIR)/web.log (tail) ---"; tail -n 15 $(RUN_DIR)/web.log 2>/dev/null; \
	   echo "hint: 'make stop' force-clears the whole process tree; 'make ps' lists strays"; \
	   exit 1; \
	 fi; \
	 for spec in "server $(API_PORT)" "web $(WEB_PORT)"; do \
	   set -- $$spec; holder=$$(lsof -nP -tiTCP:$$2 -sTCP:LISTEN 2>/dev/null | head -1); \
	   [ -n "$$holder" ] && echo $$holder > $(RUN_DIR)/$$1.pid; \
	   true; \
	 done
	@echo "Eidolon is running:"
	@echo "  Eidolon Web     http://localhost:$(WEB_PORT)"
	@echo "  Network Access  http://<server-ip>:$(WEB_PORT)"
	@echo "  Backend API     $(API_HOST):$(API_PORT) (proxied via Web at /api, /ws — normally not needed directly)"
	@echo "  Runtime Mode    $(RUNTIME_MODE)"
	@echo "  Logs:           $(RUN_DIR)/server.log, $(RUN_DIR)/web.log"
	@echo "  Stop:           make stop"

## stop: 停止开发进程（进程树 + 端口双路清理，详见 scripts/devctl.sh）
.PHONY: stop
stop:
	@EIDOLON_API_PORT=$(API_PORT) EIDOLON_WEB_PORT=$(WEB_PORT) sh $(DEVCTL) stop

## ps: 列出本仓库所有 dev 进程（包括漂移到其他端口的孤儿）
.PHONY: ps
ps:
	@EIDOLON_API_PORT=$(API_PORT) EIDOLON_WEB_PORT=$(WEB_PORT) sh $(DEVCTL) ps

## status: 只看 26881 / 26880 是否有健在的服务
.PHONY: status
status:
	@EIDOLON_API_PORT=$(API_PORT) EIDOLON_WEB_PORT=$(WEB_PORT) sh $(DEVCTL) status

## dev-inventory: **只读**清点开发库（按公司口径；不删不改。JSON 用 INV_ARGS="--format json"）
.PHONY: dev-inventory
dev-inventory: check-env
	@cd $(SERVER_DIR) && $(PYBIN) $(CURDIR)/scripts/dev_inventory.py $(INV_ARGS)

## dev-cleanup-plan: **只读**清理规划（dry-run；必须显式传 COMPANY_IDS，如 COMPANY_IDS="1 3"；不删不改）
.PHONY: dev-cleanup-plan
dev-cleanup-plan: check-env
	@[ -n "$(COMPANY_IDS)" ] || { echo "ERROR: dev-cleanup-plan 必须显式传 COMPANY_IDS（如 make dev-cleanup-plan COMPANY_IDS=\"1 3\"）—— 拒绝默认全库"; exit 2; }
	@cd $(SERVER_DIR) && $(PYBIN) $(CURDIR)/scripts/dev_cleanup_plan.py --company $(COMPANY_IDS) $(CLEANUP_ARGS)

## logs: 跟随前后端日志（Ctrl-C 退出）
.PHONY: logs
logs:
	@tail -n 40 -f $(RUN_DIR)/server.log $(RUN_DIR)/web.log

## restart: 重启全部服务
.PHONY: restart
restart: stop dev

## test: 后端 pytest + 前端 vitest
.PHONY: test
test: test-server test-web

.PHONY: test-server
test-server: check-env
	$(PYBIN) -m pytest $(SERVER_DIR)/tests -q

## test-integration: 真实 Docker 集成测试（需要本机 Docker daemon）
.PHONY: test-integration
test-integration: check-env
	$(PYBIN) -m pytest $(SERVER_DIR)/tests/integration -m integration -q

## migrate: 把数据库升到最新 schema（改了 model 后、重启 dev 服务前必跑）
.PHONY: migrate
migrate: check-env
	@cd $(SERVER_DIR) && $(ALEMBICBIN) upgrade head && $(ALEMBICBIN) current

## migrate-new: 按 model 变更自动生成一个 revision（仍需人工 review）
.PHONY: migrate-new
migrate-new: check-env
	@cd $(SERVER_DIR) && $(ALEMBICBIN) revision --autogenerate -m "$(or $(M),autogen)"
	@echo "生成后请人工检查 $(SERVER_DIR)/migrations/versions/ 里的 upgrade/downgrade"

## runtime-status: 通过 API 列出 runtime instances
.PHONY: runtime-status
runtime-status:
	@curl -sf "http://localhost:$(WEB_PORT)/api/v1/runtimes" || curl -sf "http://$(API_HOST):$(API_PORT)/api/v1/runtimes"

## runtime-list: 列出 eidolon-* 容器
.PHONY: runtime-list
runtime-list:
	@docker ps -a --filter "name=eidolon-" --format "table {{.Names}}\t{{.Image}}\t{{.Status}}"

## runtime-pull: 拉取 hermes/openclaw 运行时镜像
.PHONY: runtime-pull
runtime-pull:
	docker pull $(or $(EIDOLON_HERMES_IMAGE),nousresearch/hermes-agent:latest)
	docker pull $(or $(EIDOLON_OPENCLAW_IMAGE),ghcr.io/openclaw/openclaw:latest)

## runtime-clean: 删除所有 eidolon-* 容器（不动数据目录）
.PHONY: runtime-clean
runtime-clean:
	@docker ps -aq --filter "name=eidolon-" | xargs -r docker rm -f

.PHONY: test-web
test-web:
	cd $(WEB_DIR) && pnpm exec vitest run

## lint: ruff + prettier + eslint + tsc
.PHONY: lint
lint: check-env
	$(RUFFBIN) check $(SERVER_DIR)/app $(SERVER_DIR)/tests
	$(RUFFBIN) format --check $(SERVER_DIR)/app $(SERVER_DIR)/tests
	cd $(WEB_DIR) && pnpm exec tsc --noEmit && pnpm exec eslint .
	cd $(WEB_DIR) && pnpm exec prettier --check .

## format: ruff format + prettier（与 CI / package.json 脚本同一入口：整仓 + .prettierignore）
.PHONY: format
format: check-env
	$(RUFFBIN) format $(SERVER_DIR)/app $(SERVER_DIR)/tests
	cd $(WEB_DIR) && pnpm exec prettier --write .

## build: 前端生产构建
.PHONY: build
build:
	cd $(WEB_DIR) && pnpm build

## dev-seed-user: 注入本地测试账号（默认 **user@example.com / user**；OWNER + 自建 test-co 公司）
##   · 幂等：已存在则重置密码并强制 active/verified；自定义用 SEED_EMAIL= SEED_PASSWORD= SEED_DISPLAY_NAME=
##   · 前置 migrate（清库后**无需先 make run**，直接 make dev-seed-user 即可登录测试）
##   · 只允许本地 SQLite（拒绝外部库）；.env 与其他表不动
.PHONY: dev-seed-user
dev-seed-user: migrate
	@cd $(SERVER_DIR) && EIDOLON_DATABASE_URL="$(or $(EIDOLON_DATABASE_URL),sqlite:///./data/eidolon.db)" $(PYBIN) $(CURDIR)/scripts/dev_seed_user.py $(if $(SEED_EMAIL),--email $(SEED_EMAIL))$(if $(SEED_PASSWORD), --password $(SEED_PASSWORD))$(if $(SEED_DISPLAY_NAME), --display-name $(SEED_DISPLAY_NAME))

## dev-clear-data: 清除**本地开发数据**（apps/server/data 下的 sqlite/workspaces/employees；保留 .env）
##   · 必须先确认：DATA_CONFIRM=yes（或交互输入 y）；DRY_RUN=1 只列出不删除（结尾 NO DATA HAS BEEN MODIFIED.）
##   · 前置 stop（防数据库锁）；若 EIDOLON_DATABASE_URL 指向仓库外 ⇒ 拒绝，避免误删外部库
.PHONY: dev-clear-data
dev-clear-data: stop
	@bash $(CURDIR)/scripts/dev_clear_data.sh --confirm="$(DATA_CONFIRM)" $(if $(filter 1,$(DRY_RUN)),--dry-run)

## clean: 清理构建产物与本地运行数据（保留 .env）
.PHONY: clean
clean: stop
	rm -rf $(RUN_DIR) $(WEB_DIR)/dist $(WEB_DIR)/node_modules/.vite
	rm -rf $(SERVER_DIR)/.pytest_cache $(SERVER_DIR)/.ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
