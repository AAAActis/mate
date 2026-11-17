// App principal Mate - lógica completa (Zonas 2, 3, 4 y 5)

// Estado global
let eventSource = null;
let temaOscuro = false;
let estadoActual = 'neutro';
let sesionActual = null;
let voces = [];

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
const globoChip = document.getElementById('globo-chip');

// Elementos DOM de Zona 4
const btnIcsSubscribir = document.getElementById('btn-ics-subscribir');
const btnIcsRevocar = document.getElementById('btn-ics-revocar');
const btnCopiarIcs = document.getElementById('btn-copiar-ics');
const displayIcsUrl = document.getElementById('ics-url-display');
const dialogIcs = document.getElementById('dialog-ics');

// Elementos DOM de Zona 5
const btnEnviar = document.getElementById('btn-enviar');
const inputChat = document.getElementById('input-chat');

/// --- Función 1: setEstado(estado) ---
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

/// --- Función 2: decir(texto) ---
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
    if (voces.length === 0) {
      voces = window.speechSynthesis.getVoices();
    }
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

// Cargar voces disponibles al iniciar
document.addEventListener('DOMContentLoaded', () => {
  if ('speechSynthesis' in window) {
    voces = window.speechSynthesis.getVoices();
  }
  conectarSSE();
});

// Cargar sesión y renderizar agenda (Zona 4)
async function cargarAgenda() {
  try {
    const respuesta = await fetch('/api/v1/session', {
      credentials: 'same-origin',
    });

    if (!respuesta.ok) throw new Error('No se pudo cargar la sesión');

    sesionActual = await respuesta.json();

    // Listar tasks, habits y blocks
    const listaEventos = document.getElementById('lista-eventos');
    if (!listaEventos) return;

    listaEventos.innerHTML = '';

    // Tareas
    if (sesionActual && sesionActual.tasks) {
      for (const tarea of sesionActual.tasks) {
        const div = document.createElement('div');
        div.style = 'padding: 4px 8px; margin: 2px 0; border-radius: 10px; background: var(--bg-card);';
        div.innerHTML = `
          <span style="color: var(--yerba-olive);">📋</span> ${tarea.title}
          <span style="font-size: 0.7rem; color: var(--text-secondary); margin-left: 4px;">${tarea.due_at || ''}</span>
          <button class="btn-completar" data-tipo="task" data-id="${tarea.id}" style="background: none; border: none; color: var(--yerba-olive); font-size: 0.8rem; margin-left: 8px;">✓</button>
        `;
        listaEventos.appendChild(div);

        // Event listener para botón ✓
        const btnCompletar = div.querySelector('.btn-completar');
        btnCompletar.addEventListener('click', async () => {
          const resp = await fetch(`/api/v1/items/task/${tarea.id}/complete`, {
            method: 'POST',
            credentials: 'same-origin',
          });
          if (resp.ok) {
            const data = await resp.json();
            if (data.ok) {
              setEstado('contento');
              decir('¡Bien ahí!');
            }
          }
        });
      }
    }

    // Hábitos
    if (sesionActual && sesionActual.habits) {
      for (const habit of sesionActual.habits) {
        const div = document.createElement('div');
        div.style = 'padding: 4px 8px; margin: 2px 0; border-radius: 10px; background: var(--bg-card);';
        div.innerHTML = `
          <span style="color: var(--yerba-olive);">💪</span> ${habit.title}
          <span style="font-size: 0.7rem; color: var(--text-secondary); margin-left: 4px;">${habit.frequency || ''}</button>
        `;
        listaEventos.appendChild(div);

        const btnCompletar = div.querySelector('.btn-completar');
        if (btnCompletar) {
          btnCompletar.addEventListener('click', async () => {
            const resp = await fetch(`/api/v1/items/habit/${habit.id}/complete`, {
              method: 'POST',
              credentials: 'same-origin',
            });
            if (resp.ok) {
              const data = await resp.json();
              if (data.ok) {
                setEstado('contento');
                decir('¡Bien ahí!');
              }
            }
          });
        }
      }
    }

    // Bloques de calendario
    if (sesionActual && sesionActual.calendar_blocks) {
      for (const bloque of sesionActual.calendar_blocks) {
        const div = document.createElement('div');
        div.style = 'padding: 4px 8px; margin: 2px 0; border-radius: 10px; background: var(--bg-card);';
        div.innerHTML = `
          <span style="color: var(--gourd-brown);">📅</span> ${bloque.title}
          <span style="font-size: 0.7rem; color: var(--text-secondary); margin-left: 4px;">${bloque.start_at} - ${bloque.end_at || ''}</button>
        `;
        listaEventos.appendChild(div);

        const btnCompletar = div.querySelector('.btn-completar');
        if (btnCompletar) {
          btnCompletar.addEventListener('click', async () => {
            const resp = await fetch(`/api/v1/items/calendar_block/${bloque.id}/complete`, {
              method: 'POST',
              credentials: 'same-origin',
            });
            if (resp.ok) {
              const data = await resp.json();
              if (data.ok) {
                setEstado('contento');
                decir('¡Bien ahí!');
              }
            }
          });
        }
      }
    }
  } catch (error) {
    console.error('Error al cargar agenda:', error);
  }
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

// --- Zona 5: Barra de chat y tarjetas de acción ---

// Función para enviar mensaje al chat (tanto de tarjetas como de input manual)
async function enviarMensajeChat(textoUsuario) {
  // Ocultar input y botón durante petición
  if (inputChat) inputChat.disabled = true;
  if (btnEnviar) btnEnviar.disabled = true;

  try {
    const respuesta = await fetch('/api/v1/chat', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ mensaje: textoUsuario }),
    });

    if (respuesta.status === 429) {
      // Límite de tokens alcanzado
      decir('Pará un poco, che, probá en un ratito');
      setEstado('neutro');
      return;
    }

    if (!respuesta.ok) throw new Error('Error en la respuesta');

    const data = await respuesta.json();
    const reply = data.respuesta || 'Sin respuesta del agente';

    // Mostrar respuesta en globo
    setEstado('hablando');
    decir(reply);

    // Después de un delay, actualizar estado y refrescar agenda
    setTimeout(async () => {
      // Si hubo herramientas ejecutadas, mostrálas como chips
      if (data.herramientas_ejecutadas && data.herramientas_ejecutadas.length > 0) {
        mostrarChipsHerramientas(data.herramientas_ejecutadas);
      }
      // Refrescar agenda
      await cargarAgenda();
      // Volver al estado neutro después de un momento
      setEstado('neutro');
    }, 1500);

  } catch (error) {
    console.error('Error al enviar mensaje:', error);
    decir('Hubo un error, che. Intentá de nuevo más tarde.');
    setEstado('neutro');
  } finally {
    // Rehabilitar input y botón
    if (inputChat) inputChat.disabled = false;
    if (btnEnviar) btnEnviar.disabled = false;
    // Limpiar input
    if (inputChat) inputChat.value = '';
  }
}

// Mostrar herramientas ejecutadas como chips debajo del globo
function mostrarChipsHerramientas(herramientas) {
  if (!globoChip) return;

  // Limpiar chips anteriores
  globoChip.innerHTML = '';

  // Crear chips para cada herramienta
  herramientas.forEach(herramienta => {
    const chip = document.createElement('span');
    chip.style = 'display: inline-block; padding: 4px 8px; margin: 2px; background: var(--bg-card); border: 1px solid var(--border-warm); border-radius: 10px; font-size: 0.75rem; color: var(--text-primary);';
    chip.textContent = `✓ ${herramienta}`;
    globoChip.appendChild(chip);
  });
}

// Event listener para botón enviar (input manual)
btnEnviar.addEventListener('click', () => {
  if (inputChat && inputChat.value.trim()) {
    enviarMensajeChat(inputChat.value.trim());
  }
});

// Event listener para Enter en input
if (inputChat) {
  inputChat.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
      if (inputChat.value.trim()) {
        enviarMensajeChat(inputChat.value.trim());
      }
    }
  });
}

// Cargar agenda al inicializar
document.addEventListener('DOMContentLoaded', () => {
  cargarAgenda();
});

// Abrir diálogo ICS
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

// Cerrar diálogo
btnCerrarDialogo.addEventListener('click', () => {
  if (dialogPanel) {
    dialogPanel.close();
  }
});

// Botón Suscribir .ics - abre diálogo
btnIcsSubscribir.addEventListener('click', () => {
  if (sesionActual && sesionActual.calendar_token) {
    const urlIcs = `/api/v1/calendar/${sesionActual.calendar_token}.ics`;
    const display = document.getElementById('ics-url-display');
    if (display) {
      display.textContent = urlIcs;
    }
    const dialogIcs = document.getElementById('dialog-ics');
    if (dialogIcs) {
      dialogIcs.showModal();
    }
  }
});

// Copiar URL del ICS
btnCopiarIcs.addEventListener('click', () => {
  const display = document.getElementById('ics-url-display');
  if (display) {
    navigator.clipboard.writeText(display.textContent).then(() => {
      // Feedback visual corto
      display.style.background = 'var(--yerba-olive)';
      setTimeout(() => display.style.background = 'var(--bg-sidebar)', 1000);
    });
  }
});

// Revocar token ICS
btnRevocarIcs.addEventListener('click', () => {
  fetch('/api/v1/calendar/revoke', {
    method: 'POST',
    credentials: 'same-origin',
  }).then(() => {
    // Ocultar diálogo y botones
    const dialogIcs = document.getElementById('dialog-ics');
    if (dialogIcs) dialogIcs.close();
    const btnIcsRevocar = document.getElementById('btn-ics-revocar');
    const btnIcsSuscribir = document.getElementById('btn-ics-subscribir');
    if (btnIcsRevocar) btnIcsRevocar.style.display = 'none';
    if (btnIcsSuscribir) btnIcsSuscribir.style.display = 'inline-block';
  });
});