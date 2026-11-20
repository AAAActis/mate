# Mate - Warm Yerba & Organic Craft

**Mate** es una aplicación de bienestar y productividad que combina un avatar interactivo (un Mate con 4 estados emocionales) con gestión de tareas, hábitos y calendario integrada. La estética se inspira en el "Warm Yerba & Organic Craft" — tonos crema cálido, verde yerba y marrón calabaza.

## Problema
Prevenir BOLA (Broken Object Level Authorization) en operaciones SQLite asegurando que cada sesión solo accede a sus propios datos mediante filtrado obligatorio por `session_id`. Implementar SSE EventSource para proactividad, manejo de rate limiting 429, y avatar con 4 estados animados. Todo con estética "Warm Yerba & Organic Craft" y seguridad endurecida.

## Enlace
https://mate.bbasolutions.ar

## ¿Qué probar en 60 segundos

```bash
# 1. Levantar con Docker Compose (después de validar build)
docker-compose up -d

# 2. Verificar health check
curl localhost:8000/health/ready  # Debe retornar 200

# 3. Probar flujo completo
# - Crear sesión: GET /api/v1/session → cookie mate_sid
# - Avatar: cambiar de estado (neutro/hablando/contento/serio)
# - Agenda: ver tareas, completar con ✓, exportar .ics
# - Chat: enviar mensaje, manejar 429 ("Pará un poco, che, probá en un ratillo")
# - SSE: stream de eventos proactivos
# - Tema: sol/luna toggle (data-theme="dark-yerba")
```

## Diagrama Mermaid

```mermaid
flowchart TB
    subgraph Frontend
        direction LR
        INDEX[index.html] -->|SSE| STREAM[/api/v1/stream]
        INDEX -->|HTTP| API[/api/v1/*]
        STYLE INDEX fill:#f3ead8,stroke:#55633b,stroke-width:2px
        STYLE API fill:#e8f5e9,stroke:#55633b,stroke-width:2px
    end

    subgraph Backend
        direction LR
        MAIN[main.py] -->|SQLite| DB[(SQLite WAL)]
        MAIN -->|Rate Limit| MIDDLEWARE[middleware]
        MIDDLEWARE -->|Cookie| SESSION[session_id]
        STYLE MAIN fill:#fbf7ee,stroke:#8c5832,stroke-width:2px
        STYLE DB fill:#f5f0e8,stroke:#8c5832,stroke-width:2px
    end

    Frontend -->|Cookie mate_sid| Backend
    Backend -->|Content-Security-Policy| Frontend
```

## Estructura de ADRs

| ADR | Título | Estado |
|-----|--------|--------|
| ADR-001 | SQLite session_id isolation (BOLA prevention) | Accepted |
| ADR-002 | Docker security hardening | Accepted |
| ADR-003 | Frontend architecture - Warm Yerba & Organic Craft | Accepted |

## Cómo correrlo en 3 comandos

```bash
# Paso 1: Build de la imagen Docker
docker build -t mate:test .

# Paso 2: Correr con seguridad endurecida
docker run --rm -p 8000:8080 --read-only --tmpfs /tmp -v $(pwd)/data-test:/data mate:test

# Paso 3: Verificar que todo funciona
curl localhost:8000/health/ready  # 200
curl -b "mate_sid=test" localhost:8000/api/v1/session  # 200
```

## Tests

```bash
# Ejecutar pytest
pytest -x -q

# Validar seguridad BOLA
pytest tests/test_bola_isolation.py -x -q

# Validar rate limiting y 429
pytest tests/test_api_security.py -x -q

# Validar CSP y headers
pytest tests/test_security_headers.py -x -q
```

## Badge de CI

[![CI/CD Mate](https://github.com/aaaactis/mate/workflows/CI/CD/badge.svg)](https://github.com/aaaactis/mate/actions)

## Modelo de Amenazas OWASP API Top 10

| Riesgo | Control | Estado |
|--------|---------|--------|
| **API1: Broken Authentication** | Cookie HttpOnly, SameSite=Lax, session_id filtrado en todas queries SQLite | ✅ Implementado |
| **API2: Broken Resource Identification** | No applicable (SQLite with session isolation) | ✅ Sin riesgos por diseño |
| **API3: Excessive Data Exposure** | PiiRedactor regex en security.py (CBU, DNI, emails, teléfonos) | ✅ Implementado |
| **API4: Lack of Resource & Rate Limiting** | Rate limiting por CF-Connecting-IP, 429 response, daily token budget 50000 | ✅ Implementado |
| **API5: Security Misconfiguration** | read_only container, cap_drop ALL, no-new-privileges, json-file log rotation 10m x3 | ✅ Dockerfile + compose |
| **API6: Security Misconfiguration** | Content-Security-Policy, X-Content-Type-Options: nosniff, Referrer-Policy, Permissions-Policy headers | ✅ main.py |
| **API7: Server-Side Request Forgery** | No wget/curl en healthcheck (slim image), urllib.request solo | ✅ healthcheck.py |
| **API8: Cross-Origin Resource Sharing** | CORS cerrado por defecto, origins externos bloqueados | ✅ main.py |
| **API9: Insecure HTTP Methods** | Métodos HTTP adecuados, DELETE /api/v1/session borra sesión segura | ✅ main.py |
| **API10: Parameter Tampering** | Validación Pydantic v2 (extra="forbid"), límites de longitud, session_id filter | ✅ database.py |

## Límites Conocidos

1. **Sin Redis**: Fallback automático a memoria para quotas de tokens (no persistence across restarts).
2. **Slim image**: Healthcheck usa python urllib (no wget/curl); algunas valideces más simples que con imagen completa.
3. **Usuario 1654**: Creado con `useradd -u 1654 -M -s /usr/sbin/nologin app`; operaciones write limitadas a /data (volume) y /tmp (tmpfs) en modo read_only.
4. **SSE**: uvicorn --workers 1 (múltiples workers romperían el scheduler y rate limit en memoria).
5. **Docker daemon**: No disponible en entorno actual; files estructurados por specs pero sin validación end-to-end de `docker build + curl health/ready`.

---
*Proyecto de portafolio — Warm Yerba & Organic Craft aesthetic. Conforme a OWASP API Top 10 y hardening Docker.*