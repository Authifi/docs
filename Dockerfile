FROM python:3.12-slim AS site-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY docs ./docs
COPY mkdocs.yml ./

RUN mkdocs build --strict


FROM python:3.12-slim AS runtime

ARG APP_USER=app
ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SITE_DIR=/app/site

WORKDIR /app

COPY server/requirements.txt ./server-requirements.txt

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r server-requirements.txt

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
