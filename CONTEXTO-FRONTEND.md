# CONTEXTO FRONTEND Y UI: MATE (`mate.bbasolutions.ar`)

> **Instrucción Obligatoria para Claude Code:**
> Esta interfaz **NO** usa el tema oscuro de Pulso. Usa una estética **"Warm Yerba & Organic Craft"** (crema cálido, papel artesanal, verde yerba mate orgánico y madera de calabaza), inspirada directamente en el mockup de referencia de Santiago.
> Todo vive en un único archivo `src/mate_api/static/index.html` (HTML5 + CSS3 moderno + Vanilla JS, cero CDNs externos, cero Google Fonts remotas).

---

## 1. Orquestación Obligatoria de Plugins, Skills y MCPs

Cada plugin instalado en tu entorno tiene una responsabilidad específica en este archivo y **deben trabajar en conjunto**:

1. **`impeccable` (Auditor Anti-IA Slop):**
   - Prohibido usar gradientes violetas/cianes, sombras neón, burbujas de chat tipo clon de ChatGPT o estética SaaS genérica.
   - Aplicá bordes orgánicos sutiles (`1.5px solid`), sombras cálidas color tierra (`rgba(74, 53, 37, 0.07)`), superficies crema texturadas y jerarquía tipográfica editorial.
2. **`Superdesign` + `frontend-design`:**
   - Construir el layout exacto de la imagen de referencia: Sidebar izquierda cálida + Escenario central con el Mate ilustrado y su globo de diálogo protagonista + Tarjeta flotante derecha `"Tu agenda"` + 4 Tarjetas de acción horizontales + Barra de entrada inferior tipo píldora.
3. **`animate` + `review-animations`:**
   - Animar en SVG/CSS los **4 estados faciales y corporales del Mate** (`neutro`, `hablando`, `contento`, `serio`), el movimiento de su manito saludando, las hojitas de yerba de fondo y la transición elástica del globo de diálogo cuando habla.
4. **`emil-design-eng`:**
   - Micro-interacciones con curvas `cubic-bezier(0.22, 1, 0.36, 1)`, `font-variant-numeric: tabular-nums` en todas las horas/fechas del calendario (`16:00 hs`, `19:00 hs`, grillas de días `10 11 12 13 14 15 16`) y estados `:focus-visible` impecables.
5. **`mobile-native`:**
   - En pantallas `< 900px`, la sidebar pasa a un drawer/barra compacta, el escenario del Mate y el globo se adaptan al centro sin recortarse y la agenda se apila limpiamente.
6. **`playwright` + `chrome-devtools` (MCPs) + `break-ui`:**
   - Levantar el servidor local, verificar que no haya solapamientos de cajas (`boundingBox`), que el globo de diálogo nunca pise la tarjeta de `"Tu agenda"` y que los textos largos no rompan el layout.

---

## 2. Paleta de Colores Exacta ("Warm Yerba & Organic Paper")

Además del tema claro cálido principal (por defecto, igual a la imagen), el botón del sol/luna arriba a la derecha permite alternar a un modo **"Noche Matera"** (verde bosque profundo y madera oscura, nunca azul neón).

```css
:root {
  /* Superficies Crema y Papel Cálido */
  --bg-canvas: #f3ead8;          /* Fondo general crema yerba suave */
  --bg-sidebar: #f8f2e4;         /* Crema claro para la barra lateral izquierda */
  --bg-card: #fbf7ee;            /* Blanco hueso cálido para "Tu agenda", globo y tarjetas */
  --bg-card-hover: #f0e6d2;      /* Hover cálido en botones y tarjetas */
  --bg-accent-soft: #e2e6ce;     /* Manchas orgánicas verde salvia suave detrás del Mate */

  /* Bordes y Sombras Tierra (Cero gris frío) */
  --border-warm: #dfd3bc;        /* Borde sutil de tarjetas y divisores */
  --border-ink: #3a2e24;         /* Trazo estilo ilustración (2px) para el Mate y el globo */
  --shadow-card: 0 8px 24px -4px rgba(62, 47, 35, 0.08), 0 2px 6px -1px rgba(62, 47, 35, 0.04);

  /* Textos */
  --text-primary: #2c3527;       /* Verde bosque casi negro / tinta cálida */
  --text-secondary: #5c6454;     /* Verde oliva grisáceo para subtítulos y horas */
  --text-muted: #848978;         /* Días de la semana (Lu Ma Mi Ju Vi Sá Do) */

  /* Acentos Yerba Mate y Calabaza */
  --yerba-olive: #55633b;        /* Verde yerba principal (botones primarios, día seleccionado "13", enviar) */
  --yerba-olive-hover: #44502e;
  --yerba-fresh: #6a8a3a;        /* Puntito "Mate en línea" y punto de evento de estudio */
  --gourd-brown: #8c5832;        /* Marrón calabaza (punto de evento "Deploy", detalles) */
  --gourd-dark: #5e371c;         /* Sombra del porongo */
  --metal-alpaca: #d6d9d8;       /* Virola y bombilla de alpaca */
  --status-firm: #c86d28;        /* Terracota cálido cuando el Mate se pone en tono "Firme" */
  --danger-warm: #b94a3e;        /* Rojo terracota para "Borrar todo" */

  /* Tipografía sin CDNs externos (Orgánica y legible) */
  --font-display: 'ui-rounded', 'Quicksand', 'Nunito', system-ui, -apple-system, sans-serif;
  --font-sans: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
  --font-mono: 'JetBrains Mono', 'Fira Code', ui-monospace, monospace;
}

/* Tema opcional con el botón de sol/luna arriba a la derecha */
[data-theme="dark-yerba"] {
  --bg-canvas: #1a2118;
  --bg-sidebar: #21291e;
  --bg-card: #283225;
  --bg-card-hover: #323e2e;
  --bg-accent-soft: #243020;
  --border-warm: #394634;
  --border-ink: #11160f;
  --text-primary: #f3ead8;
  --text-secondary: #b8c2ad;
  --text-muted: #849178;
  --yerba-olive: #6c8049;
}
```

---

## 3. Arquitectura Visual de la Pantalla (5 Zonas como en la Imagen)

### Zona 1: Barra Lateral Izquierda (`250px` fijo en desktop)
- **Logo e Identidad Superior:**
  - Ilustración SVG mini del matecito arriba con destellos alrededor.
  - Título grande **`Mate`** en `--text-primary` (`font-size: 2rem; font-weight: 800`) y subtítulo `"Tu asistente, siempre con vos"` con un subrayado curvo artesanal en SVG.
- **Botón Principal:**
  - Píldora ancha color `--yerba-olive` (`#55633b`) con texto claro: **`+ Nueva conversación`** (limpia el historial visual o inicia un hilo limpio, y al lado un botón discreto `🗑 Borrar sesión` para cumplir con la purga total de datos).
- **Sección `"Rutinas y conversaciones"` (en lugar de links muertos, son disparadores reales):**
  - Ítems con ícono de burbuja que al hacer clic cargan/filtran o disparan las consultas reales de la sesión:
    - `💬 Plan de estudio para examen`
    - `💬 Rutina de entrenamiento (Gym)`
    - `💬 Recordatorios y cumpleaños`
    - `💬 Memoria de Mate (X/30 datos)` *(abre un popover/drawer mostrando los hechos guardados con `guardar_dato` y las tools ejecutadas).*
- **Tarjeta Resumen de Agenda (Abajo):**
  - Caja crema redondeada con ícono de calendario: **`Tu agenda`** / `X eventos hoy >` (actualizado en vivo con la cantidad real de bloques/tareas del día; al hacer clic resalta o abre el modal de suscripción `.ics`).
- **Pie de Sidebar (Perfil + Selector de Tono):**
  - Avatar circular + **`Santiago`** y selector rápido del carácter del Mate: **`🧉 Tono Amable`** vs **`🔥 Tono Firme`**.

---

### Zona 2: Barra Superior Flotante (Top Bar sobre el lienzo)
- **Izquierda:** Píldora crema con borde suave `--border-warm`: puntito verde pulsante (`--yerba-fresh`) + texto **`Mate en línea`** (conectado en tiempo real al stream SSE `/api/v1/stream`). Al lado, una píldora pequeña que muestra si la voz está activa (`🔊 Voz`) y el contador de cuota (`Mensajes: X/20`).
- **Derecha:**
  - Botón de **Sol/Luna** (alterna entre el tema crema original `"Warm Yerba"` y `"Noche Matera"`).
  - Botón de **Engranaje (`⚙`)**: abre el **Panel de Ingeniería & Laboratorio Proactivo** (un modal/drawer limpio para reclutadores donde se ven las llamadas a herramientas `tools_executed`, los botones para simular los 3 avisos proactivos SSE en 1 clic —Gym, Estudio, Cumpleaños— y la gestión del token `.ics`).

---

### Zona 3: Escenario Central — El Mate Ilustrado + Su Globo de Diálogo ("El Cuadradito de Texto")
Es el alma de la aplicación. No es un chat aburrido de burbujas apiladas, sino que **Mate vive en el centro y te habla directamente a través de su globo de historieta ilustrado**:

1. **Fondo Orgánico:**
   - Formas suaves en SVG color `--bg-accent-soft` (`#e2e6ce`) y hojitas de yerba mate ilustradas asomando detrás del personaje.
2. **El Personaje Mate (SVG 2D Ilustrado con trazos `--border-ink`):**
   - Calabaza redondeada marrón cálido con sombreado lateral, virola plateada de alpaca, bombilla inclinada a la izquierda con detalle de anillos, bracitos expresivos con trazo oscuro y mejillas ruborizadas suaves.
   - **Cambia su cara y postura según los 4 estados (`data-state`):**
     - `neutro`: Ojos redondos amables con parpadeo natural, sonrisa tranquila, mano saludando suavemente.
     - `hablando`: Boca animada abriéndose y cerrándose en sincronía con `speechSynthesis` o el efecto *typewriter* del globo, bombilla vibrando levemente.
     - `contento`: Ojos arqueados felices (`^ ^`), boca abierta sonriente (como en tu imagen), bracitos arriba celebrando y destellos verdes alrededor.
     - `serio`: Cejas fruncidas decididas, boca recta firme, bracitos en jarra (cuando está en tono `firme` o te reclama un hábito pendiente).
3. **El Globo de Diálogo Principal ("El Cuadradito de Texto" donde Mate habla):**
   - Ubicado a la derecha del Mate con su piquito apuntando a la boca del personaje, borde orgánico `2px solid var(--border-ink)`, fondo `--bg-card` (`#fbf7ee`) y un subrayado decorativo verde yerba adentro.
   - **Todo lo que Mate te dice (saludos, respuestas del agente o avisos proactivos SSE como *"¡Hola, Santiago! ¿En qué puedo ayudarte hoy?"* o *"Che, hoy toca estudiar Bases de Datos, ¿arrancamos?"*) aparece escrito acá adentro** con una transición suave de palabras.
   - Debajo del texto dentro del mismo globo (en letra chica técnica y prolija), si el turno ejecutó herramientas del backend, muestra los chips discretos: `✓ crear_habito` · `✓ reservar_bloque` (y un botón `"Ver historial de charla"` por si el usuario quiere desplegar los mensajes anteriores).

---

### Zona 4: Tarjeta Derecha `"Tu agenda"` (Interactiva y Conectada al Backend)
Idéntica en ubicación y estética a tu imagen (`width: 310px` a la derecha del escenario):
- **Cabecera:** Ícono de calendario + **`Tu agenda`** + botón discreto `📥 .ics` arriba a la derecha.
- **Navegador de Mes y Tira Semanal Interactiva:**
  - `< Noviembre 2026 >` (o el mes actual dinámico).
  - Grilla de 7 columnas (`Lu Ma Mi Ju Vi Sá Do`) con los números de la semana actual (`10 11 12 [13] 14 15 16` con `tabular-nums`).
  - El día seleccionado tiene círculo relleno color `--yerba-olive` (`#55633b`) y texto claro. Los días que tienen eventos o tareas muestran un puntito verde debajo del número. **Hacer clic en cualquier día filtra los eventos de ese día.**
- **Lista `"Próximos eventos"` (Interactiva):**
  - Cabecera: `Próximos eventos` | link `Suscribir .ics →` (abre el modal con la URL `/api/v1/calendar/<token>.ics`, botón copiar y botón revocar token).
  - Cada fila muestra un puntito de color según categoría (`--yerba-fresh` para estudio/hábitos, `--gourd-brown` para proyectos/personal), el título (`Estudiar para examen`) y la hora abajo (`16:00 hs`).
  - **Interacción real:** Al pasar el mouse o hacer clic sobre un evento/tarea/hábito de la lista, permite **marcarlo como completado (`✓`)** (llamando a `POST /api/v1/items/{tipo}/{id}/complete`, lo que hace que el Mate pase inmediatamente al estado `contento` y te felicite en el globo de texto) o agregar un bloque rápido.

---

### Zona 5: Las 4 Tarjetas de Acción Horizontal + Barra de Chat Inferior
Debajo del Mate y de la agenda, exactamente como en la imagen:

1. **Fila de 4 Tarjetas Interactivas (Prompts y Acciones de 1-Clic):**
   - Cuatro tarjetas crema (`background: var(--bg-card)`, `border: 1px solid var(--border-warm)`, `border-radius: 16px`, ícono ilustrado a la izquierda, texto en 2 líneas y flechita `>`):
     1. 💡 **`Planificar mi día >`** -> Envía al agente: *"Listá qué tengo para hoy, buscame un hueco libre de 1 hora y ayudame a organizar el día"* (prueba `listar_hoy` + `buscar_hueco`).
     2. 🎓 **`Agendar estudio >`** -> Envía al agente: *"Agendame un bloque de estudio de Bases de Datos hoy a las 16:00 hs y creá el hábito de estudiar 3 veces por semana"* (prueba `reservar_bloque` + `crear_habito`).
     3. 💪 **`Registrar mi Gym >`** -> Envía al agente: *"Quiero ir al gym 3 veces por semana a las 19:00 hs y anotar que hoy ya cumplí"* (prueba `crear_habito` + `registrar_cumplimiento`).
     4. 🎁 **`Guardar recordatorio >`** -> Envía al agente: *"El cumpleaños de mi novia es en 6 días, guardalo en tu memoria y creá un recordatorio"* (prueba `guardar_dato` + `crear_recordatorio`).
2. **Barra de Chat Inferior (Para pedidos puntuales):**
   - Píldora ancha inferior (`background: var(--bg-card)`, `border: 1.5px solid var(--border-warm)`, `border-radius: 999px`, `padding: 10px 16px`).
   - A la izquierda: botón de **historial/herramientas** (ícono de clip/chispas que muestra el log del chat o dispara un aviso proactivo de prueba).
   - Al centro: `<input type="text" placeholder="Escribí tu mensaje para cosas puntuales..." maxlength="500">`.
   - A la derecha: botón ovalado color `--yerba-olive` (`#55633b`) con ícono de avioncito de papel blanco para enviar.