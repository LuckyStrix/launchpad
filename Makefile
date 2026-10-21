.PHONY: dev backend frontend test lint build up down logs backup

VENV := .venv
PY := $(VENV)/bin/python

$(VENV):
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -r backend/requirements-dev.txt

frontend/node_modules:
	cd frontend && npm ci

# Local development: API on :8080, Vite with hot reload on :5173.
dev: $(VENV) frontend/node_modules
	@trap 'kill 0' EXIT; \
	(cd backend && ../$(VENV)/bin/uvicorn app.main:create_app --factory --reload --port 8080) & \
	(cd frontend && npm run dev) & \
	wait

test: $(VENV)
	cd backend && ../$(PY) -m pytest -q

lint: $(VENV) frontend/node_modules
	cd backend && ../$(VENV)/bin/ruff check .
	cd frontend && npx svelte-check --threshold warning

build:
	docker compose build

up:
	@docker network inspect launchpad-hermes >/dev/null 2>&1 || docker network create launchpad-hermes
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f launchpad

# Copy the database (layout, chat history, Google token) out of the volume.
backup:
	@mkdir -p backups
	docker compose cp launchpad:/data/launchpad.db backups/launchpad-$$(date +%Y%m%d-%H%M%S).db
	@ls -1t backups | head -1
