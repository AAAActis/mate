// Mate — frontend. Orden de carga (§1): tema → GET /api/v1/session → EventSource.

const $ = (id) => document.getElementById(id);
const ESTADOS = ['neutro', 'hablando', 'contento', 'serio'];
let sesion = null;
let eventSource = null;

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
  $('mate').setAttribute('data-state', ESTADOS.includes(estado) ? estado : 'neutro');
}
function decir(texto, estado = 'hablando') {
  $('globo-texto').textContent = texto;
  setEstado(estado);
}
function mostrarChips(herramientas) {
  const cont = $('globo-chips');
  cont.replaceChildren();
  for (const h of herramientas) {
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.textContent = h.name;
    cont.appendChild(chip);
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
  const nombre = sesion.display_name || 'Vos';
  $('perfil-nombre').textContent = nombre;
  $('perfil-inicial').textContent = nombre.charAt(0).toUpperCase();
  marcarTono(sesion.tone);
  $('contador-cuota').textContent = String(sesion.session?.messages_count_today ?? 0);
  pintarAgenda();
  if (sesion.greeting) decir(sesion.greeting.text, sesion.greeting.avatar_state);
}

function hhmm(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' });
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

function pintarAgenda() {
  const lista = $('lista-agenda');
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
  $('agenda-vacia').hidden = total > 0;
  $('resumen-agenda').textContent = total === 0
    ? 'Nada agendado por ahora.'
    : `${blocks.length} bloques, ${tasks.length} tareas y ${habits.length} hábitos.`;
}

async function completar(tipo, id) {
  const resp = await fetch(`/api/v1/items/${tipo}/${id}/complete`, { method: 'POST', credentials: 'same-origin' });
  if (!resp.ok) return;
  const data = await resp.json();
  decir(data.message, data.avatar_state);
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
    input.value = '';
    await cargarSesion().catch(() => {});
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
  enviar(b.dataset.prompt);
}));
$('btn-nueva').addEventListener('click', () => {
  cerrarDrawer();
  mostrarChips([]);
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

// ── Arranque ─────────────────────────────────────────────────────────────────
(async function init() {
  aplicarTema(leerTema());
  try {
    await cargarSesion();
  } catch (e) {
    console.error(e);
    decir('No pude cargar tu sesión. Recargá la página.', 'serio');
  }
  conectarSSE();
})();
