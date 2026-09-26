.PHONY: help setup-backend dev-api dev-web build test test-notify eval doctor install-hooks clean-venv

# Port conventions:
#   - backend: :8000 (the same for Docker and local dev, so existing users aren't confused on upgrade)
#   - frontend: :5183 (offset from BeeCount-Cloud's :5173 to avoid clashes)

ifneq ($(filter Windows_NT,$(OS)),)
WINDOWS := 1
else ifneq (,$(findstring cmd.exe,$(ComSpec)))
WINDOWS := 1
endif

ifeq ($(WINDOWS),1)
SHELL := cmd.exe
.SHELLFLAGS := /C
PYTHON := python
VENV_PYTHON := .venv\Scripts\python.exe
else
PYTHON := python3
VENV_PYTHON := .venv/bin/python
endif

help:
	@echo "PanWatch development commands:"
	@echo "  make setup-backend   create the venv and install backend dependencies"
	@echo "  make dev-api         start the backend (:8000; runs setup-backend automatically)"
	@echo "  make dev-web         start the frontend (:5183; runs pnpm install automatically)"
	@echo "  make test            run all unit tests (no notifications sent by default)"
	@echo "  make test-notify     run all unit tests (really sends notifications)"
	@echo "  make eval            run the agent process eval set (chat cases need EVAL_AI_* environment variables)"
	@echo "  make doctor          system self-check (data sources/AI/notifications/DB/disk/scheduler)"
	@echo "  make build VERSION=x build the frontend + Docker image"
	@echo "  make install-hooks   install the git pre-push hook"
	@echo "  make clean-venv      delete the local venv"

setup-backend:
ifeq ($(WINDOWS),1)
	@if not exist .venv ( echo [setup] creating venv & $(PYTHON) -m venv .venv )
	@$(VENV_PYTHON) -m pip install -q -r requirements.txt
	@if not exist .env if exist .env.example copy /Y .env.example .env >nul
else
	@if [ ! -d .venv ]; then \
		echo ">>> creating venv"; \
		python3 -m venv .venv; \
	fi
	@$(VENV_PYTHON) -m pip install -q -r requirements.txt
	@if [ ! -f .env ] && [ -f .env.example ]; then cp .env.example .env; fi
endif

# server.py already starts with uvicorn.run(host=0.0.0.0, port=8000, reload=True).
dev-api: setup-backend
ifeq ($(WINDOWS),1)
	@set "DEV_RELOAD=1" && $(VENV_PYTHON) server.py
else
	@DEV_RELOAD=1 $(VENV_PYTHON) server.py
endif

dev-web:
ifeq ($(WINDOWS),1)
	@where pnpm >nul 2>&1 || ( echo pnpm is not installed; run npm install -g pnpm first & exit /b 1 )
	@cd frontend && pnpm install --no-frozen-lockfile && pnpm dev
else
	@if ! command -v pnpm >/dev/null 2>&1; then \
		echo "pnpm is not installed; run npm install -g pnpm first"; \
		exit 1; \
	fi
	cd frontend && pnpm install --no-frozen-lockfile && pnpm dev
endif

test:
	@$(VENV_PYTHON) -m pytest tests/ -v

test-notify:
	@$(VENV_PYTHON) -m pytest tests/ -v --notify

# Agent process eval (tool choice / arguments / grounding / structured output / action allowlist):
#   - structured parsing cases are pure rules and run directly
#   - chat tool-loop cases need a model under test: EVAL_AI_BASE_URL / EVAL_AI_API_KEY / EVAL_AI_MODEL
#   - add LLM-as-judge: EVAL_JUDGE_* environment variables + EVAL_ARGS=--judge
#   - run after changing prompts/*.txt or a tool schema; a score below the threshold (EVAL_PASS_THRESHOLD) exits non-zero
eval:
	@$(VENV_PYTHON) tests/eval/run_eval.py $(EVAL_ARGS)

# Command-line system self-check: data sources/AI/notifications + DB/disk/scheduler, printing results and fix hints
doctor:
	@$(VENV_PYTHON) -m src.modules.administration.doctor

# Usage: make build VERSION=0.3.0
build:
ifeq ($(WINDOWS),1)
	@if "$(VERSION)"=="" ( echo Usage: make build VERSION=^<version^> & exit /b 1 )
	@powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\build.ps1 -Version "$(VERSION)"
else
	@if [ -z "$(VERSION)" ]; then \
		echo "Usage: make build VERSION=<version>"; \
		exit 1; \
	fi
	./build.sh $(VERSION)
endif

install-hooks:
	bash scripts/install-hooks.sh

clean-venv:
ifeq ($(WINDOWS),1)
	@if exist .venv rmdir /S /Q .venv
else
	rm -rf .venv
endif
