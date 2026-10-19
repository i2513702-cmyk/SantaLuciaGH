/* Santa Lucía — visor lightbox de producto (PhotoSwipe v5).
 *
 * Se inicializa sobre la ficha de producto: usa la galería (thumbs) como
 * fuente de datos y abre el visor al hacer clic en la imagen principal o en
 * el ícono de lupa. Carga las dimensiones reales de cada imagen antes de
 * abrir para que el zoom y el ajuste a pantalla sean correctos.
 */
(function () {
  'use strict';

  var root = document.querySelector('[data-galeria]');
  if (!root) return;

  var mainImg = root.querySelector('[data-galeria-img]');
  var thumbs = Array.prototype.slice.call(root.querySelectorAll('[data-galeria-thumb]'));
  var zoomIcon = root.querySelector('.pd-zoom-icon');

  // URLs de la galería (thumbs); si no hay thumbs, se usa la imagen principal.
  var urls = thumbs.map(function (t) {
    return t.getAttribute('data-large_image') || t.getAttribute('data-src') || t.getAttribute('href');
  });
  if (!urls.length && mainImg) {
    urls = [mainImg.getAttribute('data-large_image') || mainImg.getAttribute('src')];
  }
  if (!urls.length) return;

  function loadDims(url) {
    return new Promise(function (resolve) {
      var img = new Image();
      img.onload = function () {
        resolve({ src: url, width: img.naturalWidth || 1600, height: img.naturalHeight || 1200 });
      };
      img.onerror = function () {
        resolve({ src: url, width: 1600, height: 1200 });
      };
      img.src = url;
    });
  }

  Promise.all(urls.map(loadDims)).then(function (items) {
    var dataSource = items.map(function (it, i) {
      var thumbUrl = (thumbs[i] && (thumbs[i].getAttribute('data-large_image') ||
                      thumbs[i].getAttribute('data-src') || thumbs[i].getAttribute('href'))) || it.src;
      return {
        src: it.src,
        width: it.width,
        height: it.height,
        msrc: thumbUrl,
        alt: (mainImg && mainImg.getAttribute('alt')) || ''
      };
    });

    import('/static/vendor/photoswipe/photoswipe-lightbox.esm.min.js').then(function (mod) {
      var PhotoSwipeLightbox = mod.default;
      var lightbox = new PhotoSwipeLightbox({
        dataSource: dataSource,
        pswpModule: function () {
          return import('/static/vendor/photoswipe/photoswipe.esm.min.js');
        },
        bgOpacity: 1,
        showHideAnimationType: 'zoom',
        counter: true,
        arrowPrev: true,
        arrowNext: true,
        zoom: true,
        close: true,
        loop: true
      });

      // Botón de pantalla completa (UI personalizada).
      lightbox.on('uiRegister', function () {
        var pswp = lightbox.pswp;
        if (!pswp || !pswp.ui) return;
        pswp.ui.registerElement({
          name: 'fullscreen',
          order: 15,
          isButton: true,
          tagName: 'button',
          title: 'Pantalla completa',
          html: {
            isCustomSVG: true,
            inner: '<path d="M4 4h6v2H6v4H4V4zm16 0h6v6h-2V6h-4V4zM4 20h2v4h4v2H4v-6zm16 6h-4v-2h4v-4h2v6h-6z" id="pswp__icn-fullscreen"/>',
            outlineID: 'pswp__icn-fullscreen'
          },
          onClick: function (e, btn, pswp) {
            var el = pswp.element;
            if (!document.fullscreenElement) {
              var fn = el.requestFullscreen || el.webkitRequestFullscreen;
              if (fn) fn.call(el);
            } else {
              var ex = document.exitFullscreen || document.webkitExitFullscreen;
              if (ex) ex.call(document);
            }
          }
        });
      });

      lightbox.init();

      function currentIndex() {
        var cur = 0;
        thumbs.forEach(function (t, n) {
          if (t.getAttribute('aria-current') === 'true') cur = n;
        });
        return cur;
      }

      function openAt(i) {
        var idx = Math.max(0, Math.min(i, dataSource.length - 1));
        lightbox.loadAndOpen(idx);
      }

      if (mainImg) {
        mainImg.style.cursor = 'zoom-in';
        mainImg.addEventListener('click', function (e) {
          e.preventDefault();
          openAt(currentIndex());
        });
      }
      if (zoomIcon) {
        zoomIcon.addEventListener('click', function (e) {
          e.preventDefault();
          e.stopPropagation();
          openAt(currentIndex());
        });
      }
    }).catch(function (err) {
      // eslint-disable-next-line no-console
      console.error('No se pudo cargar PhotoSwipe:', err);
    });
  });
})();
