# python:3.12-slim, pinned by digest so every build resolves the same base.
# Refresh with: docker buildx imagetools inspect python:3.12-slim
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS site-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./

# Deliberately no `--upgrade pip`: that resolves whatever is newest on PyPI at
# build time, which would leave the digest-pinned base image only half a pin.
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY docs ./docs
COPY overrides ./overrides
COPY mkdocs.yml ./

RUN mkdocs build --strict


# python:3.12-slim, same digest as the site-builder stage above.
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS runtime

ARG APP_USER=app
ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SITE_DIR=/app/site

WORKDIR /app

COPY server/requirements.txt ./server-requirements.txt

# Same reasoning as the site-builder stage: use the pip the pinned base ships.
RUN python -m pip install --no-cache-dir -r server-requirements.txt

RUN groupadd --gid "${APP_GID}" "${APP_USER}" \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" \
        --create-home --home-dir "/home/${APP_USER}" \
        --no-log-init --shell /usr/sbin/nologin "${APP_USER}"

COPY --chown=${APP_UID}:${APP_GID} server ./server
# _headers is excluded from the MkDocs site output, but the server still reads
# it at runtime to populate the root Link headers.
COPY --from=site-builder --chown=${APP_UID}:${APP_GID} /app/docs/_headers ./docs/_headers
COPY --from=site-builder --chown=${APP_UID}:${APP_GID} /app/site ./site

EXPOSE 8080

USER ${APP_UID}:${APP_GID}

CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8080"]
