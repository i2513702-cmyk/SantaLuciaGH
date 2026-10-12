/* Panel de KPIs: pide los datos al servidor y dibuja tarjetas y graficos.
   Sin librerias externas: el panel ya usa CSS puro, aqui tambien.
   Los graficos de linea son SVG (con <title>, que da el tooltip nativo). */
(function () {
    var BOOT = window.KPI_BOOT || {};
    var grid = document.getElementById('kpi-grid');
    if (!grid) return;

    var estado = { cargando: false };

    /* ---------- utilidades ---------- */
    function q(sel, raiz) { return (raiz || document).querySelector(sel); }
    function qa(sel, raiz) { return Array.prototype.slice.call((raiz || document).querySelectorAll(sel)); }
    function esc(s) {
        return String(s === null || s === undefined ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function n(v, dec) {
        if (v === null || v === undefined || isNaN(v)) return '—';
        return Number(v).toFixed(dec === undefined ? 1 : dec);
    }
    function valor(kpi) {
        if (kpi.valor === null || kpi.valor === undefined) return '—';
        return kpi.unidad === 'horas' ? n(kpi.valor, 2) + ' h' : n(kpi.valor, 1) + ' %';
    }
    function vacio(msg) {
        return '<div class="chart-empty">' + esc(msg) + '</div>';
    }
    function conDatos(serie) {
        return (serie || []).some(function (p) { return p.valor !== null && p.valor !== undefined; });
    }

    /* ---------- grafico de linea (SVG) ---------- */
    function lineaSVG(serie, meta, unidad) {
        if (!conDatos(serie)) {
            return vacio('Sin datos en el periodo seleccionado.');
        }
        var W = 660, H = 190, PL = 46, PR = 14, PT = 16, PB = 28;
        var puntos = serie;
        var maximo = 0;
        puntos.forEach(function (p) {
            if (p.valor !== null && p.valor > maximo) maximo = p.valor;
        });
        if (meta !== null && meta !== undefined && meta > maximo) maximo = meta;
        maximo = maximo * 1.15 || 1;
        var ancho = W - PL - PR, alto = H - PT - PB;
        var paso = puntos.length > 1 ? ancho / (puntos.length - 1) : 0;
        var x = function (i) { return PL + (puntos.length > 1 ? i * paso : ancho / 2); };
        var y = function (v) { return PT + alto - (v / maximo) * alto; };

        var s = '<svg class="chart-svg" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" role="img">';
        [0, 0.5, 1].forEach(function (f) {
            var v = maximo * f, yy = y(v);
            s += '<line class="grid" x1="' + PL + '" y1="' + yy.toFixed(1) + '" x2="' + (W - PR) + '" y2="' + yy.toFixed(1) + '"/>';
            s += '<text class="axis" x="' + (PL - 8) + '" y="' + (yy + 4).toFixed(1) + '" text-anchor="end">' + n(v, unidad === 'horas' ? 0 : 0) + '</text>';
        });
        if (meta !== null && meta !== undefined) {
            s += '<line class="meta-line" x1="' + PL + '" y1="' + y(meta).toFixed(1) + '" x2="' + (W - PR) + '" y2="' + y(meta).toFixed(1) + '"/>';
            s += '<text class="axis meta-axis" x="' + (W - PR) + '" y="' + (y(meta) - 5).toFixed(1) + '" text-anchor="end">meta ' + n(meta, unidad === 'horas' ? 0 : 0) + '</text>';
        }
        var tramo = [];
        function cerrar() {
            if (tramo.length > 1) {
                s += '<polyline class="linea" points="' + tramo.join(' ') + '"/>';
            }
            tramo = [];
        }
        puntos.forEach(function (p, i) {
            if (p.valor === null || p.valor === undefined) { cerrar(); return; }
            tramo.push(x(i).toFixed(1) + ',' + y(p.valor).toFixed(1));
        });
        cerrar();
        puntos.forEach(function (p, i) {
            if (p.valor === null || p.valor === undefined) return;
            var clase = 'punto ' + (p.cumple ? 'ok' : 'critico');
            s += '<circle class="' + clase + '" cx="' + x(i).toFixed(1) + '" cy="' + y(p.valor).toFixed(1) + '" r="4">'
                + '<title>' + esc(p.etiqueta + ': ' + valor({ valor: p.valor, unidad: unidad })) + '</title>'
                + '</circle>';
        });
        puntos.forEach(function (p, i) {
            if (puntos.length > 7 && i % 2) return;
            s += '<text class="axis" x="' + x(i).toFixed(1) + '" y="' + (H - 8) + '" text-anchor="middle">' + esc(p.etiqueta) + '</text>';
        });
        s += '</svg>';
        return s;
    }

    /* ---------- sparkline de la tarjeta ---------- */
    function spark(serie) {
        if (!conDatos(serie)) return '';
        var pts = serie.slice(-8);
        var vals = pts.map(function (p) { return p.valor; }).filter(function (v) { return v !== null; });
        if (vals.length < 2) return '';
        var max = Math.max.apply(null, vals), min = Math.min.apply(null, vals);
        var rango = (max - min) || 1;
        var d = pts.map(function (p, i) {
            if (p.valor === null) return null;
            var x = (i / (pts.length - 1)) * 100;
            var y = 26 - ((p.valor - min) / rango) * 22 - 2;
            return (x).toFixed(1) + ',' + y.toFixed(1);
        }).filter(Boolean).join(' ');
        return '<svg class="spark" viewBox="0 0 100 26" preserveAspectRatio="none" aria-hidden="true">'
            + '<polyline points="' + d + '"/></svg>';
    }

    /* ---------- barra apilada ---------- */
    function apilada(items, total, vacioMsg, nota) {
        if (!total) return vacio(vacioMsg || 'Sin datos en el periodo.');
        var s = '<div class="stack">';
        items.forEach(function (it) {
            var pct = (it.valor / total) * 100;
            s += '<div class="stack-seg ' + esc(it.clase) + '" style="width:' + pct.toFixed(2) + '%" '
                + 'title="' + esc(it.etiqueta + ': ' + it.valor) + '"></div>';
        });
        s += '</div><div class="stack-leyenda">';
        items.forEach(function (it) {
            var pct = (it.valor / total) * 100;
            s += '<span class="stack-item"><i class="sw ' + esc(it.clase) + '"></i>'
                + esc(it.etiqueta) + ' <b>' + it.valor + '</b> (' + n(pct, 0) + ' %)</span>';
        });
        s += '</div>';
        if (nota) s += '<p class="chart-note">' + esc(nota) + '</p>';
        return s;
    }

    /* ---------- barras horizontales ---------- */
    function hbars(items) {
        if (!items || !items.length) return vacio('Sin datos.');
        var max = Math.max.apply(null, items.map(function (i) { return i.valor || 0; })) || 1;
        var s = '<div class="hbars">';
        items.forEach(function (it) {
            var w = ((it.valor || 0) / max) * 100;
            s += '<div class="hbar"><span class="hbar-label">' + esc(it.etiqueta) + '</span>'
                + '<span class="hbar-track"><span class="hbar-fill ' + esc(it.clase || 'accent') + '" style="width:' + w.toFixed(1) + '%"></span></span>'
                + '<span class="hbar-value">' + (it.texto || n(it.valor, 0)) + '</span></div>';
        });
        return s + '</div>';
    }

    /* ---------- embudo ---------- */
    function embudo(pasos) {
        if (!pasos.length || !pasos[0].valor) return vacio('Sin visitas registradas en el periodo.');
        var base = pasos[0].valor;
        var s = '';
        pasos.forEach(function (p) {
            var w = Math.max((p.valor / base) * 100, 1.5);
            s += '<div class="funnel-step"><span class="funnel-label">' + esc(p.etiqueta) + '</span>'
                + '<span class="funnel-track"><span class="funnel-fill ' + esc(p.clase) + '" style="width:' + w.toFixed(1) + '%">'
                + p.valor + '</span></span></div>';
        });
        return s;
    }

    /* ---------- tarjetas ---------- */
    var ETIQUETA_ESTADO = {
        ok: 'Cumple la meta',
        cerca: 'Cerca de la meta',
        critico: 'No cumple la meta',
        sin_datos: 'Sin datos'
    };
    var CLASE_BADGE = { ok: 'badge-ok', cerca: 'badge-warn', critico: 'badge-error', sin_datos: 'badge-muted' };

    function pintarTarjeta(k) {
        var card = grid.querySelector('[data-kpi="' + k.codigo + '"]');
        if (!card) return;
        var valorEl = q('[data-campo="valor"]', card);
        var subEl = q('[data-campo="sub"]', card);
        var estadoEl = q('[data-campo="estado"]', card);
        var variEl = q('[data-campo="variacion"]', card);
        var sparkEl = q('[data-campo="spark"]', card);

        if (k.error) {
            card.setAttribute('data-estado', 'error');
            valorEl.textContent = 'Error';
            subEl.textContent = k.error;
            estadoEl.className = 'badge ' + CLASE_BADGE.critico;
            estadoEl.textContent = 'No se pudo calcular';
            variEl.textContent = '';
            sparkEl.innerHTML = '';
            return;
        }
        var est = k.estado || 'sin_datos';
        card.setAttribute('data-estado', est);
        valorEl.textContent = valor(k);
        var fraccion = (k.numerador !== null && k.numerador !== undefined)
            ? n(k.numerador, 0) + ' / ' + n(k.denominador, 0)
            : (k.denominador !== null && k.denominador !== undefined ? n(k.denominador, 0) + ' citas' : '-');
        subEl.textContent = est === 'sin_datos'
            ? 'Sin datos en el periodo'
            : fraccion + (k.subtitulo ? '· ' + k.subtitulo : '');
        estadoEl.className = 'badge ' + (CLASE_BADGE[est] || 'badge-muted');
        estadoEl.textContent = ETIQUETA_ESTADO[est] || est;
        variEl.className = 'kpi-trend ' + ((k.variacion && k.variacion.clase) || 'trend-flat');
        variEl.textContent = (k.variacion && k.variacion.texto) || '—';
        sparkEl.innerHTML = spark(k.serie || []);
    }

    /* ---------- graficos ---------- */
    function pintarGraficos(k) {
        var caja = document.querySelector('[data-grafico="' + k.codigo + '"]');
        if (!caja) return;
        if (k.error) {
            qa('[data-rol]', caja).forEach(function (z) { z.innerHTML = vacio(k.error); });
            return;
        }
        var serie = (k.serie || []).map(function (p) {
            return {
                etiqueta: p.etiqueta,
                valor: p.valor,
                cumple: p.valor !== null && k.cumple
            };
        });
        var detalle = k.detalle || {};
        if (q('[data-rol="serie"]', caja)) {
            q('[data-rol="serie"]', caja).innerHTML = lineaSVG(serie, k.meta, k.unidad);
        }
        if (q('[data-rol="distribucion"]', caja)) {
            q('[data-rol="distribucion"]', caja).innerHTML = hbars((detalle.distribucion || []).map(function (t) {
                return { etiqueta: t.etiqueta, valor: t.total, clase: t.estado };
            }));
        }
        if (q('[data-rol="barra"]', caja)) {
            var total = 0;
            var items = [];
            var nota = '';
            var sinBarra = 'Sin datos en el periodo.';
            if (k.codigo === 'KPI-02') {
                total = detalle.dentro_24h + detalle.fuera_24h;
                sinBarra = 'Ninguna cita atendida en el periodo.';
                items = [
                    { etiqueta: 'Dentro de 24 h', valor: detalle.dentro_24h || 0, clase: 'ok' },
                    { etiqueta: 'Fuera de 24 h', valor: detalle.fuera_24h || 0, clase: 'critico' }
                ];
            } else if (k.codigo === 'KPI-05') {
                // El denominador son los carritos ya resueltos, asi que la barra
                // se compone solo de esos:Convertidos + Abandonados.
                total = (detalle.convertidos || 0) + (detalle.abandonados || 0);
                sinBarra = 'Sin carritos resueltos en el periodo.';
                items = [
                    { etiqueta: 'Convertidos', valor: detalle.convertidos || 0, clase: 'ok' },
                    { etiqueta: 'Abandonados', valor: detalle.abandonados || 0, clase: 'critico' }
                ];
                if (detalle.en_curso) {
                    nota = detalle.en_curso + ' carrito(s) en curso todavia no cuentan: no han tenido tiempo de abandonarse.';
                }
            }
            q('[data-rol="barra"]', caja).innerHTML = apilada(items, total, sinBarra, nota);
        }
        if (q('[data-rol="embudo"]', caja)) {
            q('[data-rol="embudo"]', caja).innerHTML = embudo([
                { etiqueta: 'Visitas', valor: detalle.visitas || 0, clase: 'ok' },
                { etiqueta: 'Con carrito', valor: detalle.carritos || 0, clase: 'cerca' },
                { etiqueta: 'Compraron', valor: detalle.compras || 0, clase: 'critico' }
            ]);
        }
        if (q('[data-rol="lista"]', caja)) {
            var sin = detalle.sin_stock || [];
            q('[data-rol="lista"]', caja).innerHTML = sin.length
                ? '<div class="chart-sub">Sin stock (' + sin.length + ')</div>' + hbars(sin.map(function (p) {
                    return { etiqueta: p.nombre, valor: 1, texto: '0 u.', clase: 'critico' };
                }))
                : vacio('Todos los productos activos tienen stock.');
        }
        if (q('[data-rol="errores"]', caja)) {
            var errs = detalle.errores || [];
            q('[data-rol="errores"]', caja).innerHTML = errs.length
                ? '<div class="chart-sub">Codigos de error mas frecuentes</div>' + hbars(errs.map(function (e) {
                    return { etiqueta: e.codigo, valor: e.total, texto: e.total + ' fallos', clase: 'critico' };
                }))
                : vacio('Sin fallos de entrega en el periodo.');
        }
    }

    /* ---------- franja de cumplimiento ---------- */
    function pintarResumen(r) {
        var strip = document.getElementById('kpi-strip');
        if (!strip || !r) { if (strip) strip.hidden = true; return; }
        strip.hidden = false;
        q('#kpi-strip-valor').textContent = r.en_meta + ' de ' + r.total + ' KPIs en meta';
        q('#kpi-strip-fill').style.width = (r.pct || 0) + '%';
        var pie = q('#kpi-strip-pie');
        pie.innerHTML = r.criticos && r.criticos.length
            ? '<span class="badge badge-error">Fuera de meta: ' + r.criticos.join(', ') + '</span>'
            : '<span class="badge badge-ok">Todos los KPIs cumplen la meta</span>';
    }

    /* ---------- carga de datos ---------- */
    function mostrarError(msg) {
        var box = document.getElementById('kpi-error');
        if (!box) return;
        box.textContent = msg;
        box.hidden = false;
        qa('[data-kpi]', grid).forEach(function (card) {
            if (card.getAttribute('data-estado') === 'cargando') {
                card.setAttribute('data-estado', 'error');
                q('[data-campo="valor"]', card).textContent = 'Error';
                q('[data-campo="estado"]', card).textContent = 'No se pudo calcular';
            }
        });
    }

    function pintar(datos) {
        var k = datos.kpi;
        (k.kpis || []).forEach(function (x) { pintarTarjeta(x); pintarGraficos(x); });
        pintarResumen(k.resumen);
        var act = document.getElementById('kpi-actualizado');
        if (act) act.textContent = 'Actualizado ' + (k.generado_en || '').replace('T', ' ').replace('+00:00', ' UTC');
    }

    function cargar(desde, hasta) {
        if (estado.cargando) return;
        estado.cargando = true;
        var carga = document.getElementById('kpi-carga');
        var box = document.getElementById('kpi-error');
        if (carga) carga.hidden = false;
        if (box) box.hidden = true;
        grid.setAttribute('aria-busy', 'true');
        qa('[data-kpi]', grid).forEach(function (card) {
            if (card.getAttribute('data-estado') === 'sin_datos' || card.getAttribute('data-estado') === 'error') {
                card.setAttribute('data-estado', 'cargando');
                q('[data-campo="valor"]', card).textContent = '—';
                q('[data-campo="estado"]', card).textContent = 'Calculando…';
            }
        });
        var url = BOOT.jsonUrl + '?desde=' + encodeURIComponent(desde) + '&hasta=' + encodeURIComponent(hasta);
        fetch(url, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, datos: d }; }); })
            .then(function (res) {
                if (!res.ok || !res.datos.ok) {
                    mostrarError(res.datos.error || 'No se pudieron calcular los KPIs.');
                    return;
                }
                pintar(res.datos);
                var csv = document.getElementById('kpi-csv');
                if (csv) csv.href = BOOT.csvUrl + '?desde=' + encodeURIComponent(desde) + '&hasta=' + encodeURIComponent(hasta);
                try {
                    window.history.replaceState({}, '', '?desde=' + desde + '&hasta=' + hasta);
                } catch (e) { /* sin historial: no es grave */ }
            })
            .catch(function () { mostrarError('No se pudo contactar al servidor. Revisa tu conexion.'); })
            .then(function () {
                estado.cargando = false;
                grid.removeAttribute('aria-busy');
                if (carga) carga.hidden = true;
            });
    }

    /* ---------- eventos ---------- */
    var desdeEl = document.getElementById('kpi-desde');
    var hastaEl = document.getElementById('kpi-hasta');

    document.getElementById('kpi-filtros').addEventListener('submit', function (ev) {
        ev.preventDefault();
        if (!desdeEl.value || !hastaEl.value) return;
        if (desdeEl.value > hastaEl.value) {
            mostrarError('La fecha inicial no puede ser posterior a la final.');
            return;
        }
        cargar(desdeEl.value, hastaEl.value);
    });

    qa('[data-kpi-rango]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            desdeEl.value = btn.getAttribute('data-desde');
            hastaEl.value = btn.getAttribute('data-hasta');
            cargar(desdeEl.value, hastaEl.value);
        });
    });

    /* ---------- arranque ---------- */
    qa('[data-kpi]').forEach(function (card) {
        var help = q('.kpi-help', card);
        if (help) help.setAttribute('title', help.getAttribute('data-help') || '');
    });
    if (desdeEl.value && hastaEl.value) cargar(desdeEl.value, hastaEl.value);
})();
