# NexusLeague — common dev commands.
# Cross-platform: works with GNU make on Linux/macOS/WSL/Git-Bash.
# On native Windows PowerShell, run scripts\setup.ps1 instead.

PY ?= python
UVICORN ?= uvicorn
COMPOSE ?= docker compose

.PHONY: help install env init-db run dev up down logs reset-db lint clean

help:
	@echo "Targets:"
	@echo "  make install     install Python deps into the active venv"
	@echo "  make env         copy .env.example to .env if missing"
	@echo "  make init-db     create tables + run inline migrations"
	@echo "  make reset-db    DROP + recreate all tables (destructive)"
	@echo "  make run         start uvicorn at http://localhost:8000"
	@echo "  make dev         uvicorn with --reload"
	@echo "  make up          docker compose up -d (db + web)"
	@echo "  make down        docker compose down"
	@echo "  make logs        docker compose logs -f web"
	@echo "  make clean       remove caches, *.pyc, etc."

install:
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.txt

env:
	@if [ ! -f .env ]; then cp .env.example .env && echo "Created .env — edit it now."; else echo ".env already exists."; fi

init-db:
	$(PY) -m scripts.init_db

reset-db:
	$(PY) -m scripts.init_db --reset

run:
	$(UVICORN) main:app --host 0.0.0.0 --port 8000

dev:
	$(UVICORN) main:app --host 0.0.0.0 --port 8000 --reload

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f web

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
