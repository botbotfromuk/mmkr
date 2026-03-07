FROM python:3.13-slim

WORKDIR /app

# Install mmkr and dependencies
COPY pyproject.toml .
COPY src/ src/
RUN pip install -e ".[trader]" --no-cache-dir

# Copy trader variant
COPY variants/trader/ variants/trader/

# Data volume
VOLUME ["/root/.mmkr-trader"]

# Health: check that state.json is written (means at least one tick completed)
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s \
    CMD test -f /root/.mmkr-trader/state.json || exit 1

CMD ["python3", "variants/trader/run_trader.py"]
