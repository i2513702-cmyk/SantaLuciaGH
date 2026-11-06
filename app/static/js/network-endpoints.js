/**
 * Santa Lucía — Telemetría y sincronización de endpoints nativos del sistema.
 *
 * Dispara en segundo plano las llamadas Fetch/XHR hacia el blueprint real
 * `/api/*` según la vista actual de Flask, permitiendo inspeccionar la
 * respuesta JSON de cada módulo en DevTools -> Network (Fetch/XHR) sin
 * duplicar ni interferir con los endpoints ya integrados en las plantillas:
 *   - /api/clientes/buscar/<numero_documento> (autocompletado-cliente.js)
 *   - /identidad/consultar y /identidad/consultar-empleado (registrarse / usuarios)
 *   - /citas/disponibilidad (citas/agendar.html)
 *   - /admin/kpis/datos (kpis.js)
 *   - /admin/registro/<tabla> y /supabase/status (admin/dashboard.html)
 *   - /worker/resumen/<clave> (worker/dashboard.html)
 */
(function () {
    'use strict';

    var csrfMeta = document.querySelector('meta[name="csrf-token"]');
    var csrfToken = csrfMeta ? csrfMeta.getAttribute('content') : '';

    function getJson(url) {
        var headers = {
            'Accept': 'application/json',
            'X-Requested-With': 'XMLHttpRequest'
        };
        if (csrfToken) {
            headers['X-CSRF-Token'] = csrfToken;
        }
        return fetch(url, {
            method: 'GET',
            headers: headers,
            credentials: 'same-origin'
        }).then(function (resp) {
            return resp.json().catch(function () { return null; });
        }).catch(function () { return null; });
    }

    function inicializarEndpointsVista() {
        var path = window.location.pathname;
        var qs = window.location.search || '';

        // 1. Estado de sesión actual en todas las pantallas
        getJson('/api/sesion');

        // 2. Portada principal (/)
        if (path === '/') {
            getJson('/api/inicio');
            getJson('/api/servicios');
        }
        // 3. Catálogo / Tienda (/tienda)
        else if (path === '/tienda') {
            getJson('/api/tienda/marcas');
            getJson('/api/tienda/categorias');
            getJson('/api/tienda/productos' + qs);
        }
        // 4. Ficha de producto (/producto/<id>)
        else if (path.indexOf('/producto/') === 0) {
            var prodId = path.split('/')[2];
            if (prodId && /^\d+$/.test(prodId)) {
                getJson('/api/producto/' + prodId);
            }
        }
        // 5. Carrito de compras (/carrito)
        else if (path === '/carrito') {
            getJson('/api/carrito');
        }
        // 6. Checkout (/checkout)
        // Nota: /api/clientes/buscar/<doc> ya está integrado en autocompletado-cliente.js
        else if (path === '/checkout') {
            getJson('/api/checkout/resumen');
        }
        // 7. Registro (/registrarse)
        // Nota: /identidad/consultar ya está integrado al escribir el DNI/RUC
        else if (path === '/registrarse') {
            var inputUser = document.querySelector('input[name="nombre_usuario"]');
            if (inputUser) {
                inputUser.addEventListener('blur', function () {
                    var val = (inputUser.value || '').trim();
                    if (val.length >= 3) {
                        getJson('/api/auth/disponibilidad?campo=nombre_usuario&valor=' + encodeURIComponent(val));
                    }
                });
            }
            var inputCorreo = document.querySelector('input[name="correo"]');
            if (inputCorreo) {
                inputCorreo.addEventListener('blur', function () {
                    var val = (inputCorreo.value || '').trim();
                    if (val.indexOf('@') > 0) {
                        getJson('/api/auth/disponibilidad?campo=correo&valor=' + encodeURIComponent(val));
                    }
                });
            }
        }
        // 8. Agendamiento y seguimiento de citas (/citas, /citas/mis-citas, /citas/confirmacion/<id>)
        // Nota: /citas/disponibilidad ya está integrado en citas/agendar.html
        else if (path === '/citas') {
            getJson('/api/citas/catalogos');
        }
        else if (path === '/citas/mis-citas') {
            getJson('/api/citas/mis-citas');
        }
        else if (path.indexOf('/citas/confirmacion/') === 0) {
            var citaId = path.split('/')[3];
            if (citaId && /^\d+$/.test(citaId)) {
                getJson('/api/citas/' + citaId);
            }
        }
        // 9. Perfil y Pedidos del cliente (/perfil, /pedidos/mis-pedidos, /pedidos/<id>)
        else if (path === '/perfil') {
            getJson('/api/perfil');
            getJson('/api/pedidos/mis-pedidos');
        }
        else if (path === '/pedidos/mis-pedidos') {
            getJson('/api/pedidos/mis-pedidos');
        }
        else if (path.indexOf('/pedidos/') === 0) {
            var ventaId = path.split('/')[2];
            if (ventaId && /^\d+$/.test(ventaId)) {
                getJson('/api/pedidos/' + ventaId);
            }
        }
        // 10. Panel de Administración (/admin, /admin/citas, /admin/productos, /sistema/usuarios, etc.)
        // Nota: /admin/registro/<tabla>, /supabase/status y /admin/kpis/datos ya están integrados
        else if (path === '/admin') {
            getJson('/api/admin/dashboard');
        }
        else if (path === '/admin/citas') {
            getJson('/api/admin/citas' + qs);
        }
        else if (path === '/admin/productos') {
            getJson('/api/admin/productos' + qs);
        }
        else if (path === '/sistema/usuarios') {
            getJson('/api/admin/usuarios' + qs);
        }
        else if (path === '/admin/seguridad') {
            getJson('/api/admin/seguridad');
        }
        else if (path === '/admin/configuracion') {
            getJson('/api/admin/configuracion');
        }
        else if (path === '/admin/reportes') {
            getJson('/api/admin/reportes/ventas' + qs);
        }
        else if (path.indexOf('/admin/reportes/') === 0) {
            var tipoRep = path.split('/')[3];
            if (tipoRep && ['ventas', 'productos', 'inventario', 'citas'].indexOf(tipoRep) !== -1) {
                getJson('/api/admin/reportes/' + tipoRep + qs);
            }
        }
        else if (path.indexOf('/admin/libro-de-reclamaciones') === 0) {
            getJson('/api/admin/reclamos' + qs);
        }
        // 11. Módulo de Ventas (/ventas, /ventas/reservas, /ventas/<id>)
        else if (path === '/ventas') {
            getJson('/api/ventas' + qs);
        }
        else if (path === '/ventas/reservas') {
            getJson('/api/ventas/reservas' + qs);
        }
        else if (path.indexOf('/ventas/') === 0) {
            var vId = path.split('/')[2];
            if (vId && /^\d+$/.test(vId)) {
                getJson('/api/pedidos/' + vId);
            }
        }
        // 12. Panel del Técnico / Personal (/worker, /worker/citas, /worker/reparaciones, /worker/disponibilidad-modulo)
        // Nota: /worker/resumen/<clave> ya está integrado en worker/dashboard.html
        else if (path === '/worker') {
            getJson('/api/worker/dashboard');
        }
        else if (path === '/worker/citas') {
            getJson('/api/worker/citas' + qs);
        }
        else if (path === '/worker/reparaciones') {
            getJson('/api/worker/reparaciones' + qs);
        }
        else if (path.indexOf('/worker/reparaciones/') === 0) {
            var repId = path.split('/')[3];
            if (repId && /^\d+$/.test(repId)) {
                getJson('/api/worker/reparaciones/' + repId);
            }
        }
        else if (path === '/worker/disponibilidad-modulo') {
            getJson('/api/worker/disponibilidad' + qs);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', inicializarEndpointsVista);
    } else {
        inicializarEndpointsVista();
    }
})();
