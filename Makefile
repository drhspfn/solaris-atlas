COMPOSE = docker compose -f infrastructure/local/compose.yml --env-file infrastructure/local/.env
DEV_COMPOSE = $(COMPOSE) -f infrastructure/dev/compose.yml

.PHONY: setup local-build local-up dev dev-down migrate server-test worker-test web-build

setup:
	./scripts/dev-up.sh

local-build:
	$(COMPOSE) build api web worker

local-up:
	$(COMPOSE) up -d

dev:
	$(DEV_COMPOSE) up --build -d

dev-down:
	$(DEV_COMPOSE) down

migrate:
	./scripts/migrate.sh

server-test:
	cd packages/server && uv run pytest

worker-test:
	cd packages/worker && uv run pytest

web-build:
	cd packages/web && npm run build
