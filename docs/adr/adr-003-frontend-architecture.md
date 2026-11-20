# ADR-003: Frontend Architecture - Warm Yerba & Organic Craft

## Status
Accepted

## Context
The frontend must implement the "Warm Yerba & Organic Craft" aesthetic with a consistent visual language across all 5 zones. The avatar SVG must support 4 emotional states (neutro, hablando, contento, serio) via data-state attributes. SSE EventSource connection must work with the backend /api/v1/stream endpoint. Theme toggle (sol/luna) must alter data-theme on <html>. All user data must use textContent to prevent XSS.

## Decision
Build the frontend using vanilla JavaScript with the following structure:

1. **index.html**: Semantic HTML with 5 zones (zona-1 through zona-5), SVG Mate inline with data-state="neutro" as default, connection indicator, sun/moon toggle button (data-theme="dark-yerba"), gear dialog (<dialog>), 4 action cards, chat input with maxlength=500.

2. **app.js**: 
   - `setEstado(estado)`: changes svg-el`'s data-state attribute with prefers-reduced-motion respect
   - `decir(texto)`: writes with textContent, uses speechSynthesis in es-AR if available, respects prefers-reduced-motion
   - EventSource connection to /api/v1/stream with online/offline state indicator ("Mate en línea" verde/gris)
   - Toggle sol/luna handler: sets data-theme on <html>
   - Panel ⚙ handler: opens <dialog> vacío
   - Zona 4: cargarAgenda() async function con fetch GET /api/v1/session credentials: "same-origin"
   - Zona 5: enviarMensajeChat(texto) async function con manejo 429 ("Pará un poco, che, probá en un ratillo")
   - Mostrar tools_executed como chips debajo del globo

3. **styles.css**: Paleta crema cálido (#f3ead8, #fbf7ee), verde yerba (#55633b), marrón calabaza (#8c5832). Modo "Noche Matera" con data-theme="dark-yerba". [data-state="contento"] .ojos rules, [data-state="hablando"] .boca rules. Respetar prefers-reduced-motion en todas las animaciones.

## Consequences
- **Positive**: Consistent aesthetic, accessible state management, XSS prevention via textContent, SSE real-time updates, theme toggle works across all zones.
- **Negative**: vanilla JS requires more boilerplate than React; SVG state depends on CSS selectors being correctly authored.

## Dependencies
- CONTEXTO-FRONTEND.md defines the aesthetic and interaction patterns.
- Backend must expose /api/v1/stream (SSE), /api/v1/session, /api/v1/chat, /api/v1/items/{tipo}/{id}/complete.

---
*Frontend architecture ADR for the Mate project, defining the Warm Yerba & Organic Craft implementation.*