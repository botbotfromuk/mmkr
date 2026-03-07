FROM python:3.13-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y git curl && rm -rf /var/lib/apt/lists/*

# Copy mmkr source (monorepo context)
COPY ../../ /mmkr-src/

# Install mmkr
RUN pip install -e /mmkr-src --no-cache-dir

# Copy coder variant
COPY . /app/

# Data volume
VOLUME ["/root/.mmkr-coder"]

# Health check via state.json
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s \
  CMD test -f /root/.mmkr-coder/state.json || exit 1

ENV MMKR_DATA=/root/.mmkr-coder
ENV MMKR_TICK_INTERVAL=90
ENV MMKR_MEMORY_SLOTS=400

CMD ["python3", "run_coder.py"]
