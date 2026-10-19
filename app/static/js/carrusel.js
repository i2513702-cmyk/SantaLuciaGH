/* Santa Lucía — componentes interactivos del catálogo.
 *
 * 1) Carrusel horizontal de productos (`.carrusel`): flechas + puntos.
 *    Funciona también sin JS porque la pista usa scroll nativo.
 * 2) Galería de la ficha de producto (`[data-galeria]`): miniaturas, flechas,
 *    contador, teclado y gesto táctil para ver el producto desde varios ángulos.
 *
 * Sin dependencias: solo DOM y scroll nativos.
 */
(function () {
  'use strict';

  /* ====================================================================
     1) Carrusel de productos
     ==================================================================== */
  document.querySelectorAll('[data-carrusel]').forEach(function (raiz) {
    var track = raiz.querySelector('[data-carrusel-track]');
    var prev = raiz.querySelector('[data-carrusel-prev]');
    var next = raiz.querySelector('[data-carrusel-next]');
    var dots = raiz.querySelector('[data-carrusel-dots]');
    if (!track || !prev || !next) return;

    function paso() {
      var items = track.children;
      if (!items.length) return track.clientWidth || 1;
      var estilo = window.getComputedStyle(track);
      var gap = parseFloat(estilo.columnGap || estilo.gap) || 0;
      return items[0].getBoundingClientRect().width + gap;
    }

    function totalPaginas() {
      var p = paso();
      if (p <= 0) return 1;
      return Math.max(1, Math.ceil(track.scrollWidth / p - 0.15));
    }

    function paginaActual() {
      var p = paso();
      return p > 0 ? Math.round(track.scrollLeft / p) : 0;
    }

    function irA(indice) {
      var ultimo = totalPaginas() - 1;
      var destino = Math.max(0, Math.min(indice, ultimo));
      track.scrollTo({ left: destino * paso(), behavior: 'smooth' });
    }

    function pintar() {
      var total = totalPaginas();
      var actual = Math.min(Math.max(paginaActual(), 0), total - 1);
      prev.disabled = track.scrollLeft <= 2;
      next.disabled = track.scrollLeft + track.clientWidth >= track.scrollWidth - 2;

      if (!dots) return;
      if (dots.children.length !== total) {
        dots.innerHTML = '';
        for (var i = 0; i < total; i++) {
          (function (n) {
            var b = document.createElement('button');
            b.type = 'button';
            b.setAttribute('aria-label', 'Ir al grupo ' + (n + 1) + ' de ' + total);
            b.addEventListener('click', function () { irA(n); });
            dots.appendChild(b);
          })(i);
        }
      }
      Array.prototype.forEach.call(dots.children, function (b, i) {
        b.setAttribute('aria-current', i === actual ? 'true' : 'false');
      });
    }

    prev.addEventListener('click', function () { irA(paginaActual() - 1); });
    next.addEventListener('click', function () { irA(paginaActual() + 1); });

    var pendiente = false;
    track.addEventListener('scroll', function () {
      if (pendiente) return;
      pendiente = true;
      window.requestAnimationFrame(function () {
        pendiente = false;
        pintar();
      });
    }, { passive: true });

    window.addEventListener('resize', pintar);
    pintar();
  });

  /* ====================================================================
     2) Galería de la ficha de producto
     ==================================================================== */
  document.querySelectorAll('[data-galeria]').forEach(function (raiz) {
    var img = raiz.querySelector('[data-galeria-img]');
    var thumbs = Array.prototype.slice.call(
      raiz.querySelectorAll('[data-galeria-thumb]')
    );
    if (!img || thumbs.length < 2) return;

    var prev = raiz.querySelector('[data-galeria-prev]');
    var next = raiz.querySelector('[data-galeria-next]');
    var contador = raiz.querySelector('[data-galeria-count]');
    var indice = 0;

    function mostrar(i) {
      indice = (i + thumbs.length) % thumbs.length;
      var destino = thumbs[indice].getAttribute('data-src');
      if (destino) img.setAttribute('src', destino);
      thumbs.forEach(function (t, n) {
        t.setAttribute('aria-current', n === indice ? 'true' : 'false');
      });
      if (contador) contador.textContent = (indice + 1) + ' / ' + thumbs.length;
    }

    thumbs.forEach(function (t, n) {
      t.addEventListener('click', function (ev) {
        ev.preventDefault();
        mostrar(n);
      });
    });

    if (prev) prev.addEventListener('click', function () { mostrar(indice - 1); });
    if (next) next.addEventListener('click', function () { mostrar(indice + 1); });

    raiz.addEventListener('keydown', function (ev) {
      if (document.querySelector('.pswp--open')) return;
      if (ev.key === 'ArrowLeft') { ev.preventDefault(); mostrar(indice - 1); }
      if (ev.key === 'ArrowRight') { ev.preventDefault(); mostrar(indice + 1); }
    });

    // Gesto táctil: deslizar para cambiar de foto
    var x0 = null;
    raiz.addEventListener('touchstart', function (ev) {
      x0 = ev.changedTouches[0].clientX;
    }, { passive: true });
    raiz.addEventListener('touchend', function (ev) {
      if (x0 === null) return;
      var dx = ev.changedTouches[0].clientX - x0;
      if (Math.abs(dx) > 40) mostrar(indice + (dx < 0 ? 1 : -1));
      x0 = null;
    }, { passive: true });

    mostrar(0);
  });
})();
