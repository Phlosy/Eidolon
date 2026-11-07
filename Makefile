# Eidolon Makefile — one entrypoint for install/run/test/build.
# Config comes from .env (copied from .env.example on `make install`).

-include .env
export

API_HOST      ?= $(or $(EIDOLON_V2_API_HOST),127.0.0.1)
API_PORT      ?= $(or $(EIDOLON_V2_API_PORT),26881)
WEB_PORT      ?= $(or $(EIDOLON_WEB_PORT),26880)

SERVER_DIR    := apps/server
WEB_DIR       := apps/web
RUN_DIR       := .run
DEVCTL        := $(CURDIR)/scripts/devctl.sh

# Python 工具链：后端跑在**本地 conda 环境**里（默认名 eidolon），仓库内不建 .venv。
# conda 环境由 `make install` 按 conda-forge + Python 版本创建，与 CI 用同一份 requirements.lock。
CONDA         ?= $(shell command -v conda 2>/dev/null)
CONDA_BASE    := $(if $(CONDA),$(shell $(CONDA) info --base 2>/dev/null),)
CONDA_ENV     ?= eidolon
CONDA_PY      ?= 3.12   # 与 CI 的 python-version 对齐（.github/workflows/ci.yml）
PYBIN         := $(CONDA_BASE)/envs/$(CONDA_ENV)/bin/python
PIPBIN        := $(PYBIN) -m pip
RUFFBIN       := $(PYBIN) -m ruff
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

# 所有需要解释器的目标都依赖它：环境缺失时给出一行可执行的修复命令。
.PHONY: check-env
check-env: check-tools
	@[ -x $(PYBIN) ] || { echo "ERROR: conda env '$(CONDA_ENV)' missing ($(PYBIN)). Run: make install-server"; exit 1; }

.PHONY: install-server
install-server: check-tools
	# --override-channels -c conda-forge：绕开 Anaconda defaults 通道的 ToS 拦截。
	@[ -x $(PYBIN) ] || $(CONDA) create -y -n $(CONDA_ENV) --override-channels -c conda-forge python=$(CONDA_PY)
	$(PIPBIN) install -r $(SERVER_DIR)/requirements.lock
	$(PIPBIN) install -e '$(SERVER_DIR)' --no-deps

## lock-server: 用当前 conda 环境的实测版本重写后端 requirements.lock（只保留 v2 真正 import 的包；检查 diff 再提交）
.PHONY: lock-server
lock-server: check-env
	@$(PIPBIN) freeze --exclude-editable | grep -Ev '^-e | @ ' | sort -f > $(SERVER_DIR)/requirements.lock
	@git diff --stat -- $(SERVER_DIR)/requirements.lock | tail -1
	@echo "已重写 $(SERVER_DIR)/requirements.lock（检查 diff 后一起提交）"

.PHONY: install-web
install-web:
	# 与 CI 完全一致：lock 与 package.json 不一致就当场报错，而不是默默改 lock。
	cd $(WEB_DIR) && pnpm install --frozen-lockfile

## pg-up: 启动 PostgreSQL（docker compose；无 Docker 用 scripts/devpg.sh start）
.PHONY: pg-up
pg-up:
	docker compose up -d --wait postgres

## pg-down: 停止 PostgreSQL 容器
.PHONY: pg-down
pg-down:
	docker compose stop postgres

## migrate-v2: 数据库升到最新（alembic-v2.ini）
.PHONY: migrate-v2
migrate-v2: check-env
	@cd $(SERVER_DIR) && $(ALEMBICBIN) -c alembic-v2.ini upgrade head && $(ALEMBICBIN) -c alembic-v2.ini current

## migrate-v2-new: 按 model 变更生成一个 revision（仍需人工检查；新表先报批）
.PHONY: migrate-v2-new
migrate-v2-new: check-env
	@cd $(SERVER_DIR) && $(ALEMBICBIN) -c alembic-v2.ini revision --autogenerate -m "$(or $(M),autogen)"

## run: 一次启动 API + worker + Web（后台运行，日志在 .run/；需要 PostgreSQL 已就绪并 migrate-v2）
.PHONY: run
run: dev

## dev: 同 run
.PHONY: dev
dev: check-env stop
	@mkdir -p $(RUN_DIR)
	@cd $(SERVER_DIR) && nohup $(PYBIN) -m eidolon api --host $(API_HOST) --port $(API_PORT) > $(CURDIR)/$(RUN_DIR)/api.log 2>&1 & echo $$! > $(CURDIR)/$(RUN_DIR)/api.pid
	@cd $(SERVER_DIR) && nohup $(PYBIN) -m eidolon worker > $(CURDIR)/$(RUN_DIR)/worker.log 2>&1 & echo $$! > $(CURDIR)/$(RUN_DIR)/worker.pid
	@cd $(WEB_DIR) && nohup pnpm dev > $(CURDIR)/$(RUN_DIR)/web.log 2>&1 & echo $$! > $(CURDIR)/$(RUN_DIR)/web.pid
	@failed=""; \
	 up=0; for i in $$(seq 1 30); do curl -sf -o /dev/null "http://127.0.0.1:$(API_PORT)/api/v2/health" && { up=1; break; }; sleep 1; done; \
	 [ "$$up" = 1 ] || failed="$$failed api(:$(API_PORT))"; \
	 up=0; for i in $$(seq 1 30); do lsof -nP -tiTCP:$(WEB_PORT) -sTCP:LISTEN >/dev/null 2>&1 && { up=1; break; }; sleep 1; done; \
	 [ "$$up" = 1 ] || failed="$$failed web(:$(WEB_PORT))"; \
	 if [ -n "$$failed" ]; then \
	   echo "ERROR: Eidolon did not come up — not listening:$$failed"; \
	   echo "--- $(RUN_DIR)/api.log (tail) ---"; tail -n 15 $(RUN_DIR)/api.log 2>/dev/null; \
	   echo "--- $(RUN_DIR)/worker.log (tail) ---"; tail -n 15 $(RUN_DIR)/worker.log 2>/dev/null; \
	   echo "--- $(RUN_DIR)/web.log (tail) ---"; tail -n 15 $(RUN_DIR)/web.log 2>/dev/null; \
	   echo "hint: 'make stop' force-clears the whole process tree; 'make ps' lists strays"; \
	   exit 1; \
	 fi
	@echo "Eidolon is running:"
	@echo "  Web      http://localhost:$(WEB_PORT)"
	@echo "  API      $(API_HOST):$(API_PORT)/api/v2 (proxied via Web at /api)"
	@echo "  Worker   see $(RUN_DIR)/worker.log"
	@echo "  Logs:    $(RUN_DIR)/api.log, $(RUN_DIR)/worker.log, $(RUN_DIR)/web.log"
	@echo "  Stop:    make stop"

## stop: 停止开发进程（进程树 + 端口双路清理，详见 scripts/devctl.sh）
.PHONY: stop
stop:
	@EIDOLON_API_PORT=$(API_PORT) EIDOLON_WEB_PORT=$(WEB_PORT) sh $(DEVCTL) stop

## ps: 列出本仓库所有 dev 进程（包括漂移到其他端口的孤儿）
.PHONY: ps
ps:
	@EIDOLON_API_PORT=$(API_PORT) EIDOLON_WEB_PORT=$(WEB_PORT) sh $(DEVCTL) ps

## status: 只看 API / Web 端口是否有健在的服务
.PHONY: status
status:
	@EIDOLON_API_PORT=$(API_PORT) EIDOLON_WEB_PORT=$(WEB_PORT) sh $(DEVCTL) status

## logs: 跟随 API / worker / Web 日志（Ctrl-C 退出）
.PHONY: logs
logs:
	@tail -n 50 -f $(RUN_DIR)/api.log $(RUN_DIR)/worker.log $(RUN_DIR)/web.log

## restart: 重启全部服务
.PHONY: restart
restart: stop dev

## v2-worker: 前台运行 worker 进程（LISTEN 唤醒 + 游标 drain；SIGTERM 优雅停止）
.PHONY: v2-worker
v2-worker: check-env
	@cd $(SERVER_DIR) && $(PYBIN) -m eidolon worker $(V2_ARGS)

## v2-api: 前台运行 API 进程（uvicorn；端口 EIDOLON_V2_API_PORT，默认 26881）
.PHONY: v2-api
v2-api: check-env
	@cd $(SERVER_DIR) && $(PYBIN) -m eidolon api --host $(API_HOST) --port $(API_PORT) $(V2_ARGS)

## v2-verify: 校验所有公司的事实哈希链（任一断链 exit 1；V2_ARGS="--company <uuid>" 单查）
.PHONY: v2-verify
v2-verify: check-env
	@cd $(SERVER_DIR) && $(PYBIN) -m eidolon verify-chain $(V2_ARGS)

## v2-ledger-verify: 校验所有公司的账本（折叠合法、试算平衡、投影一致、成本与分录对应）
.PHONY: v2-ledger-verify
v2-ledger-verify: check-env
	@cd $(SERVER_DIR) && $(PYBIN) -m eidolon ledger-verify $(V2_ARGS)

## v2-cursors: 列出消费者游标与落后量
.PHONY: v2-cursors
v2-cursors: check-env
	@cd $(SERVER_DIR) && $(PYBIN) -m eidolon cursors

## v2-record-fixtures: 对真实模型重录 provider/loop/project/company 回放 fixture（需要 key；写入开发库真实 run）
.PHONY: v2-record-fixtures
v2-record-fixtures: check-env
	@$(PYBIN) $(CURDIR)/scripts/record_provider_fixtures.py --loop --project --company $(V2_ARGS)

## test: 后端 pytest（真实 PostgreSQL）+ 前端 vitest
.PHONY: test
test: test-v2 test-web

## test-v2: v2 测试（需要 PostgreSQL：make pg-up 或 scripts/devpg.sh start；库名须以 _test 结尾）
.PHONY: test-v2
test-v2: check-env
	cd $(SERVER_DIR) && $(PYBIN) -m pytest tests_v2 -q

.PHONY: test-web
test-web:
	cd $(WEB_DIR) && pnpm exec vitest run

## lint: ruff + prettier + eslint + tsc
.PHONY: lint
lint: check-env
	$(RUFFBIN) check $(SERVER_DIR)/eidolon $(SERVER_DIR)/tests_v2 scripts
	$(RUFFBIN) format --check $(SERVER_DIR)/eidolon $(SERVER_DIR)/tests_v2 scripts
	cd apps/runner && $(RUFFBIN) check . && $(RUFFBIN) format --check .
	cd $(WEB_DIR) && pnpm exec tsc --noEmit && pnpm exec eslint .
	cd $(WEB_DIR) && pnpm exec prettier --check .

## format: ruff format + prettier（与 CI / package.json 脚本同一入口）
.PHONY: format
format: check-env
	$(RUFFBIN) format $(SERVER_DIR)/eidolon $(SERVER_DIR)/tests_v2 scripts
	cd apps/runner && $(RUFFBIN) format .
	cd $(WEB_DIR) && pnpm exec prettier --write .

## build: 前端生产构建
.PHONY: build
build:
	cd $(WEB_DIR) && pnpm build

## clean: 清理构建产物与本地运行数据（保留 .env 与数据库）
.PHONY: clean
clean: stop
	rm -rf $(RUN_DIR) $(WEB_DIR)/dist $(WEB_DIR)/node_modules/.vite
	rm -rf $(SERVER_DIR)/.pytest_cache $(SERVER_DIR)/.ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
