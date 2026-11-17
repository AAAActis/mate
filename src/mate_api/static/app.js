// App principal Mate - lógica completa (Zonas 2 y 3)

// Estado global
let eventSource = null;
let temaOscuro = false;
let estadoActual = 'neutro';

// Elementos DOM de Zona 2
const puntitoConexion = document.getElementById('puntito-conexion');
const textoConexion = document.getElementById('texto-conexion');
const contadorCuota = document.getElementById('contador-cuota');
const btnSolLuna = document.getElementById('btn-sol-luna');
const dialogPanel = document.getElementById('dialog-panel');
const btnCerrarDialogo = document.getElementById('btn-cerrar-dialogo');
const btnSimularGym = document.getElementById('btn-simular-gym');
const btnSimularEstudio = document.getElementById('btn-simular-estudio');
const btnSimularCumple = document.getElementById('btn-simular-cumple');
const personajeMate = document.getElementById('personaje-mate');

// Elementos DOM de Zona 3
const globoTexto = document.getElementById('globo-texto');

// --- Función 1: setEstado(estado) ---
/**
 * Cambia el estado visual del Mate (data-state en SVG).
 * Estados: 'neutro', 'hablando', 'contento', 'serio'.
 * Respeta prefers-reduced-motion.
 * @param {'neutro' | 'hablando' | 'contento' | 'serio'} estado
 */
function setEstado(estado) {
  const estadosValidos = ['neutro', 'hablando', 'contento', 'serio'];
  if (!estadosValidos.includes(estado)) {
    console.error(`setEstado: estado inválido "${estado}"`);
    return;
  }

  // Actualizar data-state en el SVG del Mate
  if (personajeMate) {
    personajeMate.setAttribute('data-state', estado);
  }

  estadoActual = estado;

  // Transición suave respetando prefers-reduced-motion
  if (personajeMate) {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const duracion = reduced ? 0 : 300;
    personajeMate.style.transition = `all ${duracion}ms cubic-bezier(0.22, 1, 0.36, 1)`;
  }
}

// --- Función 2: decir(texto) ---
/**
 * Muestra texto en el globo de diálogo y habla con speechSynthesis.
 * Usa idioma es-AR si está disponible, sino español por defecto.
 * @param {string} texto
 */
function decir(texto) {
  if (!globoTexto) return;

  // Mostrar en globo con textContent (anti-XSS)
  globoTexto.textContent = texto;

  // Intentar speechSynthesis con es-AR
  if ('speechSynthesis' in window) {
    const voz = voces.find(v => v.lang === 'es-AR') ||
                voces.find(v => v.lang.startsWith('es')) ||
               voices[0];

    if (voz) {
      const utterance = new SpeechSynthesisUtterance(texto);
      utterance.lang = voz.lang || 'es-AR';
      utterance.rate = 1;
      utterance.pitch = 1;
      speechSynthesis.speak(utterance);
    } else {
      // Fallback sin idioma específico
      const utterance = new SpeechSynthesisUtterance(texto);
      utterance.lang = 'es-AR';
      utterance.rate = 0.9;
      utterance.pitch = 1;
      speechSynthesis.speak(utterance);
    }
  }
}

// Estado global SSE
let voces = [];

// Cargar voces disponibles al iniciar
document.addEventListener('DOMContentLoaded', () => {
  if ('speechSynthesis' in window) {
    voces = window.speechSynthesis.getVoices();
  }
  conectarSSE();
});

// Conectar EventSource a /api/v1/stream
function conectarSSE() {
  // Cerrar conexión anterior si existe
  if (eventSource) {
    eventSource.close();
  }

  eventSource = new EventSource('/api/v1/stream');

  eventSource.onopen = () => {
    // Conexión abierta: puntito verde sólido, texto actualizado
    puntitoConexion.style.background = 'var(--yerba-fresh)';
    puntitoConexion.style.boxShadow = '0 0 2px 1px var(--yerba-fresh)';
    textoConexion.textContent = 'Mate en línea';
    textoConexion.style.color = 'var(--text-primary)';
  };

  eventSource.onmessage = (event) => {
    const datos = event.data;
    // El backend envía mensajes SSE como "Mate en línea: ..." o avisos proactivos
    if (datos.includes('¡Hola') || datos.includes('Che')) {
      // Mostrar en el globo de diálogo
      mostrarEnGlobo(datos);
      // Cambiar estado a "hablando" mientras habla
      setEstado('hablando');
      // Hablar después de un pequeño delay
      setTimeout(() => decir(datos), 200);
    }
    // El contador de mensajes podría actualizarse aquí
    if (contadorCuota) {
      const actual = parseInt(contadorCuota.textContent) || 0;
      contadorCuota.textContent = Math.min(actual + 1, 20);
    }
  };

  eventSource.onerror = () => {
    // Corte de conexión: puntito gris, texto actualizado
    puntitoConexion.style.background = '#777';
    puntitoConexion.style.boxShadow = 'none';
    textoConexion.textContent = 'Desconectado';
    textoConexion.style.color = '#aaa';
    // No cerramos el Source aquí - se reconectará automáticamente o al recargar
    setEstado('neutro');
  };
}

// Mostrar mensaje en el globo de diálogo
function mostrarEnGlobo(mensaje) {
  if (!globoTexto) return;
  // Transición suave de palabras - fade out old, fade in new
  globoTexto.style.opacity = '0';
  globoTexto.textContent = mensaje;
  globoTexto.style.opacity = '1';
}

// Toggle sol/luna - alterna data-theme="dark-yerba" en <html>
btnSolLuna.addEventListener('click', () => {
  const html = document.documentElement;
  temaOscuro = !temaOscuro;

  if (temaOscuro) {
    html.setAttribute('data-theme', 'dark-yerba');
    btnSolLuna.textContent = '☀️';
  } else {
    html.removeAttribute('data-theme');
    btnSolLuna.textContent = '🌙';
  }
});

// Abrir diálogo del panel ⚙
btnPanel.addEventListener('click', () => {
  if (dialogPanel) {
    dialogPanel.showModal();
  }
});

// Cerrar diálogo
btnCerrarDialogo.addEventListener('click', () => {
  if (dialogPanel) {
    dialogPanel.close();
  }
});

// Simular avisos proactivos SSE
btnSimularGym.addEventListener('click', () => {
  if (eventSource && eventSource.readyState === EventSource.OPEN) {
    eventSource.send('gym');
  }
  mostrarEnGlobo('✓ Aviso proactivo: sesión de gym programada para hoy a las 19:00 hs');
  setEstado('contento');
});

btnSimularEstudio.addEventListener('click', () => {
  if (eventSource && eventSource.readyState === EventSource.OPEN) {
    eventSource.send('estudio');
  }
  mostrarEnGlobo('✓ Aviso proactivo: hora de estudiar Bases de Datos - ¡arrancamos?');
  setEstado('contento');
});

btnSimularCumple.addEventListener('click', () => {
  if (eventSource && eventSource.readyState === EventSource.OPEN) {
    eventSource.send('cumple');
  }
  mostrarEnGlobo('✓ Aviso proactivo: cumpleaños en 6 días - recordatorio guardado');
  setEstado('contento');
});