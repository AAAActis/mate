// ===== Escena de Mate: cielo según la hora, personalización, mirada y saltito =====
// Cargar con <script src="/static/escena.js" defer></script> (después de app.js).
(function () {
  "use strict";

  const CLAVE = "mate-personal";
  const POR_DEFECTO = { calabaza: "clasica", virola: "alpaca", fondo: "auto" };

  function leer() {
    try { return Object.assign({}, POR_DEFECTO, JSON.parse(localStorage.getItem(CLAVE) || "{}")); }
    catch (e) { return Object.assign({}, POR_DEFECTO); }
  }
  function guardar(p) {
    try { localStorage.setItem(CLAVE, JSON.stringify(p)); } catch (e) { /* sin almacenamiento: no pasa nada */ }
  }

  // Momento del día en Córdoba
  function momentoActual() {
    const h = Number(new Intl.DateTimeFormat("en-GB", {
      hour: "2-digit", hourCycle: "h23", timeZone: "America/Argentina/Cordoba",
    }).format(new Date()));
    if (h >= 6 && h < 12) return "manana";
    if (h >= 12 && h < 19) return "tarde";
    return "noche";
  }

  function aplicar(p) {
    const escena = document.querySelector(".escena");
    const mates = document.querySelectorAll("svg.mate");   // el grande y el del logo si es un clon
    if (escena) escena.dataset.momento = p.fondo === "auto" ? momentoActual() : p.fondo;
    mates.forEach(function (m) {
      m.dataset.calabaza = p.calabaza;
      m.dataset.virola = p.virola;
    });
    document.querySelectorAll(".pp-opciones button").forEach(function (b) {
      b.setAttribute("aria-pressed", String(p[b.dataset.set] === b.dataset.valor));
    });
  }

  function iniciarPersonalizar(p) {
    const boton = document.querySelector(".btn-personalizar");
    const panel = document.getElementById("panel-personalizar");
    if (!boton || !panel) return;
    boton.addEventListener("click", function (e) {
      e.stopPropagation();
      panel.hidden = !panel.hidden;
    });
    document.addEventListener("click", function (e) {
      if (!panel.hidden && !panel.contains(e.target) && e.target !== boton) panel.hidden = true;
    });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") panel.hidden = true; });
    panel.addEventListener("click", function (e) {
      const b = e.target.closest("button[data-set]");
      if (!b) return;
      p[b.dataset.set] = b.dataset.valor;
      guardar(p);
      aplicar(p);
    });
  }

  // Los ojos siguen el cursor (se mueve el grupo .m-mirada como mucho 4 unidades del SVG)
  function iniciarMirada() {
    const mate = document.getElementById("mate");
    if (!mate || !mate.getScreenCTM) return;
    const mirada = mate.querySelector(".m-mirada");
    if (!mirada) return;
    let pendiente = false, x = 0, y = 0;
    window.addEventListener("mousemove", function (e) {
      x = e.clientX; y = e.clientY;
      if (pendiente) return;
      pendiente = true;
      requestAnimationFrame(function () {
        pendiente = false;
        const ctm = mate.getScreenCTM();
        if (!ctm) return;
        const cx = ctm.a * 120 + ctm.e;        // centro entre los ojos (120,162) en pantalla
        const cy = ctm.d * 162 + ctm.f;
        const dx = x - cx, dy = y - cy;
        const dist = Math.hypot(dx, dy) || 1;
        const k = Math.min(1, dist / 300) * 4;  // más lejos, más mira
        mirada.style.transform = "translate(" + (dx / dist * k).toFixed(2) + "px," + (dy / dist * k).toFixed(2) + "px)";
      });
    }, { passive: true });
    document.addEventListener("mouseleave", function () { mirada.style.transform = ""; });
  }

  // Clic en el Mate: saltito (solo animación)
  function iniciarSalto() {
    const mate = document.getElementById("mate");
    if (!mate) return;
    mate.addEventListener("click", function () {
      mate.classList.remove("salta");
      void mate.getBoundingClientRect();       // reinicia la animación
      mate.classList.add("salta");
    });
    mate.addEventListener("animationend", function (e) {
      if (e.animationName === "m-salto") mate.classList.remove("salta");
    });
  }

  function iniciar() {
    const p = leer();
    aplicar(p);
    iniciarPersonalizar(p);
    iniciarMirada();
    iniciarSalto();
    // si el fondo es automático, se actualiza cada 10 minutos
    setInterval(function () { if (leer().fondo === "auto") aplicar(leer()); }, 600000);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", iniciar);
  else iniciar();
})();
