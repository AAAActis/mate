FROM python:3.12-slim AS builder

# Create non-root user 1654
RUN useradd -u 1654 -M -s /usr/sbin/nologin app

WORKDIR /app

# Install only needed deps for build (no gcc needed if we copy installed deps)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/mate_api/ /app/src/mate_api/

# Production stage
FROM python:3.12-slim

# Create non-root user 1654 (same UID as builder)
RUN useradd -u 1654 -M -s /usr/sbin/nologin app

# Security: read-only filesystem, tmpfs for /tmp, drop all caps, no new privileges
# These will be overridden by docker-compose security settings

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy source code
COPY src/mate_api/ /app/src/mate_api/

# Healthcheck using python urllib (no wget/curl in slim image)
# The app serves /health/ready on port 8080
COPY --chmod=0644 src/mate_api/healthcheck.py /app/healthcheck.py

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health/ready', timeout=3).status == 200 else 1)"]

# Uvicorn with single worker (scheduler, SSE and in-memory rate limit don't work with multiple)
ENV PYTHONPATH=/app
WORKDIR /app
ENV DATABASE_PATH=/data/mate.db
CMD ["uvicorn", "src.mate_api.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]

# Drop privileges to non-root user after CMD is set
USER 1654

# Environment
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1