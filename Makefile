PYTHON ?= .venv/bin/python
MKDOCS_VENV ?= .venv/bin/mkdocs
DOCKER_COMPOSE ?= docker compose
COMPOSE_COMMON = $(DOCKER_COMPOSE) -f compose.yaml
COMPOSE_REAL = $(DOCKER_COMPOSE) -f compose.yaml -f compose.real.yaml
COMPOSE_MOCK = $(DOCKER_COMPOSE) -f compose.yaml -f compose.mock.yaml

.PHONY: serve test build release docker-build local-up local-mock-up local-down local-smoke local-config

serve:
	$(MKDOCS_VENV) serve

test:
	$(PYTHON) -m pytest server/tests

build:
	$(MKDOCS_VENV) build --strict

release:
	./scripts/build-release.sh "$${RELEASE_SHA:-$$(git rev-parse HEAD)}" "$${RELEASE_DIR:-dist/releases}"

docker-build:
	docker build --tag authifi-docs:local .

local-up:
	$(COMPOSE_REAL) up -d --build

local-mock-up:
	$(COMPOSE_MOCK) up -d --build

local-down:
	$(COMPOSE_MOCK) down --volumes --remove-orphans

local-smoke:
	$(PYTHON) -m server.local_smoke

local-config:
	$(DOCKER_COMPOSE) --env-file .env.example -f compose.yaml -f compose.real.yaml config
	$(COMPOSE_MOCK) config
