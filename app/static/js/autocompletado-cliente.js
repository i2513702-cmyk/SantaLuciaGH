/* Autocompletado de clientes por número de documento.
 * Dispara el endpoint /api/clientes/buscar/<documento> y rellena los campos
 * del formulario. Ver app/routes/api.py para el contrato de la respuesta. */
(function () {
    'use strict';

    var doc = document.getElementById('cliNumeroDocumento');
    if (!doc) return;

    var apiUrl = (doc.closest('[data-cliente-api]') || {}).dataset;
    apiUrl = apiUrl ? apiUrl.clienteApi : '';
    if (!apiUrl) return;

    var tipo = document.getElementById('cliTipoDocumento');
    var spinner = document.getElementById('cliSpinner');
    var feedback = document.getElementById('cliFeedback');
    var campos = {
        nombres: document.getElementById('cliNombres'),
        apellidos: document.getElementById('cliApellidos'),
        telefono: document.getElementById('cliTelefono'),
        correo: document.getElementById('cliCorreo')
    };

    var MIN_DIGITOS = 6;
    var ESPERA = 300;
    var ultimo = '';
    var timer = null;
    var controlador = null;

    function soloDigitos(valor) {
        return (valor || '').replace(/\D+/g, '');
    }

    function cargando(activo) {
        if (spinner) spinner.hidden = !activo;
        doc.classList.toggle('is-loading', activo);
    }

    function avisar(texto, tipoMensaje) {
        if (!feedback) return;
        feedback.textContent = texto || '';
        feedback.className = 'form-text ac-feedback' + (tipoMensaje ? ' ac-' + tipoMensaje : '');
    }

    function limpiarCampos() {
        Object.keys(campos).forEach(function (clave) {
            if (campos[clave]) campos[clave].value = '';
        });
    }

    function rellenar(cliente) {
        Object.keys(campos).forEach(function (clave) {
            if (campos[clave] && cliente[clave]) campos[clave].value = cliente[clave];
        });
    }

    function buscar() {
        var numero = soloDigitos(doc.value);

        // Normaliza el input: solo dígitos.
        if (numero !== doc.value.trim()) doc.value = numero;
        if (numero === ultimo) return;

        if (numero.length < MIN_DIGITOS) {
            ultimo = numero;
            avisar(numero.length ? 'Ingresa al menos ' + MIN_DIGITOS + ' dígitos.' : '', '');
            return;
        }

        ultimo = numero;
        avisar('Buscando cliente...', '');
        cargando(true);

        var url = apiUrl.replace('__documento__', encodeURIComponent(numero));
        if (tipo) url += '?tipo_documento_id=' + encodeURIComponent(tipo.value);

        // Cancela la petición anterior si el usuario sigue escribiendo.
        if (controlador) controlador.abort();
        controlador = new AbortController();

        fetch(url, {
            headers: { 'Accept': 'application/json' },
            signal: controlador.signal
        })
            .then(function (r) {
                return r.json().then(function (datos) { return { status: r.status, datos: datos }; });
            })
            .then(function (respuesta) {
                // Ignora respuestas de una petición ya superada.
                // Descarta la respuesta si el campo ya cambió (petición obsoleta).
                var cliente = respuesta.datos.cliente;
                if (cliente && cliente.numero_documento !== numero) return;

                var datos = respuesta.datos;
                if (respuesta.status === 200 && datos.encontrado && cliente) {
                    rellenar(cliente);
                    avisar('Cliente encontrado: datos completados automáticamente.', 'ok');
                } else if (respuesta.status === 404) {
                    limpiarCampos();
                    avisar('Cliente no registrado. Completa los datos manualmente.', 'nuevo');
                } else if (respuesta.status === 400) {
                    avisar(datos.detalle || 'Revisa el número de documento.', 'error');
                } else {
                    avisar(datos.detalle || 'No se pudo consultar la base de datos.', 'error');
                }
                cargando(false);
            })
            .catch(function (err) {
                if (err.name === 'AbortError') return;
                cargando(false);
                avisar('No se pudo consultar el servidor. Inténtalo de nuevo.', 'error');
            });
    }

    doc.addEventListener('input', function () {
        clearTimeout(timer);
        timer = setTimeout(buscar, ESPERA);
    });

    doc.addEventListener('blur', function () {
        clearTimeout(timer);
        buscar();
    });

    // Al cambiar el tipo de documento se vuelve a validar el mismo número.
    if (tipo) {
        tipo.addEventListener('change', function () {
            ultimo = '';
            buscar();
        });
    }

    avisar('');
})();