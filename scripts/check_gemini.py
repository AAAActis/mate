#!/usr/bin/env python3
"""Script para verificar que la API nativa de Gemini funciona con la key del .env."""
import os
import sys
import json
import httpx
from pathlib import Path

# Cargar .env si existe
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    from dotenv import load_dotenv
    load_dotenv(env_path)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
GEMINI_TIMEOUT_S = float(os.getenv("GEMINI_TIMEOUT_S", "20"))

if not GEMINI_API_KEY:
    print("HTTP 000: GEMINI_API_KEY no configurada en .env")
    sys.exit(1)

url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
headers = {
    "x-goog-api-key": GEMINI_API_KEY,
    "Content-Type": "application/json",
}
payload = {
    "contents": [{"role": "user", "parts": [{"text": "decime hola"}]}],
    "generationConfig": {"maxOutputTokens": 100, "temperature": 0.7},
}

try:
    with httpx.Client(timeout=GEMINI_TIMEOUT_S) as client:
        resp = client.post(url, headers=headers, json=payload)

    status = resp.status_code
    if status >= 400:
        try:
            error_data = resp.json()
            error_status = error_data.get("error", {}).get("status", "UNKNOWN")
            error_message = error_data.get("error", {}).get("message", "")
            print(f"HTTP {status}: {error_status} - {error_message}")
        except Exception:
            print(f"HTTP {status}: {resp.text}")
    else:
        data = resp.json()
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)
            print(f"HTTP {status}: {text.strip()}")
        else:
            print(f"HTTP {status}: (sin candidates)")

except httpx.TimeoutException:
    print(f"HTTP 000: Timeout ({GEMINI_TIMEOUT_S}s)")
except Exception as e:
    print(f"HTTP 000: {type(e).__name__}: {e}")