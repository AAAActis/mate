"""Health check module for Docker container."""

import urllib.request
import sys


def check_ready():
    """Check if the application health endpoint is ready."""
    try:
        response = urllib.request.urlopen(
            "http://127.0.0.1:8080/health/ready", timeout=3
        )
        sys.exit(0 if response.status == 200 else 1)
    except Exception:
        sys.exit(1)