# Eidolon Makefile — one entrypoint for install/run/test/build.
# Config comes from .env (copied from .env.example on `make install`).

-include .env
export

API_HOST      ?= $(or $(EIDOLON_API_HOST),127.0.0.1)
API_PORT      ?= $(or $(EIDOLON_API_PORT),26881)
WEB_PORT      ?= $(or $(EIDOLON_WEB_PORT),26880)
RUNTIME_MODE  ?= $(or $(EIDOLON_RUNTIME_MODE),mock)

SERVER_DIR    := apps/server
WEB_DIR       := apps/web
RUN_DIR       := .run
VENV          := $(SERVER_DIR)/.venv
UV            := $(shell command -v uv 2>/dev/null)
PYTHON        ?= python3

ifeq ($(UV),)
  PYBIN       := $(VENV)/bin/python
  PIPBIN      := $(VENV)/bin/pip
  RUFFBIN     := $(VENV)/bin/ruff
  UVICORNBIN  := $(VENV)/bin/uvicorn
else
  PYBIN       := cd $(SERVER_DIR) && uv run python
  RUFFBIN     := cd $(SERVER_DIR) && uv run ruff
  UVICORNBIN  := cd $(SERVER_DIR) && uv run uvicorn
endif

.DEFAULT_GOAL := help

## help: 显示所有可用命令
.PHONY: help
help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/^## //' | column -t -s ':'

## install: 安装前后端全部依赖（自动检测 uv / pnpm）
.PHONY: install
install: check-tools install-server install-web
	@[ -f .env ] || { cp .env.example .env; echo "created .env from .env.example"; }
	@echo "install done."

.PHONY: check-tools
check-tools:
	@command -v $(PYTHON) >/dev/null || { echo "ERROR: python3 not found (need >= 3.11)"; exit 1; }
	@command -v pnpm >/dev/null || { echo "ERROR: pnpm not found. Install: npm i -g pnpm (or https://pnpm.io/installation)"; exit 1; }
ifndef UV
	@echo "note: uv not found, falling back to python venv + pip"
endif

.PHONY: install-server
install-server:
ifdef UV
	cd $(SERVER_DIR) && uv sync --extra dev
else
	$(PYTHON) -m venv $(VENV)
	$(PIPBIN) install -e '$(SERVER_DIR)[dev]'
endif

.PHONY: install-web
install-web:
	cd $(WEB_DIR) && pnpm install

## run: 一次启动 Frontend + Backend（后台运行，日志在 .run/）
.PHONY: run
run: dev

## dev: 同 run
.PHONY: dev
dev: stop
	@mkdir -p $(RUN_DIR)
ifdef UV
	@cd $(SERVER_DIR) && nohup uv run uvicorn app.main:app --host $(API_HOST) --port $(API_PORT) > $(CURDIR)/$(RUN_DIR)/server.log 2>&1 & echo $$! > $(CURDIR)/$(RUN_DIR)/server.pid
else
	@cd $(SERVER_DIR) && nohup .venv/bin/uvicorn app.main:app --host $(API_HOST) --port $(API_PORT) > $(CURDIR)/$(RUN_DIR)/server.log 2>&1 & echo $$! > $(CURDIR)/$(RUN_DIR)/server.pid
endif
	@cd $(WEB_DIR) && nohup pnpm dev > $(CURDIR)/$(RUN_DIR)/web.log 2>&1 & echo $$! > $(CURDIR)/$(RUN_DIR)/web.pid
	@sleep 2
	@echo "Eidolon is starting:"
	@echo "  Eidolon Web     http://localhost:$(WEB_PORT)"
	@echo "  Network Access  http://<server-ip>:$(WEB_PORT)"
	@echo "  Backend API     $(API_HOST):$(API_PORT) (proxied via Web at /api, /ws — normally not needed directly)"
	@echo "  Runtime Mode    $(RUNTIME_MODE)"
	@echo "  Logs:           $(RUN_DIR)/server.log, $(RUN_DIR)/web.log"
	@echo "  Stop:           make stop"

## stop: 停止前后台开发进程
.PHONY: stop
stop:
	@for s in server web; do \
	  if [ -f $(RUN_DIR)/$$s.pid ] && kill -0 $$(cat $(RUN_DIR)/$$s.pid) 2>/dev/null; then \
	    kill $$(cat $(RUN_DIR)/$$s.pid) 2>/dev/null && echo "stopped $$s"; \
	  fi; rm -f $(RUN_DIR)/$$s.pid; \
	done; true

## restart: 重启全部服务
.PHONY: restart
restart: stop dev

## test: 后端 pytest + 前端 vitest
.PHONY: test
test: test-server test-web

.PHONY: test-server
test-server:
ifdef UV
	cd $(SERVER_DIR) && uv run pytest tests -q
else
	$(VENV)/bin/python -m pytest $(SERVER_DIR)/tests -q
endif

## test-integration: 真实 Docker 集成测试（需要本机 Docker daemon）
.PHONY: test-integration
test-integration:
ifdef UV
	cd $(SERVER_DIR) && uv run pytest tests/integration -m integration -q
else
	$(VENV)/bin/python -m pytest $(SERVER_DIR)/tests/integration -m integration -q
endif

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

## lint: ruff + eslint + tsc
.PHONY: lint
lint:
ifdef UV
	cd $(SERVER_DIR) && uv run ruff check app tests && uv run ruff format --check app tests
else
	$(RUFFBIN) check $(SERVER_DIR)/app $(SERVER_DIR)/tests
	$(RUFFBIN) format --check $(SERVER_DIR)/app $(SERVER_DIR)/tests
endif
	cd $(WEB_DIR) && pnpm exec tsc --noEmit && pnpm exec eslint .

## format: ruff format + prettier
.PHONY: format
format:
ifdef UV
	cd $(SERVER_DIR) && uv run ruff format app tests
else
	$(RUFFBIN) format $(SERVER_DIR)/app $(SERVER_DIR)/tests
endif
	cd $(WEB_DIR) && pnpm exec prettier --write src

## build: 前端生产构建
.PHONY: build
build:
	cd $(WEB_DIR) && pnpm build

## clean: 清理构建产物与本地运行数据（保留 .env）
.PHONY: clean
clean: stop
	rm -rf $(RUN_DIR) $(WEB_DIR)/dist $(WEB_DIR)/node_modules/.vite
	rm -rf $(SERVER_DIR)/.pytest_cache $(SERVER_DIR)/.ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
