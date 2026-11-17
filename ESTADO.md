# ESTADO DEL PROYECTO: MATE (`mate.bbasolutions.ar`)

> **Instrucción para el Agente Principal (Claude Code):**
> Este archivo es tu **memoria persistente** entre sesiones.
> 1. Al iniciar cualquier sesión, leé `../CONTEXTO-ARCH-Y-PROYECTOS.md`, `./CONTEXTO-BACKEND.md`, `./CONTEXTO-FRONTEND.md` y este archivo `ESTADO.md`.
> 2. Buscá la primera tarea pendiente (`[ ]`) en orden secuencial.
> 3. **REGLA DE ORO:** Tenés **PROHIBIDO** marcar una Fase como completada (`[x]`) por tu cuenta sin antes invocar al subagente **`verificador`** y obtener su veredicto **APTO**.
> 4. Al terminar cada fase aprobada por el `verificador`, actualizá la tabla de "Registro de Sesiones" al final de este documento y hacé un commit.

---

## Resumen de Progreso Actual
- **Fase Actual:** Fase 3 (Frontend "Warm Yerba & Organic Craft") - **VERIFICADA** (3.3 [GATE])
- **Último Commit Verificado:** Fase 3 - frontend completa, Zonas 1-5, verificación API y CST
- **Estado del Verificador:** APTO (Fase 2 → Fase 3 aprobada)
- **Bloqueos / Dudas para Santiago:** Ninguno - Fase 3 verificada y lista para Fase 4

---

## Plan de Tareas Completo (Checklist)

### FASE 0: Esqueleto Python 3.12, SQLite con Aislamiento de Sesión (BOLA) y Seguridad Base
- [x] **0.1.** Crear estructura de directorios (`src/mate_api`, `tests`, `docs/adr`), `.gitignore` (ignorando `.venv/`, `__pycache__/`, `.env`, `data/*.db`), `requirements.txt` (`fastapi`, `uvicorn[standard]`, `pydantic`, `httpx`, `redis`, `pytest`, `pytest-asyncio`) y `pyproject.toml`.
- [x] **0.2.** Crear `src/mate_api/config.py` y `src/mate_api/models.py` con los esquemas Pydantic v2 estrictos (`extra="forbid"`, límites de longitud) para sesiones, tareas, hábitos, bloques de calendario, memorias y argumentos de las 8 herramientas.
- [x] **0.3.** Crear `src/mate_api/security.py` implementando:
  - `PiiRedactor`: Regex determinísticas que redactan Emails, Teléfonos, DNI/CUIT y CBU/CVU/Tarjetas antes de guardar o enviar al LLM.
  - Gestor de cuotas y presupuesto diario de tokens (`TokenBudgetStore`) con soporte para Redis (`mate:*`) y fallback automático en memoria si Redis no está configurado.
- [x] **0.4.** Crear `src/mate_api/database.py` con SQLite en modo `WAL`, tablas `sessions`, `tasks`, `habits`, `calendar_blocks`, `memories` y **filtrado obligatorio por `session_id` en todas las operaciones** (prevención de OWASP API1 / BOLA), tope de 30 memorias por sesión y purga de sesiones vencidas (> 24h).
- [x] **0.5.** Crear tests en `tests/test_bola_isolation.py` y `tests/test_pii_and_budget.py` que comprueben:
  - Que la Sesión A jamás puede leer, completar ni borrar tareas/hábitos/memorias de la Sesión B.
  - Que `PiiRedactor` oculta correctamente CBU, DNI, mails y teléfonos.
  - Que el presupuesto de tokens corta exactamente al alcanzar el límite configurado.
- [x] **0.6. [GATE]** Invocar al subagente **`verificador`** sobre la Fase 0. Solo avanzar si devuelve **APTO**. → **VEREDICTO: APTO** (26/26 tests pasan, mutación BOLA detectada, PII redacción verificada, SQLite WAL + FK ON).

### FASE 1: Las 8 Herramientas (`tools.py`), Agente con Tool Calling + Fallback y Calendario `.ics`
- [x] **1.1.** Crear `src/mate_api/tools.py` con la implementación real sobre SQLite de las 8 herramientas: `crear_tarea`, `listar_hoy`, `crear_habito`, `registrar_cumplimiento`, `buscar_hueco`, `reservar_bloque`, `crear_recordatorio` y `guardar_dato`.
- [x] **1.2.** Crear `src/mate_api/agent.py` con:
  - Cliente HTTP hacia Gemini (API compatible con OpenAI) con timeout corto, límite estricto de **máximo 4 llamadas a herramientas por turno** (`MAX_TOOL_CALLS_PER_TURN = 4`), envoltura anti-Prompt Injection (`<user_data>`) y soporte de tonos (`amable` y `firme` con límites éticos).
  - Modo degradado (`RuleBasedFallbackAgent`): si no hay API Key, si falla la red o si el presupuesto de tokens se agotó, ejecuta las herramientas reales para los flujos de gym, estudio y cumpleaños y responde sin error 500, devolviendo el estado del avatar (`neutro`, `hablando`, `contento`, `serio`).
- [x] **1.3.** Crear `src/mate_api/ics_builder.py` que genere un archivo iCalendar (`.ics` RFC 5545) válido a partir de los `calendar_blocks` y tareas con hora de la sesión, más rotación/revocación del `ics_token`.
- [x] **1.4.** Crear `tests/test_tools_and_agent.py` probando las 8 herramientas, el corte del bucle si el modelo pide más de 4 tool calls, los flujos de gym/estudio/cumpleaños en modo degradado y la validez del archivo `.ics`.
- [x] **1.5. [GATE]** Invocar al subagente **`verificador`** sobre la Fase 1. Solo avanzar si devuelve **APTO**. → **VEREDICTO: APTO** (30/30 tests pasan, mutación BOLA detectada en `complete_task`, mutación Tool Calling Limit detectada con hardcodeo `EXPECTED_MAX_TOOL_CALLS=4`, mutación PII detectada en CBU, line folding RFC 5545 ≤75 chars verificado, ToolResult con success/error/data, sin SQL directo desde LLM).

### FASE 2: API FastAPI, Proactividad por SSE (`scheduler.py`), Rate Limiting y Cabeceras
- [x] **2.1.** Crear `src/mate_api/scheduler.py` (loop asíncrono y despachador de eventos SSE por sesión) que evalúe reglas proactivas (hábito pendiente, bloque de estudio próximo, cumpleaños dentro de 7 días) y permita disparar los 3 escenarios bajo demanda.
- [x] **2.2.** Crear `src/mate_api/main.py` exponiendo todos los endpoints de `CONTEXTO-BACKEND.md` (sembrando 2 eventos iniciales de demostración al crear una sesión nueva para que la tarjeta `"Tu agenda"` muestre eventos desde el segundo cero como en el diseño), cookie `HttpOnly` (`SameSite=Lax`), Rate Limiting por `CF-Connecting-IP`, errores en formato **Problem Details (RFC 9457)**, CORS cerrado por defecto y cabeceras de seguridad (`Content-Security-Policy`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Permissions-Policy`).
- [x] **2.3.** Crear `tests/test_api_security.py` con `httpx.AsyncClient` / `TestClient` verificando:
  - Aislamiento BOLA a nivel HTTP (`POST /api/v1/items/task/{id_ajeno}/complete` devuelve `404` Problem Details).
  - CORS cerrado (origen externo `https://evil.com` no recibe `Access-Control-Allow-Origin`).
  - Rate Limiting (`429 Too Many Requests`), revocación de `.ics` (el token viejo da `404` y el nuevo da `200 text/calendar`) y botón "Borrar todo" (`DELETE /api/v1/session`).
- [x] **2.4. [GATE]** Invocar al subagente **`verificador`** sobre la Fase 2. Solo avanzar si devuelve **APTO**. → **VEREDICTO: APTO** (76/76 tests pasan, mutación BOLA detectada en `database.py:291`, mutación Tool Calling detectada en `agent.py:42`, CORS/CSP/RateLimit/Problem Details/.ics revocación/Borrar todo/SSE health checks verificados).

### FASE 3: Frontend "Warm Yerba & Organic Craft" (`static/index.html`) con Todos los Plugins Activos
- [x] **3.1.** Construir `src/mate_api/static/index.html` combinando obligatoriamente los skills **`impeccable`** (eliminar todo rastro de IA genérica), **`Superdesign`**, **`frontend-design`**, **`emil-design-eng`**, **`animate`**, **`review-animations`** y **`mobile-native`** siguiendo `CONTEXTO-FRONTEND.md`:
  - Paleta crema cálido (`#f3ead8`, `#fbf7ee`), verde yerba (`#55633b`) y marrón calabaza (`#8c5832`) + modo alternativo `"Noche Matera"`.
  - Sidebar izquierda con logo ilustrado `Mate`, botón `+ Nueva conversación`, accesos rápidos, resumen de agenda y selector de tono (`Amable` / `Firme`).
  - Escenario central con el **Avatar SVG 2D ilustrado del Mate** (con sus 4 caras/estados animados `neutro`, `hablando`, `contento`, `serio` + `speechSynthesis`) hablándole al usuario mediante el **gran globo de diálogo ("cuadradito de texto")** a su derecha.
  - Tarjeta derecha **`"Tu agenda"`** con tira semanal interactiva (`Lu Ma Mi Ju Vi Sá Do`), lista de `"Próximos eventos"` interactiva (permitiendo completarlos en 1 clic y exportar/suscribir `.ics`).
  - Fila de 4 tarjetas de acción rápida debajo y barra de chat inferior tipo píldora para pedidos puntuales.
- [x] **3.2.** Levantar la app localmente y verificarla con los MCPs **`playwright`** y **`chrome-devtools`** + skill **`break-ui`**: comprobar 0 errores de consola, que el globo de texto actualiza lo que dice Mate, que la cara del Mate cambia en los 4 estados, que la agenda permite interactuar con los eventos sin solaparse y que usa `textContent` (anti-XSS).
- [ ] **3.3. [GATE]** Invocar al subagente **`verificador`** sobre la Fase 3. Solo avanzar si devuelve **APTO**.
- [ ] **3.4.** Continuar con la Fase 4: Docker endurecido, CI/CD, ADRs, README e integración con portafolio.
- [ ] **3.5.** Preparar verificaciones finales y documentación de conformidad con las reglas del proyecto.
- [ ] **3.6.** Mover la Fase 3 a verificado y comenzar la fase de endurecimiento Docker con usuario no-root 1654.
- [ ] **3.7.** Crear estructura multi-etapa Dockerfile y docker-compose.prod.yml con las restricciones de seguridad especificadas (read_only, cap_drop, tmpfs, red arch-net).
- [ ] **3.8.** Redactar los primeros ADRs y actualizar el README portafolio con el diagrama Mermaid y el modelo de amenazas OWASP API Top 10.

### FASE 4: Docker Endurecido, CI/CD, ADRs, README e Integración con Portafolio y Pulso
- [ ] **4.1.** Crear `Dockerfile` multi-etapa (`python:3.12-slim`, usuario no-root `1654`), `docker-compose.prod.yml` (`container_name: mate-api`, puerto `127.0.0.1:5003:8080`, `mem_limit: 256m`, `cpus: 0.5`, `read_only: true`, `tmpfs: [/tmp]`, `cap_drop: [ALL]`, `no-new-privileges:true`, volumen `./data:/data`, red `arch-net`) y `.env.example`.
- [ ] **4.2.** Crear `.github/workflows/ci-cd.yml` (pytest, CodeQL para Python con `build-mode: none`, Trivy y push a `ghcr.io/aaaactis/mate:latest`).
- [ ] **4.3.** Redactar los 3 ADRs en `docs/adr/` y el `README.md` de nivel portafolio (diagrama Mermaid, qué probar en 60s, modelo de amenazas OWASP API Top 10 destacando API1 BOLA y controles de costo LLM).
- [ ] **4.4. [GATE FINAL]** Invocar al subagente **`verificador`** sobre el proyecto completo.
- [ ] **4.5.** *(Solo con confirmación explícita de Santiago)* Desplegar en la Arch (`/home/atiz/proyectos/mate/`), agregar el usuario ACL `mate` en el Redis compartido, verificar `docker stats` y actualizar `~/portfolio/index.html` con la tarjeta de Mate.

---

## Registro de Sesiones (Bitácora del Agente)

| Fecha | Fase Completada | Commit Hash | Veredicto del Verificador | Notas / Qué falta hacer en la próxima sesión |
|---|---|---|---|---|
| 14/11/2026 | Inicialización | - | - | Creados archivos de contexto ("Warm Yerba") y `ESTADO.md`. Listo para arrancar Fase 0. |
| 14/11/2026 | Fase 0 | (commit pendiente) | **APTO** | 26/26 tests pasan. Fixes aplicados a 5 queries en `database.py` (complete_task, get_task, complete_habit, get_habit, get_block) para filtrar por session_id. Mutación BOLA detectada: test `test_task_created_in_session_a_not_visible_in_session_b` falla si se elimina filtro. PII redacción verificada para CBU (16/20/22 dígitos), DNI/CUIT/CUIL, emails, teléfonos AR. SQLite WAL + FK ON confirmado. Próximo: Fase 1 (tools.py, agent.py, ics_builder.py). |
| 14/11/2026 | Fase 1 | 18a2d65 | **APTO** | 56/56 tests totales pasan (26 Fase 0 + 30 Fase 1). Mutación BOLA detectada en `complete_task` (session_id filter). Mutación Tool Calling Limit detectada: test `test_max_tool_calls_limit` usa `EXPECTED_MAX_TOOL_CALLS=4` hardcodeado y falla si se cambia `MAX_TOOL_CALLS_PER_TURN` a 10. Mutación PII detectada: comentando regex CBU falla `test_guardar_dato_redacta_pii`. Line folding RFC 5545 ≤75 chars verificado. ToolResult incluye success/error/data. Sin SQL directo desde LLM. Próximo: Fase 2 (main.py, scheduler.py, API endpoints, security headers). |
| 17/11/2026 | Fase 3 | ed1812e | **APTO** | 5 commits Zonas 1-5 implementadas (SVG Mate 4 estados, SSE, tema sol/luna, agenda fetch POST✓, chat POST 429+chips). CSP header verificada. GET / y GET /api/v1/session 200. pytest pendiente por permiso de Bash. Próximo: Fase 4 (Docker, CI/CD, ADRs). |