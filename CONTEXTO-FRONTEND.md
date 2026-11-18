# CONTEXTO FRONTEND: MATE (`mate.bbasolutions.ar`) — v2 (17/11/2026)

> **Instrucción para Claude Code:** reemplaza a la versión anterior. Vos no podés ver imágenes, así que acá está todo en **medidas, colores y coordenadas exactas**: seguilas al pie de la letra en vez de "interpretar una estética". No menciones ni simules plugins: lo que vale es el resultado en el código.
> Archivos: `src/mate_api/static/index.html`, `styles.css`, `app.js`. Sin CDNs, sin fuentes remotas, sin imágenes remotas, sin `innerHTML` con datos (siempre `textContent` o `createElement`).

---

## 0. Qué tiene que sentir el usuario

Entra y ve, en **una sola pantalla sin scroll** (desktop), un **mate ilustrado grande** en el centro que le **habla con un globo de historieta**, su **agenda de la semana** a la derecha, **4 atajos** abajo y una **barra para escribir**. Fondo **crema claro**, verdes yerba, marrón calabaza, trazos de tinta oscura. Nada oscuro, nada de neón, nada de violeta.

Mate es un **asistente general** (ver `CONTEXTO-BACKEND.md` §0): charla, redacta mails, opina de ropa, y recuerda rutinas y fechas.

---

## 1. Reglas que hoy están rotas (arreglar primero)

1. **Tema por defecto: claro crema.** Solo pasa a `data-theme="dark-yerba"` si el usuario toca la luna; la elección se guarda en `localStorage` (con `try/catch`). **No usar `prefers-color-scheme`.**
2. **Ningún overlay visible al cargar.** Todo `backdrop`, drawer, `<dialog>` u overlay arranca con `hidden`/`display:none`. Ninguna animación de entrada puede dejar `opacity < 1` como estado final si falla el JS.
3. **Al cargar, `app.js` llama a `GET /api/v1/session`** (con `credentials: "same-origin"`) **antes** de abrir el `EventSource`. Hoy el log del servidor muestra que nunca se llama, y por eso la agenda y el globo quedan vacíos.
4. Con la respuesta: pinta la agenda, pone el nombre y muestra `greeting.text` en el globo con `greeting.avatar_state`.

---

## 2. Paleta (sin cambios de valores)

```css
:root {
  --bg-canvas:#f3ead8; --bg-sidebar:#f8f2e4; --bg-card:#fbf7ee; --bg-card-hover:#f0e6d2;
  --bg-accent-soft:#e2e6ce; --border-warm:#dfd3bc; --border-ink:#3a2e24;
  --shadow-card:0 8px 24px -4px rgba(62,47,35,.08),0 2px 6px -1px rgba(62,47,35,.04);
  --text-primary:#2c3527; --text-secondary:#5c6454; --text-muted:#848978;
  --yerba-olive:#55633b; --yerba-olive-hover:#44502e; --yerba-fresh:#6a8a3a; --leaf:#8a9a5b;
  --gourd:#9a5f33; --gourd-shade:#7a4724; --gourd-dark:#5e371c; --metal-alpaca:#d6d9d8;
  --mouth:#5e2a1a; --tongue:#d9776b; --cheek:#e58e74; --status-firm:#c86d28; --danger-warm:#b94a3e;
  --font-display: ui-rounded, "Nunito", "Quicksand", system-ui, sans-serif;
  --font-sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --ease: cubic-bezier(.22,1,.36,1);
}
[data-theme="dark-yerba"] { /* igual que v1 */ }
```

Íconos: **SVG inline de línea** (`stroke: currentColor; stroke-width: 1.8; fill: none`, 20×20). **No usar emojis** en la interfaz.

---

## 3. Layout exacto (desktop ≥ 1100 px)

```
body: display:grid; grid-template-columns: 260px 1fr; height:100vh; overflow:hidden; background:var(--bg-canvas)

┌ SIDEBAR 260px ┐┌──────────────── MAIN (padding 24px, gap 20px) ─────────────────┐
│ logo + título ││ TOPBAR (alto 48px)                                              │
│ + Nueva conv. ││ ┌──────── ESCENARIO (1fr) ─────────┐ ┌── AGENDA 300px ──┐       │
│ Probá pedirle ││ │  Mate SVG 300px   Globo de texto  │ │                  │       │
│  (4 ejemplos) ││ │                                   │ │                  │       │
│ Tu agenda     ││ └───────────────────────────────────┘ └──────────────────┘       │
│ pie: nombre + ││ [ 4 TARJETAS, 4 columnas iguales, alto 88px ]                   │
│ tono          ││ [ BARRA DE CHAT, alto 56px, ancho completo ]                    │
└───────────────┘└─────────────────────────────────────────────────────────────────┘
```

- MAIN: `display:grid; grid-template-rows: 48px 1fr 88px 56px; gap:20px; padding:24px; min-height:0`.
- Fila del medio: `display:grid; grid-template-columns: 1fr 300px; gap:20px; min-height:0`.
- Todas las tarjetas: `background:var(--bg-card); border:1.5px solid var(--border-warm); border-radius:20px; box-shadow:var(--shadow-card)`.

**Mobile (< 900 px):** una sola columna con scroll. La sidebar se reduce a una barra superior de 56px con el logo y un botón ☰ que abre un drawer (con backdrop, **oculto por defecto**). Orden: escenario → barra de chat → tarjetas (2×2) → agenda.

---

## 4. Sidebar

1. **Logo:** mini Mate (el mismo SVG de §6, a 44px, estado `contento`) + título "Mate" (`font-family:var(--font-display); font-size:2rem; font-weight:800; color:var(--text-primary)`), subtítulo "Tu asistente, siempre con vos" (0.9rem, `--text-secondary`) y debajo un subrayado curvo: `<svg width="120" height="10"><path d="M2 6 Q60 0 118 6" stroke="#6a8a3a" stroke-width="2.5" fill="none" stroke-linecap="round"/></svg>`.
2. **Botón "+ Nueva conversación":** ancho completo, alto 44px, `border-radius:999px`, fondo `--yerba-olive`, texto `#fff` 600. Limpia el globo y la vista de historial (**no** borra datos).
3. **"Probá pedirle"** (título 0.8rem, mayúsculas, `--text-muted`): 4 ítems con ícono de burbuja; al tocarlos, se envían como mensaje:
   - "Redactame un mail para pedir una prórroga de entrega"
   - "Voy al gym lunes, miércoles y viernes a las 19"
   - "El 23 es el cumple de mi novia, avisame antes"
   - "Tengo una cita: jean negro y camisa bordó, ¿va?"
4. **Tarjeta "Tu agenda":** ícono calendario + "Tu agenda" + "{n} eventos hoy ›". Al tocarla, hace scroll y foco a la agenda.
5. **Pie:** círculo con la inicial del `display_name` (o "?") + nombre, y un selector segmentado `Amable | Firme` (activo: fondo `--yerba-olive`, texto blanco; `Firme` activo: fondo `--status-firm`). Debajo, en 0.75rem `--text-muted`: "Mate guarda lo que le contás 30 días en este navegador." + link "Borrar todo" (`--danger-warm`, pide confirmación con un `<dialog>`).

---

## 5. Topbar

- Izquierda: píldora (`border:1.5px solid var(--border-warm); border-radius:999px; padding:6px 14px`) con un punto de 8px (`--yerba-fresh` con pulso si el SSE está conectado, `--text-muted` si no) + "Mate en línea" / "Reconectando…".
- Al lado, una píldora chica: "Mensajes {usados}/{límite}".
- Derecha: dos botones circulares de 40px: sol/luna (cambia el tema) y engranaje (abre el `<dialog>` "Laboratorio": 3 botones para disparar los avisos de gym, estudio y cumple, la lista de herramientas del último turno y la gestión del token `.ics`).

---

## 6. El Mate (SVG inline, `viewBox="0 0 240 260"`, ancho 300px en el escenario)

Estructura (todos los trazos: `stroke:var(--border-ink); stroke-width:3; stroke-linejoin:round; stroke-linecap:round`):

```
<svg id="mate" data-state="neutro" viewBox="0 0 240 260" role="img" aria-label="Mate, tu asistente">
  <!-- bombilla -->
  <g class="bombilla">
    <line x1="152" y1="18" x2="128" y2="96" stroke="#c9ccc9" stroke-width="9"/>   (sin trazo de tinta extra; después otra línea igual con stroke ink width 2 por encima y opacity .35 para el borde)
    <rect x="144" y="10" width="18" height="10" rx="3" fill="#d6d9d8"/>             boquilla
    <line x1="146" y1="40" x2="141" y2="56" stroke="#a9adab" stroke-width="9"/>     anillo
  </g>
  <!-- cuerpo / porongo -->
  <path class="cuerpo" fill="var(--gourd)" d="M64 104 C40 140 38 205 80 232 C105 248 135 248 160 232 C202 205 200 140 176 104 Z"/>
  <path class="sombra" fill="var(--gourd-shade)" opacity=".45" stroke="none" d="M160 112 C186 150 188 205 150 236 C175 214 182 160 160 112 Z"/>
  <!-- virola de alpaca + yerba -->
  <rect x="60" y="88" width="120" height="18" rx="9" fill="var(--metal-alpaca)"/>
  <ellipse cx="120" cy="90" rx="52" ry="8" fill="var(--yerba-fresh)"/>
  <!-- mejillas -->
  <ellipse cx="86" cy="172" rx="10" ry="6" fill="var(--cheek)" opacity=".55" stroke="none"/>
  <ellipse cx="154" cy="172" rx="10" ry="6" fill="var(--cheek)" opacity=".55" stroke="none"/>
  <!-- OJOS: un grupo por estado -->
  <g class="ojos ojos-abiertos">  círculos (98,150) y (142,150) r=8 fill ink; brillo blanco r=2.5 en (101,147) y (145,147) </g>
  <g class="ojos ojos-felices">   paths "M89 153 Q98 141 107 153" y "M133 153 Q142 141 151 153", fill none, stroke-width 4 </g>
  <g class="cejas cejas-serio">   paths "M86 134 L108 140" y "M154 134 L132 140", stroke-width 4 </g>
  <!-- BOCAS -->
  <path class="boca boca-neutro"   d="M106 176 Q120 188 134 176" fill="none" stroke-width="4"/>
  <g    class="boca boca-contento">  path "M100 172 Q120 204 140 172 Z" fill var(--mouth); ellipse (120,188) rx 8 ry 5 fill var(--tongue) stroke none </g>
  <ellipse class="boca boca-hablando" cx="120" cy="181" rx="11" ry="8" fill="var(--mouth)"/>
  <path class="boca boca-serio"    d="M108 182 L132 182" fill="none" stroke-width="4"/>
  <!-- BRAZOS: un grupo por postura -->
  <g class="brazos brazos-saludo">   izq: path "M66 170 Q48 182 44 196", mano circle (44,198) r=9 fill gourd;
                                     der: <g class="mano-saluda"> path "M174 164 Q196 150 204 128", mano circle (205,123) r=9 fill gourd </g> </g>
  <g class="brazos brazos-arriba">   izq: path "M68 160 Q46 140 40 116" mano (39,111) r=9; der: path "M172 160 Q194 140 200 116" mano (201,111) r=9 </g>
  <g class="brazos brazos-jarra">    izq: path "M66 168 Q44 184 62 204" mano (64,206) r=9; der: path "M174 168 Q196 184 178 204" mano (176,206) r=9 </g>
</svg>
```

**Visibilidad por estado (solo CSS, todo lo demás `display:none`):**

| `data-state` | Ojos | Cejas | Boca | Brazos | Animación |
|---|---|---|---|---|---|
| `neutro` | abiertos | no | neutro | saludo | parpadeo (scaleY de los ojos a 0.1 en el 96-98 % de un ciclo de 4 s) + mano saludando (rotate ±14° sobre el hombro 174,164, 1.4 s, alternate) |
| `hablando` | abiertos | no | hablando | saludo (sin animar la mano) | boca: `ry` de 3 a 9 cada 0.16 s (`transform: scaleY` con `transform-box: fill-box; transform-origin: center`) + bombilla vibrando ±2° |
| `contento` | felices | no | contento | arriba | todo el SVG da un saltito: translateY 0 → -8px → 0, 0.6 s, 2 veces |
| `serio` | abiertos | sí | serio | jarra | ninguna |

- Cambiar de estado: `setEstado(e)` pone `data-state` y vuelve a `neutro` a los 4 s (salvo si sigue hablando).
- `@media (prefers-reduced-motion: reduce)`: sin animaciones, solo cambian las caras.

---

## 7. Escenario y globo de diálogo

- Escenario: `position:relative; display:flex; align-items:center; justify-content:center; gap:32px; overflow:hidden; border-radius:24px`.
- Fondo: un SVG absoluto (`inset:0; z-index:0`) con 2 manchas `--bg-accent-soft` (`<ellipse cx="35%" cy="55%" rx="32%" ry="40%"/>` y `<ellipse cx="70%" cy="30%" rx="22%" ry="26%"/>`) y **4 hojas de yerba** en las esquinas: `<path d="M0 0 C20 -30 60 -30 80 0 C60 30 20 30 0 0 Z" fill="var(--leaf)" opacity=".55"/>` + nervadura `<path d="M4 0 L76 0" stroke="#6f7f45" stroke-width="2"/>`, cada una con `transform` distinto (rotación entre -40° y 35°, escala 0.8 a 1.3).
- **Globo** (`#globo`): a la derecha del Mate, `max-width:440px; min-width:260px; background:var(--bg-card); border:2px solid var(--border-ink); border-radius:28px; padding:24px 28px; box-shadow:var(--shadow-card)`.
  - Piquito a la izquierda apuntando a la boca del Mate: `::before` triángulo de 18px con el mismo borde, a 45 % de alto.
  - **Texto corto (≤ 90 caracteres):** `font-family:var(--font-display); font-size:1.6rem; font-weight:800; line-height:1.25`.
  - **Texto largo (por ejemplo, un mail):** clase `.largo`: `font-size:1rem; font-weight:400; white-space:pre-wrap; max-height:300px; overflow:auto`, y aparece un botón "Copiar" (copia al portapapeles y muestra "¡Copiado!").
  - Arriba del texto, en 0.8rem `--text-muted`, el último mensaje del usuario: "Vos: {texto recortado a 60 caracteres}…".
  - Abajo, chips de herramientas ejecutadas: `✓ crear_habito`, en 0.75rem, fondo `--bg-accent-soft`, `border-radius:999px`.
  - Animación al cambiar el texto: escala 0.96 → 1 y opacidad 0 → 1 en 240 ms con `var(--ease)`. El texto aparece palabra por palabra (35 ms por palabra) mientras el Mate está en `hablando`.
- `decir(texto)` también lee el texto con `speechSynthesis` (voz `es-AR` o `es`) **solo si** el usuario activó la voz con el botón 🔊 de la topbar (apagada por defecto).

---

## 8. Agenda (columna derecha, 300px)

1. Cabecera: ícono + "Tu agenda" (1.1rem, 700) a la izquierda; botón chico "Suscribir .ics" (píldora con borde) a la derecha.
2. Fila de mes: botón `‹`, "Noviembre 2026" centrado (mes y año reales, en español), botón `›`. Las flechas mueven **de a una semana**.
3. **Tira semanal: UN solo contenedor** `display:grid; grid-template-columns:repeat(7,1fr); row-gap:6px; text-align:center`.
   - Fila 1: `Lu Ma Mi Ju Vi Sá Do` (0.75rem, `--text-muted`).
   - Fila 2: 7 `<button>` de 34×34, `border-radius:50%`, `font-variant-numeric:tabular-nums`. Hoy y el seleccionado: fondo `--yerba-olive`, texto `#fff`. Si el día tiene eventos, puntito de 5px debajo (`--yerba-fresh`).
   - Tocar un día filtra la lista de abajo a ese día.
4. "Próximos eventos" (0.95rem, 700) a la izquierda + "Ver todos ›" a la derecha (quita el filtro).
5. Cada evento es una fila `grid-template-columns: 10px 1fr auto; gap:10px; padding:10px 0; border-top:1px solid var(--border-warm)`:
   - Punto de 8px: `--yerba-fresh` (estudio, gym, hábitos) o `--gourd` (personal, recordatorios).
   - Título (0.95rem, 600) y abajo la hora "16:00 hs" (0.8rem, `--text-secondary`, tabular-nums) o la fecha "Mié 19 · 16:00 hs" si no es hoy.
   - Botón ✓ redondo de 28px: aparece al pasar el mouse (siempre visible en touch). Llama a `POST /api/v1/items/{tipo}/{id}/complete`; si responde OK: `setEstado("contento")`, `decir("¡Bien ahí! Uno menos.")`, y recarga la agenda.
6. Lista vacía: "Todavía no hay nada. Contale a Mate tus rutinas." Si el fetch falla: "No pude cargar la agenda." (nunca vacío sin texto).
7. El `<dialog>` del `.ics` muestra la URL en un input de solo lectura, con botón "Copiar" y botón "Generar link nuevo" (revoca).

---

## 9. Tarjetas y barra de chat

**4 tarjetas** (`display:grid; grid-template-columns:repeat(4,1fr); gap:16px`), cada una un `<button>` de alto 88px: ícono de 40px en un círculo `--bg-accent-soft` a la izquierda, texto en 2 líneas (600, 0.95rem) y `›` a la derecha. Hover: fondo `--bg-card-hover` y translateY(-2px).

| Tarjeta | Mensaje que envía |
|---|---|
| Planificar mi día | "¿Qué tengo hoy? Buscame un hueco de 1 hora y armame el día." |
| Redactar un mail | "Ayudame a redactar un mail. Preguntame primero para quién es y qué quiero decir." |
| ¿Qué me pongo? | "Tengo un evento y no sé qué ponerme. Preguntame qué tipo de evento es y qué ropa tengo." |
| Ver mi semana | "¿Qué tengo esta semana?" |

**Barra de chat:** píldora de ancho completo, alto 56px, `border-radius:999px; border:1.5px solid var(--border-warm); background:var(--bg-card); padding:0 8px 0 16px`.
- Izquierda: botón de reloj ("Historial"): abre un `<dialog>` lateral con `GET /api/v1/messages` (los míos a la derecha con fondo `--bg-accent-soft`, los de Mate a la izquierda con fondo `--bg-card`), y un botón "Cargar anteriores" (`before_id`).
- Centro: `<input maxlength="1000" placeholder="Escribile a Mate…">`. Enter envía.
- Derecha: botón ovalado de 40×40, fondo `--yerba-olive`, ícono avioncito blanco.
- Al enviar: deshabilita el input, `setEstado("hablando")`, el globo muestra "…" con tres puntos animados; con la respuesta, `decir(reply)`, después `setEstado(avatar_state)`, pone los chips y recarga la agenda si hubo herramientas.
- 429: `decir("Pará un poco, che. Probá en un ratito.")` con estado `serio`. Error de red: "Se me cortó la conexión. Probá de nuevo."

---

## 10. Verificación (sin ver la pantalla)

Antes de dar algo por terminado, mostrá la salida de:

```fish
grep -c "api/v1/session" src/mate_api/static/app.js          # >= 1
grep -c "prefers-color-scheme" src/mate_api/static/styles.css # 0
grep -nE "https?://" src/mate_api/static/*                     # vacío
grep -n "innerHTML" src/mate_api/static/app.js                 # vacío o solo con strings fijos
grep -c 'data-state' src/mate_api/static/index.html            # >= 1
grep -cE 'ojos-felices|boca-hablando|brazos-jarra' src/mate_api/static/index.html  # 3
```

Y levantá uvicorn y confirmá en el log que, al cargar `/`, aparece `GET /api/v1/session` **antes** de `GET /api/v1/stream`.
La verificación visual final la hace Santiago en el navegador.
