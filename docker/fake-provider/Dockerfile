ARG PYTHON_IMAGE=python:3.12.13-slim-bookworm

FROM ${PYTHON_IMAGE} AS runtime

ENV HOME=/home/fake-provider \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0

RUN groupadd --system --gid 10001 fake-provider \
    && useradd --system --uid 10001 --gid 10001 \
       --home-dir /home/fake-provider --create-home fake-provider

WORKDIR /srv/fake-provider

COPY --chown=10001:10001 docker/fake-provider/server.py ./server.py

USER 10001:10001

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=10 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).read()"

ENTRYPOINT ["python", "-I", "/srv/fake-provider/server.py"]
