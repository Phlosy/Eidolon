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
DEVCTL        := $(CURDIR)/scripts/devctl.sh
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
	@[ -d $(VENV) ] || (cd $(SERVER_DIR) && uv venv)
	cd $(SERVER_DIR) && uv pip install -r requirements.lock
	cd $(SERVER_DIR) && uv pip install -e . --no-deps
else
	$(PYTHON) -m venv $(VENV)
	$(PIPBIN) install -r $(SERVER_DIR)/requirements.lock
	$(PIPBIN) install -e '$(SERVER_DIR)' --no-deps
endif

## lock-server: 用当前 .venv 的实测版本重写后端 requirements.lock
.PHONY: lock-server
lock-server:
ifdef UV
	@cd $(SERVER_DIR) && uv pip freeze | grep -Ev '^(eidolon-server|-e )| @ |^#' | sort -f > requirements.lock
else
	@$(VENV)/bin/python -m pip freeze --exclude-editable | grep -Ev '^-e | @ ' | sort -f > $(SERVER_DIR)/requirements.lock
endif
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
dev: stop
	@mkdir -p $(RUN_DIR)
ifdef UV
	@cd $(SERVER_DIR) && nohup uv run uvicorn app.main:app --host $(API_HOST) --port $(API_PORT) > $(CURDIR)/$(RUN_DIR)/server.log 2>&1 & echo $$! > $(CURDIR)/$(RUN_DIR)/server.pid
else
	@cd $(SERVER_DIR) && nohup .venv/bin/uvicorn app.main:app --host $(API_HOST) --port $(API_PORT) > $(CURDIR)/$(RUN_DIR)/server.log 2>&1 & echo $$! > $(CURDIR)/$(RUN_DIR)/server.pid
endif
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

## lint: ruff + prettier + eslint + tsc
.PHONY: lint
lint:
ifdef UV
	cd $(SERVER_DIR) && uv run ruff check app tests && uv run ruff format --check app tests
else
	$(RUFFBIN) check $(SERVER_DIR)/app $(SERVER_DIR)/tests
	$(RUFFBIN) format --check $(SERVER_DIR)/app $(SERVER_DIR)/tests
endif
	cd $(WEB_DIR) && pnpm exec tsc --noEmit && pnpm exec eslint .
	cd $(WEB_DIR) && pnpm exec prettier --check .

## format: ruff format + prettier（与 CI / package.json 脚本同一入口：整仓 + .prettierignore）
.PHONY: format
format:
ifdef UV
	cd $(SERVER_DIR) && uv run ruff format app tests
else
	$(RUFFBIN) format $(SERVER_DIR)/app $(SERVER_DIR)/tests
endif
	cd $(WEB_DIR) && pnpm exec prettier --write .

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
