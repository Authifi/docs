PYTHON ?= .venv/bin/python
MKDOCS_VENV ?= .venv/bin/mkdocs
DOCKER_COMPOSE ?= docker compose
COMPOSE_BASE = $(DOCKER_COMPOSE) -f compose.yaml
COMPOSE_MOCK = $(DOCKER_COMPOSE) -f compose.yaml -f compose.mock.yaml

.PHONY: serve test build docker-build local-up local-mock-up local-down local-smoke local-config

define MOCK_ENV
OIDC_ISSUER=$${OIDC_ISSUER:-http://$${MOCK_OIDC_HOST:-oidc-mock.127.0.0.1.nip.io}:$${MOCK_OIDC_PORT:-9400}} \
OIDC_CLIENT_ID=$${OIDC_CLIENT_ID:-local-docs-client} \
OIDC_CLIENT_SECRET=$${OIDC_CLIENT_SECRET:-local-docs-secret} \
SESSION_SECRET=$${SESSION_SECRET:-local-session-secret} \
PUBLIC_BASE_URL=$${PUBLIC_BASE_URL:-http://localhost:$${DOCS_PORT:-8000}}
endef

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
	$(MOCK_ENV) $(COMPOSE_MOCK) up -d --build

local-down:
	$(MOCK_ENV) $(COMPOSE_MOCK) down --volumes --remove-orphans

local-smoke:
	$(MOCK_ENV) $(PYTHON) -m server.local_smoke

local-config:
	$(COMPOSE_BASE) config
	$(MOCK_ENV) $(COMPOSE_MOCK) config
