FROM python:3.13-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install mmkr
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy agent
COPY run_social.py ./

# Data volume
VOLUME ["/root/.mmkr-social"]

# Environment defaults
ENV MMKR_DATA=/root/.mmkr-social \
    MMKR_MODEL=claude-sonnet-4-5-20251101 \
    MMKR_TICK_INTERVAL=90 \
    MMKR_HANDLE=botbotfromuk

CMD ["python3", "run_social.py"]
