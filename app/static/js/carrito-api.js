/* Carrito vía API REST /api/v1 (integración real, no simulada).
 *
 * Intercepta los formularios/botones marcados con [data-carrito-api] y hace
 * Fetch al endpoint que ya existe en el backend:
 *   POST   /api/v1/cart/items            {producto_id, cantidad}   -> 201
 *   PATCH  /api/v1/cart/items/<item_id>  {cantidad} | {accion}     -> 200
 *   DELETE /api/v1/cart/items/<item_id>                            -> 200
 *   DELETE /api/v1/cart                                            -> 204
 *
 * La petición es real: guarda en Supabase vía carrito_service, se ve en
 * DevTools > Network > Fetch/XHR, usa la cookie de sesión (credentials)
 * y, si el cliente tiene JWT, también acepta Bearer en el servidor.
 * Si JS está desactivado, el <form> conserva su action tradicional
 * (/carrito/agregar/<id>) como respaldo sin romper nada.
 */
(function () {
    'use strict';

    function mostrarMensaje(texto, tipo) {
        // Reutiliza el contenedor de flash de base.html; si no existe, crea uno.
        var main = document.querySelector('main');
        if (!main) return;
        var alertType = tipo === 'error' ? 'danger' : (tipo || 'success');
        var div = document.createElement('div');
        div.className = 'alert alert-' + alertType + ' alert-dismissible fade show';
        div.setAttribute('role', 'alert');
        div.textContent = texto;
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn-close';
        btn.setAttribute('data-bs-dismiss', 'alert');
        btn.setAttribute('aria-label', 'Cerrar');
        div.appendChild(btn);
        main.prepend(div);
        if (window.bootstrap) {
            try { new bootstrap.Alert(div); } catch (e) { /* sin Bootstrap: igual se muestra */ }
        }
    }

    function actualizarBadge(nItems) {
        var badge = document.getElementById('carrito-badge');
        if (!badge) return;
        if (nItems > 0) {
            badge.textContent = String(nItems);
            badge.style.display = '';
        } else {
            badge.style.display = 'none';
        }
    }

    function parseJson(response) {
        return response.text().then(function (texto) {
            var datos = {};
            try { datos = texto ? JSON.parse(texto) : {}; } catch (e) {
                datos = { mensaje: 'Respuesta no válida del servidor (' + response.status + ').' };
            }
            return { status: response.status, ok: response.ok, datos: datos };
        });
    }

    function setOcupado(form, ocupado) {
        var btn = form.querySelector('button[type="submit"], button:not([type])');
        if (!btn) return;
        if (ocupado) {
            btn.dataset.textoOriginal = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span> Agregando...';
        } else {
            btn.disabled = false;
            if (btn.dataset.textoOriginal) btn.innerHTML = btn.dataset.textoOriginal;
        }
    }

    /* ---------- Agregar (detalle de producto + tienda) ---------- */
    document.querySelectorAll('form[data-carrito-api="agregar"]').forEach(function (form) {
        form.addEventListener('submit', function (ev) {
            ev.preventDefault();
            var productoId = parseInt(form.getAttribute('data-producto-id'), 10);
            var cantidadInput = form.querySelector('[name="cantidad"]');
            var cantidad = cantidadInput ? parseInt(cantidadInput.value, 10) : 1;
            if (!productoId) return;
            setOcupado(form, true);
            fetch('/api/v1/cart/items', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
                body: JSON.stringify({ producto_id: productoId, cantidad: cantidad || 1 })
            })
                .then(parseJson)
                .then(function (res) {
                    if (res.ok) {
                        mostrarMensaje(res.datos.mensaje || 'Producto agregado al carrito.', 'success');
                        if (typeof res.datos.n_items === 'number') actualizarBadge(res.datos.n_items);
                    } else {
                        mostrarMensaje(res.datos.mensaje || res.datos.error || 'No se pudo agregar el producto al carrito.', 'error');
                    }
                })
                .catch(function () {
                    mostrarMensaje('No se pudo contactar al servidor. Inténtalo de nuevo.', 'error');
                })
                .then(function () { setOcupado(form, false); });
        });
    });

    /* ---------- Actualizar cantidad / quitar (página /carrito) ---------- */
    document.querySelectorAll('[data-carrito-accion]').forEach(function (el) {
        el.addEventListener('click', function (ev) {
            ev.preventDefault();
            var itemId = el.getAttribute('data-item-id');
            var accion = el.getAttribute('data-carrito-accion'); // incrementar|decrementar|quitar
            if (!itemId) return;
            var esQuitar = accion === 'quitar';
            var primer = esQuitar
                ? fetch('/api/v1/cart/items/' + encodeURIComponent(itemId), {
                    method: 'DELETE', credentials: 'same-origin',
                    headers: { 'Accept': 'application/json' }
                  })
                : fetch('/api/v1/cart/items/' + encodeURIComponent(itemId), {
                    method: 'PATCH', credentials: 'same-origin',
                    headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
                    body: JSON.stringify({ accion: accion })
                  });
            primer.then(parseJson).then(function (res) {
                if (res.ok) {
                    if (typeof res.datos.n_items === 'number') actualizarBadge(res.datos.n_items);
                    window.location.reload(); // re-render server-side con los totales reales
                } else {
                    mostrarMensaje(res.datos.mensaje || res.datos.error || 'No se pudo actualizar el carrito.', 'error');
                }
            }).catch(function () {
                mostrarMensaje('No se pudo contactar al servidor. Inténtalo de nuevo.', 'error');
            });
        });
    });

    /* ---------- Vaciar carrito ---------- */
    document.querySelectorAll('form[data-carrito-api="vaciar"]').forEach(function (form) {
        form.addEventListener('submit', function (ev) {
            ev.preventDefault();
            fetch('/api/v1/cart', {
                method: 'DELETE', credentials: 'same-origin',
                headers: { 'Accept': 'application/json' }
            }).then(function (res) {
                if (res.ok || res.status === 204) {
                    actualizarBadge(0);
                    window.location.reload();
                } else {
                    return parseJson(res).then(function (r) {
                        mostrarMensaje(r.datos.mensaje || r.datos.error || 'No se pudo vaciar el carrito.', 'error');
                    });
                }
            }).catch(function () {
                mostrarMensaje('No se pudo contactar al servidor. Inténtalo de nuevo.', 'error');
            });
        });
    });
})();
