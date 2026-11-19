# Control plane only. Build the wheel before building this image.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
RUN groupadd --gid 10001 relay && useradd --uid 10001 --gid relay --create-home relay
WORKDIR /app
COPY requirements-server.lock /app/requirements-server.lock
RUN python -m pip install --no-cache-dir --require-hashes -r requirements-server.lock
COPY dist/reel_relay-*.whl /tmp/wheels/
RUN python -m pip install --no-cache-dir --no-deps /tmp/wheels/*.whl && rm -rf /tmp/wheels \
    && mkdir /data /config && chown relay:relay /data /config
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health/live', timeout=3)"
ENTRYPOINT ["reel-relay", "--config", "/config/config.toml"]
CMD ["serve"]
