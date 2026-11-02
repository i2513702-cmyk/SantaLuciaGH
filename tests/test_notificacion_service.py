import pytest

from app.services import notificacion_service as ns

RESUMEN = {
    "venta_id": 7,
    "codigo_pedido": "PED-0007",
    "tipo_comprobante": "BOLETA",
    "serie": "B001-7",
    "cliente_nombre": "Ana <Perez>",
    "total": 118.0,
}


@pytest.mark.parametrize(
    "entrada,esperado",
    [(" Ana@Mail.com ", "ana@mail.com"), ("sin-arroba", None), ("a@b", None), ("", None), (None, None)],
)
def test_normalizar_correo(entrada, esperado):
    assert ns.normalizar_correo(entrada) == esperado


def test_correo_registrado_prefiere_cliente():
    u = {"correo": "cuenta@x.com", "clientes": {"correo": "cli@x.com"}}
    assert ns.correo_registrado(u) == "cli@x.com"
    assert ns.correo_registrado({"correo": "cuenta@x.com"}) == "cuenta@x.com"
    assert ns.correo_registrado({"clientes": [{"correo": "l@x.com"}]}) == "l@x.com"
    assert ns.correo_registrado({}) == ""


def test_construir_correo_adjunta_pdf_y_escapa_html():
    msg = ns.construir_correo(RESUMEN, b"%PDF-1.4 x", "ana@x.com", "tienda@x.com")
    adjuntos = list(msg.iter_attachments())
    assert len(adjuntos) == 1
    assert adjuntos[0].get_filename() == "BOLETA_B001-7.pdf"
    assert adjuntos[0].get_content() == b"%PDF-1.4 x"
    html = msg.get_body(("html",)).get_content()
    assert "Ana &lt;Perez&gt;" in html and "S/ 118.00" in html
    assert "PED-0007" in msg["Subject"]


@pytest.fixture
def espias(monkeypatch):
    registros, enviados = [], []
    monkeypatch.setattr(ns, "_registrar", lambda *a, **k: registros.append(a))
    monkeypatch.setattr(ns, "_enviar_correo", lambda r, pdf, d: enviados.append(d))
    monkeypatch.setattr("app.services.pdf_service.generar_comprobante_pdf", lambda r: b"%PDF")
    return registros, enviados


def test_envia_y_registra(monkeypatch, espias):
    registros, enviados = espias
    monkeypatch.setattr(ns, "correo_configurado", lambda: True)
    r = ns.enviar_comprobante(RESUMEN, " Ana@X.com ", en_segundo_plano=False)
    assert enviados == ["ana@x.com"]
    assert registros == [(7, "ana@x.com", "enviado")]
    assert r == {"destino": "a***@x.com", "invalido": False, "sin_canal": False}


def test_correo_invalido_no_envia(monkeypatch, espias):
    registros, enviados = espias
    monkeypatch.setattr(ns, "correo_configurado", lambda: True)
    r = ns.enviar_comprobante(RESUMEN, "no-es-correo", en_segundo_plano=False)
    assert enviados == [] and registros == []
    assert r["invalido"] is True and r["destino"] is None


def test_sin_correo_no_hace_nada(monkeypatch, espias):
    registros, enviados = espias
    monkeypatch.setattr(ns, "correo_configurado", lambda: True)
    r = ns.enviar_comprobante(RESUMEN, "", en_segundo_plano=False)
    assert enviados == [] and registros == [] and r["invalido"] is False


def test_smtp_sin_configurar_no_registra_fallo(monkeypatch, espias):
    registros, enviados = espias
    monkeypatch.setattr(ns, "correo_configurado", lambda: False)
    r = ns.enviar_comprobante(RESUMEN, "ana@x.com", en_segundo_plano=False)
    assert enviados == [] and registros == []
    assert r["sin_canal"] is True and r["destino"] is None


def test_fallo_smtp_queda_registrado(monkeypatch, espias):
    registros, _ = espias
    monkeypatch.setattr(ns, "correo_configurado", lambda: True)

    def roto(*a):
        raise ns.EnvioError("SMTP_AUTH", "credenciales")

    monkeypatch.setattr(ns, "_enviar_correo", roto)
    ns.enviar_comprobante(RESUMEN, "ana@x.com", en_segundo_plano=False)
    assert registros == [(7, "ana@x.com", "fallido", "SMTP_AUTH")]
