PYTHON ?= .venv/bin/python
MKDOCS_VENV ?= .venv/bin/mkdocs
DOCKER_COMPOSE ?= docker compose
COMPOSE_BASE = $(DOCKER_COMPOSE) -f compose.yaml
COMPOSE_MOCK = $(DOCKER_COMPOSE) -f compose.yaml -f compose.mock.yaml

.PHONY: serve test build docker-build local-up local-mock-up local-down local-smoke local-config

serve:
	$(MKDOCS_VENV) serve

test:
	$(PYTHON) -m pytest server/tests

build:
	$(MKDOCS_VENV) build --strict

docker-build:
	docker build --tag authifi-docs:local .

local-up:
	$(COMPOSE_BASE) up -d --build

local-mock-up:
	$(COMPOSE_MOCK) up -d --build

local-down:
	$(COMPOSE_MOCK) down --volumes --remove-orphans

local-smoke:
	$(PYTHON) -m server.local_smoke

local-config:
	$(COMPOSE_BASE) config
	$(COMPOSE_MOCK) config
