// Mate — frontend. Orden de carga (§1): tema → GET /api/v1/session → EventSource.

const $ = (id) => document.getElementById(id);
const ESTADOS = ['neutro', 'hablando', 'contento', 'serio'];
let sesion = null;
let eventSource = null;

// ── Calendar state ───────────────────────────────────────────────────────────
let calendarMonth = null; // Date object for current month in Córdoba (1st day of month)
let selectedDay = null;   // YYYY-MM-DD string for selected day

// ── Tema (§1.1): claro por defecto, oscuro solo si el usuario lo eligió ─────
function leerTema() {
  try { return localStorage.getItem('mate-tema'); } catch { return null; }
}
function aplicarTema(tema) {
  const oscuro = tema === 'dark-yerba';
  if (oscuro) document.documentElement.setAttribute('data-theme', 'dark-yerba');
  else document.documentElement.removeAttribute('data-theme');
  $('btn-tema').setAttribute('aria-label', oscuro ? 'Cambiar a tema claro' : 'Cambiar a tema oscuro');
}
$('btn-tema').addEventListener('click', () => {
  const nuevo = document.documentElement.getAttribute('data-theme') === 'dark-yerba' ? 'claro' : 'dark-yerba';
  aplicarTema(nuevo);
  try { localStorage.setItem('mate-tema', nuevo); } catch { /* sin storage: queda solo para esta visita */ }
});

// ── Mate y globo ─────────────────────────────────────────────────────────────
function setEstado(estado) {
  const ESTADOS = ['neutro', 'hablando', 'contento', 'serio'];
  const normalized = ESTADOS.includes(estado) ? estado : 'neutro';
  const $mate = $('mate');
  if ($mate.getAttribute('data-state') !== normalized) {
    $mate.setAttribute('data-state', normalized);
  }
}
function mostrarChips(herramientas) {
  // Chips solo en el panel de ingeniería (⚙), no en el globo
  const cont = document.querySelector('#dialog-panel .globo-chips');
  if (!cont) return;
  cont.replaceChildren();

  // Group by tool name to avoid duplicates
  const counts = {};
  for (const h of herramientas) {
    if (!h.success) continue; // Only show successful tools
    const name = h.name;
    counts[name] = (counts[name] || 0) + 1;
  }

  // Human-readable names (neutral, no assumptions)
  const displayNames = {
    'crear_tarea': 'Anotado',
    'reservar_bloque': 'Reservado',
    'crear_habito': 'Anotado',
    'guardar_dato': 'Guardé un dato',
    'crear_recordatorio': 'Recordatorio',
    'listar_hoy': 'Agenda',
    'registrar_cumplimiento': 'Listo',
    'buscar_hueco': 'Hueco encontrado'
  };

  for (const [name, count] of Object.entries(counts)) {
    const chip = document.createElement('span');
    chip.className = 'globo-chip';
    chip.textContent = displayNames[name] + (count > 1 ? ` ×${count}` : '');
    cont.appendChild(chip);
  }
}

// ── Date helpers in Córdoba timezone ─────────────────────────────────────────
const CORDOBA_TZ = 'America/Argentina/Cordoba';

// Returns "YYYY-MM-DD" for today in Córdoba
function todayKeyCordoba() {
  const fmt = new Intl.DateTimeFormat('en-CA', { timeZone: CORDOBA_TZ, year: 'numeric', month: '2-digit', day: '2-digit' });
  return fmt.format(new Date());
}

// Returns "YYYY-MM-DD" for a Date constructed as local (y, m-1, d)
function formatDateKey(date) {
  const fmt = new Intl.DateTimeFormat('en-CA', { timeZone: CORDOBA_TZ, year: 'numeric', month: '2-digit', day: '2-digit' });
  return fmt.format(date);
}

// Parse "YYYY-MM-DD" as local Córdoba date (no timezone conversion)
function parseDateLocal(dateStr) {
  const [y, m, d] = dateStr.split('-').map(Number);
  return new Date(y, m - 1, d);
}

// Month name in Spanish (Argentina)
function monthNameES(date) {
  const fmt = new Intl.DateTimeFormat('es-AR', { timeZone: CORDOBA_TZ, month: 'long', year: 'numeric' });
  return fmt.format(date);
}

// Day name in Spanish (Argentina)
function dayNameES(date) {
  const fmt = new Intl.DateTimeFormat('es-AR', { timeZone: CORDOBA_TZ, weekday: 'long' });
  return fmt.format(date);
}

// First/last day of month for a Date (local y/m/d, treated as Córdoba)
function getMonthBounds(date) {
  const first = new Date(date.getFullYear(), date.getMonth(), 1);
  const last = new Date(date.getFullYear(), date.getMonth() + 1, 0);
  return { first, last };
}

// Day of week: 0=Monday ... 6=Sunday
function getDayOfWeek(date) {
  const dow = date.getDay();
  return dow === 0 ? 6 : dow - 1;
}

// ── Calendar rendering ────────────────────────────────────────────────────────
function getEventsForDate(dateKey) {
  if (!sesion?.calendar) return [];
  return sesion.calendar.filter(e => e.date === dateKey);
}

function getCategoryColor(category) {
  const colors = {
    'deporte': '#4a7aa8',
    'estudio': '#c0603a',
    'trabajo': '#6a8a3a',
    'social': '#8a6aa8',
    'personal': '#8c5832',
    'recordatorio': '#8a6aa8'
  };
  return colors[category] || '#8a6aa8';
}

function renderCalendarGrid() {
  try {
    const grid = $('calendario-grid');
    const titleEl = $('mes-actual');
    if (!grid || !calendarMonth) return;

    grid.replaceChildren();
    titleEl.textContent = monthNameES(calendarMonth).replace(/^./, c => c.toUpperCase());

    const { first, last } = getMonthBounds(calendarMonth);
    const firstDow = getDayOfWeek(first); // 0=Monday
    const daysInMonth = last.getDate();
    const todayKey = todayKeyCordoba();

    // Empty cells before first day
    for (let i = 0; i < firstDow; i++) {
      const empty = document.createElement('span');
      empty.className = 'calendario-dia vacio';
      grid.appendChild(empty);
    }

    // Days of month
    for (let day = 1; day <= daysInMonth; day++) {
      const date = new Date(calendarMonth.getFullYear(), calendarMonth.getMonth(), day);
      const dateKey = formatDateKey(date);
      const events = getEventsForDate(dateKey);
      const isToday = dateKey === todayKey;
      const isSelected = dateKey === selectedDay;

      const cell = document.createElement('button');
      cell.className = 'calendario-dia';
      cell.setAttribute('role', 'gridcell');
      cell.setAttribute('aria-label', `${day} de ${calendarMonth.toLocaleString('es-AR', { month: 'long' })}`);
      if (isToday) cell.classList.add('hoy');
      if (isSelected) cell.classList.add('seleccionado');
      cell.dataset.date = dateKey;

      // Day number
      const num = document.createElement('span');
      num.className = 'dia-numero';
      num.textContent = String(day);
      cell.appendChild(num);

      // Event dots (max 3, then +N)
      if (events.length > 0) {
        const dots = document.createElement('div');
        dots.className = 'dia-eventos';
        const maxDots = 3;
        const showCount = Math.min(events.length, maxDots);
        for (let i = 0; i < showCount; i++) {
          const dot = document.createElement('span');
          dot.className = 'evento-punto';
          dot.style.background = getCategoryColor(events[i].category);
          dots.appendChild(dot);
        }
        if (events.length > maxDots) {
          const more = document.createElement('span');
          more.className = 'evento-punto mas';
          more.textContent = `+${events.length - maxDots}`;
          dots.appendChild(more);
        }
        cell.appendChild(dots);
      }

      cell.addEventListener('click', () => selectDay(dateKey));
      grid.appendChild(cell);
    }
  } catch (e) {
    console.error('renderCalendarGrid error:', e);
  }
}

function selectDay(dateKey) {
  selectedDay = dateKey;
  renderCalendarGrid();
  renderDayList();
}

function renderDayList() {
  try {
    const list = $('lista-dia');
    const titleEl = $('dia-seleccionado-titulo');
    const emptyEl = $('dia-vacio');
    if (!list || !selectedDay) return;

    const events = getEventsForDate(selectedDay);
    const dateObj = parseDateLocal(selectedDay);

    // Update title
    const todayKey = todayKeyCordoba();
    const isToday = selectedDay === todayKey;
    titleEl.textContent = isToday
      ? `Hoy, ${dateObj.getDate()} de ${monthNameES(dateObj).split(' ')[0]}`
      : `${dayNameES(dateObj).replace(/^./, c => c.toUpperCase())}, ${dateObj.getDate()} de ${monthNameES(dateObj).split(' ')[0]}`;

    list.replaceChildren();

    if (events.length === 0) {
      emptyEl.hidden = false;
      return;
    }
    emptyEl.hidden = true;

    // Group by type: blocks first, then tasks, reminders, birthdays
    const typeOrder = { block: 0, task: 1, reminder: 2, birthday: 3 };
    events.sort((a, b) => (typeOrder[a.type] || 99) - (typeOrder[b.type] || 99));

    for (const ev of events) {
      const li = document.createElement('li');
      li.className = `item-dia ${ev.category}`;
      if (ev.completed) li.classList.add('hecho');

      const left = document.createElement('div');
      left.style.display = 'flex';
      left.style.flexDirection = 'column';
      left.style.gap = '2px';
      left.style.flex = '1';
      left.style.minWidth = '0';

      const titleSpan = document.createElement('span');
      titleSpan.className = 'item-dia-titulo';
      titleSpan.textContent = ev.title;
      left.appendChild(titleSpan);

      const timeSpan = document.createElement('span');
      timeSpan.className = 'item-dia-hora';
      timeSpan.textContent = ev.time;
      left.appendChild(timeSpan);

      li.appendChild(left);

      // Checkbox for completable items (tasks only)
      if (ev.type === 'task' && !ev.completed) {
        const btn = document.createElement('button');
        btn.className = 'btn-check';
        btn.setAttribute('aria-label', `Marcar "${ev.title}" como hecho`);
        btn.textContent = '✓';
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          completar('task', ev.id);
        });
        li.appendChild(btn);
      }

      list.appendChild(li);
    }
  } catch (e) {
    console.error('renderDayList error:', e);
  }
}

// ── Sesión (§1.3 y §1.4) ─────────────────────────────────────────────────────
async function cargarSesion() {
  const resp = await fetch('/api/v1/session', { credentials: 'same-origin' });
  if (!resp.ok) throw new Error(`GET /session ${resp.status}`);
  sesion = await resp.json();
  pintarSesion();
}

function pintarSesion() {
  try {
    const nombre = sesion.display_name || 'Vos';
    $('perfil-nombre').textContent = nombre;
    $('perfil-inicial').textContent = nombre.charAt(0).toUpperCase();
    marcarTono(sesion.tone);
    $('contador-cuota').textContent = String(sesion.session?.messages_count_today ?? 0);
    // Saludo solo en la primera carga de la página (no al recargar tras un mensaje)
    if (sesion.greeting && !pintarSesion.ya) {
      pintarSesion.ya = true;
      decirSaludo(sesion.greeting.text, sesion.greeting.avatar_state);
    }

    // Sidebar agenda summary: "X cosas hoy · Y mañana"
    const resumenEl = $('resumen-agenda');
    if (resumenEl && sesion.calendar) {
      const today = todayKeyCordoba();
      const tomorrow = formatDateKey(new Date(parseDateLocal(today).getTime() + 86400000));
      const todayCount = sesion.calendar.filter(e => e.date === today).length;
      const tomorrowCount = sesion.calendar.filter(e => e.date === tomorrow).length;
      resumenEl.textContent = `${todayCount} cosa${todayCount !== 1 ? 's' : ''} hoy · ${tomorrowCount} cosa${tomorrowCount !== 1 ? 's' : ''} mañana`;
    }

    // Initialize calendar to current month in Córdoba (only first load)
    if (!calendarMonth) {
      const today = todayKeyCordoba();
      const [y, m] = today.split('-').map(Number);
      calendarMonth = new Date(y, m - 1, 1);
      selectedDay = today;
    }
    renderCalendarGrid();
    renderDayList();
  } catch (e) {
    console.error('pintarSesion error:', e);
  }
}

function pintarAgenda() {
  try {
    const lista = $('lista-agenda');
    if (!lista) return;
    lista.replaceChildren();
    const tasks = sesion.tasks || [];
    const habits = sesion.habits || [];
    const blocks = sesion.blocks || [];

    for (const b of blocks) lista.appendChild(itemAgenda(b.title, hhmm(b.start_utc)));
    for (const t of tasks) {
      lista.appendChild(itemAgenda(t.title, t.due_time || '', () => completar('task', t.id), t.completed));
    }
    for (const h of habits) {
      lista.appendChild(itemAgenda(h.name, `${h.done_this_week}/${h.target_per_week} sem.`, () => completar('habit', h.id)));
    }

    const total = tasks.length + habits.length + blocks.length;
    const vacia = $('agenda-vacia');
    const resumen = $('resumen-agenda');
    if (vacia) vacia.hidden = total > 0;
    if (resumen) resumen.textContent = total === 0
      ? 'Nada agendado por ahora.'
      : `${blocks.length} bloques, ${tasks.length} tareas y ${habits.length} hábitos.`;
  } catch (e) {
    console.error('pintarAgenda error:', e);
  }
}

function hhmm(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return new Intl.DateTimeFormat('es-AR', { timeZone: CORDOBA_TZ, hour: '2-digit', minute: '2-digit', hour12: false }).format(d);
}

function itemAgenda(titulo, meta, completar, hecho) {
  const li = document.createElement('li');
  li.className = 'item-agenda' + (hecho ? ' hecho' : '');
  const t = document.createElement('span');
  t.className = 'titulo';
  t.textContent = titulo;
  const m = document.createElement('span');
  m.className = 'meta';
  m.textContent = meta;
  li.append(t, m);
  if (completar && !hecho) {
    const b = document.createElement('button');
    b.className = 'btn-check';
    b.setAttribute('aria-label', `Marcar "${titulo}" como hecho`);
    b.textContent = '✓';
    b.addEventListener('click', completar);
    li.appendChild(b);
  }
  return li;
}

async function completar(tipo, id) {
  const resp = await fetch(`/api/v1/items/${tipo}/${id}/complete`, { method: 'POST', credentials: 'same-origin' });
  if (!resp.ok) return;
  const data = await resp.json();
  // Solo mostrar la reacción si el backend la envía; si no viene, silencio
  if (data.message) decir(data.message, data.avatar_state);
  await cargarSesion().catch(() => {});
}

// ── Tono ─────────────────────────────────────────────────────────────────────
function marcarTono(tono) {
  document.querySelectorAll('.tono').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.tono === tono)));
}
document.querySelectorAll('.tono').forEach((b) => b.addEventListener('click', async () => {
  const resp = await fetch('/api/v1/tone', {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tone: b.dataset.tono }),
  });
  if (resp.ok) marcarTono((await resp.json()).tone);
}));

// ── Tono Mode (Automático/Amable/Firme) ─────────────────────────────────────
function marcarTonoModo(modo) {
  document.querySelectorAll('.tono-modo-btn').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.modo === modo)));
}
document.querySelectorAll('.tono-modo-btn').forEach((b) => b.addEventListener('click', async () => {
  const resp = await fetch('/api/v1/tone/mode', {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tone_mode: b.dataset.modo }),
  });
  if (resp.ok) marcarTonoModo((await resp.json()).tone_mode);
}));

function mostrarTonoDetectado(tono) {
  const el = $('tono-detectado');
  if (!el) return;
  if (tono) {
    el.textContent = `Tono usado: ${tono}`;
    el.hidden = false;
  } else {
    el.hidden = true;
  }
}

// ── Copiar globo ─────────────────────────────────────────────────────────────
document.querySelector('.copiar-btn')?.addEventListener('click', () => {
  const texto = $('globo-texto-largo').textContent;
  navigator.clipboard?.writeText(texto);
  const btn = document.querySelector('.copiar-btn');
  const textoOriginal = btn.textContent;
  btn.textContent = '¡Copiado!';
  btn.disabled = true;
  setTimeout(() => {
    btn.textContent = textoOriginal;
    btn.disabled = false;
  }, 2000);
});

// ── Chat ─────────────────────────────────────────────────────────────────────
async function enviar(texto) {
  const input = $('input-chat');
  input.disabled = true;
  $('btn-enviar').disabled = true;
  decir('…', 'hablando');
  try {
    const resp = await fetch('/api/v1/chat', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: texto }),
    });
    if (resp.status === 429) { decir('Pará un poco, che. Probá en un ratito.', 'serio'); return; }
    if (!resp.ok) throw new Error(`POST /chat ${resp.status}`);
    const data = await resp.json();
    decir(data.reply, data.avatar_state);
    mostrarChips(data.tools_executed || []);
    mostrarTonoDetectado(data.tone_used);
    input.value = '';
    hideSuggestionCards();
    await cargarSesion().catch((e) => console.error('Error al recargar sesión:', e));
  } catch (e) {
    console.error(e);
    decir('Hubo un error, che. Probá de nuevo en un rato.', 'neutro');
  } finally {
    input.disabled = false;
    $('btn-enviar').disabled = false;
    input.focus();
  }
}
$('form-chat').addEventListener('submit', (e) => {
  e.preventDefault();
  const texto = $('input-chat').value.trim();
  if (texto) enviar(texto);
});
document.querySelectorAll('[data-prompt]').forEach((b) => b.addEventListener('click', () => {
  cerrarDrawer();
  hideSuggestionCards();
  enviar(b.dataset.prompt);
}));
$('btn-nueva').addEventListener('click', () => {
  cerrarDrawer();
  mostrarChips([]);
  showSuggestionCards();
  if (sesion?.greeting) decir(sesion.greeting.text, sesion.greeting.avatar_state);
  $('input-chat').focus();
});

// ── Drawer mobile (oculto por defecto, §1.2) ────────────────────────────────
function abrirDrawer() {
  $('sidebar').classList.add('abierta');
  $('backdrop').hidden = false;
  $('btn-menu').setAttribute('aria-expanded', 'true');
}
function cerrarDrawer() {
  $('sidebar').classList.remove('abierta');
  $('backdrop').hidden = true;
  $('btn-menu').setAttribute('aria-expanded', 'false');
}
$('btn-menu').addEventListener('click', abrirDrawer);
$('backdrop').addEventListener('click', cerrarDrawer);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') cerrarDrawer(); });

// ── Diálogos ─────────────────────────────────────────────────────────────────
$('btn-panel').addEventListener('click', () => $('dialog-panel').showModal());
document.querySelectorAll('[data-scenario]').forEach((b) => b.addEventListener('click', () => {
  fetch('/api/v1/proactive/trigger', {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scenario: b.dataset.scenario }),
  });
  $('dialog-panel').close();
}));

$('btn-ics').addEventListener('click', () => {
  if (!sesion) return;
  $('ics-url').textContent = location.origin + sesion.ics_url;
  $('dialog-ics').showModal();
});
$('btn-copiar-ics').addEventListener('click', () => navigator.clipboard?.writeText($('ics-url').textContent));
$('btn-revocar-ics').addEventListener('click', async () => {
  const resp = await fetch('/api/v1/calendar/revoke', { method: 'POST', credentials: 'same-origin' });
  if (!resp.ok) return;
  const data = await resp.json();
  sesion.ics_url = data.new_ics_url;
  $('ics-url').textContent = location.origin + data.new_ics_url;
});

// ── SSE (se abre después de la sesión) ───────────────────────────────────────
function conectarSSE() {
  eventSource = new EventSource('/api/v1/stream');
  eventSource.onopen = () => {
    $('puntito-conexion').classList.add('online');
    $('texto-conexion').textContent = 'Mate en línea';
  };
  eventSource.onerror = () => {
    $('puntito-conexion').classList.remove('online');
    $('texto-conexion').textContent = 'Reconectando…';
  };
  eventSource.addEventListener('proactive', (e) => {
    try {
      const data = JSON.parse(e.data);
      decir(data.message, data.avatar_state);
    } catch { /* evento mal formado: se ignora */ }
  });
}

// ── Month navigation ──────────────────────────────────────────────────────────
$('btn-mes-anterior').addEventListener('click', () => {
  calendarMonth = new Date(calendarMonth.getFullYear(), calendarMonth.getMonth() - 1, 1);
  renderCalendarGrid();
  renderDayList();
});

$('btn-mes-siguiente').addEventListener('click', () => {
  calendarMonth = new Date(calendarMonth.getFullYear(), calendarMonth.getMonth() + 1, 1);
  renderCalendarGrid();
  renderDayList();
});

// ── Suggestion cards hide/show logic ──────────────────────────────────────────
let firstMessageSent = false;
function hideSuggestionCards() {
  const tarjetas = document.querySelector('.tarjetas');
  if (tarjetas && !firstMessageSent) {
    tarjetas.classList.add('ocultas');
    firstMessageSent = true;
  }
}
function showSuggestionCards() {
  const tarjetas = document.querySelector('.tarjetas');
  if (tarjetas) {
    tarjetas.classList.remove('ocultas');
    firstMessageSent = false;
  }
}

// ── Arranque ─────────────────────────────────────────────────────────────────
(async function init() {
  aplicarTema(leerTema());
  try {
    await cargarSesion();
  } catch (e) {
    console.error(e);
    decir('No pude cargar tu sesión. Recargá la página.', 'serio');
  }
  // ── Logo sidebar: clon dinámica de #mate ─────────────────────────────────────
  (function setupSidebarLogo() {
    const $mate = $('mate');
    const $logo = $('sidebar-logo');
    if ($mate && $logo) {
      // Clonar el SVG, quitar id y data-state="contento", width 44px
      const $clone = $mate.cloneNode(true);
      $clone.removeAttribute('id');
      $clone.removeAttribute('data-state');
      $clone.setAttribute('data-state', 'contento');
      $clone.style.width = '44px';
      $clone.style.height = 'auto';
      // Limpiar clases que puedan conflictar, mantener solo las esenciales
      const classesToKeep = ['mate', 'm-cuerpo', 'm-brazos', 'm-bombilla', 'm-ojos', 'm-boca', 'm-virola', 'm-yerba'];
      const allClasses = $clone.classList;
      const keepSet = new Set(classesToKeep);
      const newClasses = Array.from(allClasses).filter(c => keepSet.has(c));
      allClasses.remove(...allClasses);
      newClasses.forEach(c => allClasses.add(c));
      $logo.innerHTML = ''; // Limpiar texto "Mate"
      $logo.appendChild($clone);
    }
  })();

  conectarSSE();
})();

// decir(): muestra el texto en el globo y pone al Mate a hablar un rato
function decir(texto) {
  const t = String(texto == null ? "" : texto);
  const largo = t.length > 90;
  const globo = document.getElementById("globo");
  const corto = document.getElementById("globo-texto");
  const lar = document.getElementById("globo-texto-largo");
  if (globo) globo.classList.toggle("largo", largo);
  if (corto) { corto.textContent = largo ? "" : t; corto.hidden = largo; }
  if (lar) { lar.textContent = largo ? t : ""; lar.hidden = !largo; }
  if (!corto && !lar && globo) globo.textContent = t;
  const mate = document.getElementById("mate");
  if (!mate) return;
  mate.setAttribute("data-state", "hablando");
  clearTimeout(decir._t);
  decir._t = setTimeout(function () {
    if (mate.getAttribute("data-state") === "hablando") mate.setAttribute("data-state", "neutro");
  }, Math.min(6000, 900 + t.length * 35));
}

// el saludo se muestra una sola vez; despues no pisa las respuestas
function decirSaludo(texto) {
  if (decirSaludo.ya) return;
  decirSaludo.ya = true;
  decir(texto);
}