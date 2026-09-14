ARG PYTHON_IMAGE=python:3.12.13-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.8.22

FROM ${UV_IMAGE} AS uv-bin

FROM ${PYTHON_IMAGE} AS dependency-builder

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /build

COPY --from=uv-bin /uv /usr/local/bin/uv
COPY requirements.lock ./
RUN uv venv --python /usr/local/bin/python /opt/klm-venv \
    && uv pip sync \
        --python /opt/klm-venv/bin/python \
        --require-hashes \
        --no-cache \
        requirements.lock

FROM ${PYTHON_IMAGE} AS runtime

ARG SOURCE_REVISION=""

LABEL org.opencontainers.image.revision="${SOURCE_REVISION}" \
      io.meuexecutivo.database.alembic-head="c9d0e1f2a3b4" \
      io.meuexecutivo.database.contract="resolver-owner-v1"

ENV HOME=/home/klm \
    PATH=/opt/klm-venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0

RUN groupadd --system --gid 10001 klm \
    && useradd --system --uid 10001 --gid 10001 --home-dir /home/klm --create-home klm \
    && mkdir -p /app/.local/object-storage \
    && chown -R 10001:10001 /app /home/klm

WORKDIR /app

COPY --from=dependency-builder --chown=10001:10001 /opt/klm-venv /opt/klm-venv
COPY --chown=10001:10001 app ./app
COPY --chown=10001:10001 alembic ./alembic
COPY --chown=10001:10001 alembic.ini ./alembic.ini
COPY --chown=10001:10001 \
    docs/oss-function-units.lock.json \
    docs/oss-upstreams.lock.json \
    ./docs/
COPY --chmod=0555 --chown=10001:10001 entrypoint.sh ./entrypoint.sh

# The Debian slim runtime includes perl-base as an essential package even
# though the application does not execute Perl. Keeping it would ship known
# critical CVEs with no Bookworm security update available. Remove it only
# after every build-time package operation; the runtime is intentionally
# package-manager-free and the application contract is verified from the
# resulting image before release.
RUN dpkg --purge --force-depends --force-remove-essential perl-base \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/*

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else sys.exit(1)" || exit 1

CMD ["/app/entrypoint.sh"]
