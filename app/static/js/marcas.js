/**
 * Marcas — carrusel en bucle continuo.
 *
 * El HTML renderiza 4 copias del grupo de marcas y la animación avanza un grupo
 * exacto, así el bucle no deja huecos. Aquí solo se asegurar dos cosas:
 *
 *   1. que haya suficientes copias para cubrir el doble del ancho visible
 *      (si hay pocas marcas, el track se quedaría corto y se vería el vacío);
 *   2. que --marcas-grupos coincida con las copias realmente presentes, porque
 *      de él depende el desplazamiento exacto de un grupo.
 *
 * Sin dependencias; corre en cuanto existe el DOM.
 */
(function () {
  const VISTAS_MINIMAS = 2; // el track debe cubrir al menos 2 veces el viewport

  function ajustar(track) {
    var grupo = track.querySelector(".marcas-group");
    var viewport = track.parentElement;
    if (!grupo || !viewport) return;

    var anchoGrupo = grupo.offsetWidth;
    if (!anchoGrupo) return;

    // Cubrir el viewport actual + un grupo de reserva siempre en pantalla.
    var necesarias = Math.ceil((viewport.clientWidth * VISTAS_MINIMAS) / anchoGrupo) + 1;

    while (track.children.length < necesarias) {
      var copia = grupo.cloneNode(true);
      copia.setAttribute("aria-hidden", "true");
      track.appendChild(copia);
    }

    track.style.setProperty("--marcas-grupos", String(track.children.length));
  }

  function iniciar() {
    var tracks = document.querySelectorAll(".marcas-track");
    if (!tracks.length) return;

    tracks.forEach(function (track) {
      ajustar(track);

      // Las fuentes web cambian el ancho de las tarjetas: reajustar al cargar.
      if (document.fonts && document.fonts.ready) {
        document.fonts.ready.then(function () { ajustar(track); });
      }

      var temporizador;
      window.addEventListener("resize", function () {
        clearTimeout(temporizador);
        temporizador = setTimeout(function () { ajustar(track); }, 150);
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", iniciar);
  } else {
    iniciar();
  }
})();