"""Envío del comprobante (PDF) al correo del cliente.

- SMTP estándar (`smtplib`), con el PDF adjunto.
- Cada intento queda en `notificaciones` (`estado_envio` = enviado / fallido),
  que es la tabla que mide el KPI-06.

El envío corre en un hilo para no demorar el checkout, y nunca lo rompe: si
falla, la compra sigue y el fallo queda registrado.

Variables de entorno (ver .env.example):
  MAIL_SERVER, MAIL_PORT, MAIL_USERNAME, MAIL_PASSWORD, MAIL_DEFAULT_SENDER,
  MAIL_USE_TLS
"""

from __future__ import annotations

import logging
import os
import re
import smtplib
import ssl
import threading
from datetime import datetime, timezone
from email.message import EmailMessage
from html import escape

log = logging.getLogger(__name__)

TIPO_NOTIFICACION = "COMPROBANTE"
CANAL_CORREO = "CORREO"
_RE_CORREO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EnvioError(Exception):
    """Fallo del envío. `codigo` queda en notificaciones.codigo_error."""

    def __init__(self, codigo: str, mensaje: str = ""):
        super().__init__(mensaje or codigo)
        self.codigo = codigo


def _cfg(nombre: str, defecto: str = "") -> str:
    return (os.getenv(nombre) or defecto).strip()


def normalizar_correo(valor) -> str | None:
    correo = (valor or "").strip().lower()
    return correo if _RE_CORREO.match(correo) else None


def enmascarar_correo(correo: str) -> str:
    usuario, _, dominio = correo.partition("@")
    return f"{usuario[:1]}***@{dominio}"


def correo_registrado(usuario: dict) -> str:
    """Correo guardado del cliente vinculado o, si no hay, de la cuenta."""
    cliente = (usuario or {}).get("clientes") or {}
    if isinstance(cliente, list):
        cliente = cliente[0] if cliente else {}
    return cliente.get("correo") or (usuario or {}).get("correo") or ""


def correo_configurado() -> bool:
    return bool(_cfg("MAIL_SERVER") and _cfg("MAIL_USERNAME") and _cfg("MAIL_PASSWORD"))


def _nombre_pdf(resumen: dict) -> str:
    return f"{resumen.get('tipo_comprobante', 'COMPROBANTE')}_{resumen.get('serie', '')}.pdf"


def _soles(valor) -> str:
    try:
        return f"S/ {float(valor):.2f}"
    except (TypeError, ValueError):
        return "S/ 0.00"


def construir_correo(resumen: dict, pdf: bytes, destinatario: str, remitente: str) -> EmailMessage:
    tipo = resumen.get("tipo_comprobante", "Comprobante")
    pedido = resumen.get("codigo_pedido", "")
    total = _soles(resumen.get("total"))
    cliente = resumen.get("cliente_nombre", "")

    msg = EmailMessage()
    msg["Subject"] = f"Tu {tipo.lower()} {resumen.get('serie', '')} - pedido {pedido}".strip()
    msg["From"] = remitente
    msg["To"] = destinatario
    msg.set_content(
        f"Hola {cliente},\n\n"
        f"Gracias por tu compra en Santa Lucia. Adjuntamos tu {tipo.lower()} del pedido {pedido} "
        f"por un total de {total}.\n\n"
        "Si tienes dudas, responde a este correo."
    )
    msg.add_alternative(
        "<p>Hola <strong>{c}</strong>,</p>"
        "<p>Gracias por tu compra en <strong>Santa Lucía</strong>. Adjuntamos tu {t} "
        "del pedido <strong>{p}</strong> por un total de <strong>{m}</strong>.</p>"
        "<p>Si tienes dudas, responde a este correo.</p>".format(
            c=escape(cliente), t=escape(tipo.lower()), p=escape(pedido), m=escape(total)
        ),
        subtype="html",
    )
    msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=_nombre_pdf(resumen))
    return msg


def _enviar_correo(resumen: dict, pdf: bytes, destinatario: str) -> None:
    host = _cfg("MAIL_SERVER")
    puerto = int(_cfg("MAIL_PORT", "587") or 587)
    usuario = _cfg("MAIL_USERNAME")
    clave = _cfg("MAIL_PASSWORD")
    remitente = _cfg("MAIL_DEFAULT_SENDER") or usuario
    usar_tls = _cfg("MAIL_USE_TLS", "1").lower() not in ("0", "false", "no")

    msg = construir_correo(resumen, pdf, destinatario, remitente)
    contexto = ssl.create_default_context()
    try:
        if puerto == 465:
            with smtplib.SMTP_SSL(host, puerto, timeout=20, context=contexto) as server:
                server.login(usuario, clave)
                server.send_message(msg)
        else:
            with smtplib.SMTP(host, puerto, timeout=20) as server:
                if usar_tls:
                    server.starttls(context=contexto)
                server.login(usuario, clave)
                server.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise EnvioError("SMTP_AUTH", str(exc)) from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise EnvioError("SMTP_DESTINATARIO", str(exc)) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise EnvioError("SMTP_ERROR", str(exc)) from exc


def _registrar(venta_id, destinatario: str, estado: str, codigo_error: str | None = None) -> None:
    try:
        from app.supabase_client import get_admin_client

        get_admin_client().table("notificaciones").insert(
            {
                "venta_id": venta_id,
                "canal": CANAL_CORREO,
                "destinatario": destinatario,
                "tipo": TIPO_NOTIFICACION,
                "estado_envio": estado,
                "codigo_error": codigo_error[:60] if codigo_error else None,
                "fecha_creacion": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        ).execute()
    except Exception:  # noqa: BLE001
        log.warning("No se pudo registrar la notificacion (venta %s)", venta_id, exc_info=True)


def _trabajo(resumen: dict, pdf: bytes, destinatario: str) -> None:
    venta_id = resumen.get("venta_id")
    try:
        _enviar_correo(resumen, pdf, destinatario)
    except EnvioError as exc:
        log.warning("Comprobante por correo fallo (%s): %s", exc.codigo, exc)
        _registrar(venta_id, destinatario, "fallido", exc.codigo)
    except Exception as exc:  # noqa: BLE001
        log.exception("Comprobante por correo fallo")
        _registrar(venta_id, destinatario, "fallido", type(exc).__name__.upper())
    else:
        _registrar(venta_id, destinatario, "enviado")


def enviar_comprobante(resumen: dict, correo=None, en_segundo_plano: bool = True) -> dict:
    """Valida el correo y manda el comprobante.

    Devuelve:
      destino   -> correo enmascarado (a***@x.com) si se va a intentar el envío, si no None
      invalido  -> True si se escribió un correo con formato inválido
      sin_canal -> True si el correo es válido pero el SMTP no está configurado
    """
    correo_ok = normalizar_correo(correo)
    invalido = bool((correo or "").strip()) and not correo_ok
    resultado = {"destino": None, "invalido": invalido, "sin_canal": False}
    if not correo_ok:
        return resultado
    if not correo_configurado():
        resultado["sin_canal"] = True
        return resultado

    from app.services.pdf_service import generar_comprobante_pdf

    pdf = generar_comprobante_pdf(resumen)
    resultado["destino"] = enmascarar_correo(correo_ok)
    if en_segundo_plano:
        threading.Thread(target=_trabajo, args=(resumen, pdf, correo_ok), daemon=True).start()
    else:
        _trabajo(resumen, pdf, correo_ok)
    return resultado
