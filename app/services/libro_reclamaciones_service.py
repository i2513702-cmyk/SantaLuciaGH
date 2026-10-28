"""Servicio para Libro de Reclamaciones (Indecopi Perú)."""

import json
import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

from app.exceptions import AppError, ValidationError
from app.supabase_client import get_admin_client, get_reader

TABLA = "libro_reclamaciones"
BACKUP_DIR = "data/backups/libro_reclamaciones"


def _now():
    return datetime.now(timezone.utc)


def _generar_numero_registro() -> str:
    """Genera número correlativo automático."""
    try:
        resp = get_reader().table(TABLA).select("id").order("id", desc=True).limit(1).execute()
        ultimo = resp.data[0]["id"] if resp.data else 0
        return f"LR-{datetime.now().year}-{ultimo + 1:06d}"
    except Exception:
        return f"LR-{datetime.now().year}-{datetime.now().strftime('%H%M%S')}"


def crear_reclamo(datos: Dict[str, Any]) -> Dict[str, Any]:
    numero = _generar_numero_registro()
    fila = {
        "numero_registro": numero,
        "fecha_registro": datos.get("fecha_registro") or _now().isoformat(),
        "razon_social": datos.get("razon_social"),
        "ruc": datos.get("ruc"),
        "domicilio_proveedor": datos.get("domicilio_proveedor"),
        "consumidor_nombres": datos.get("consumidor_nombres"),
        "consumidor_apellidos": datos.get("consumidor_apellidos"),
        "tipo_documento": datos.get("tipo_documento"),
        "numero_documento": datos.get("numero_documento"),
        "domicilio_consumidor": datos.get("domicilio_consumidor"),
        "telefono": datos.get("telefono"),
        "email": datos.get("email"),
        "es_menor": bool(datos.get("es_menor")),
        "apoderado_nombre": datos.get("apoderado_nombre"),
        "apoderado_parentesco": datos.get("apoderado_parentesco"),
        "apoderado_documento": datos.get("apoderado_documento"),
        "tipo_bien": datos.get("tipo_bien"),
        "descripcion_bien": datos.get("descripcion_bien"),
        "monto_reclamado": datos.get("monto_reclamado"),
        "tipo_solicitud": datos.get("tipo_solicitud"),
        "detalle_hecho": datos.get("detalle_hecho"),
        "pedido_consumidor": datos.get("pedido_consumidor"),
        "estado": "pendiente",
        "respuesta": None,
        "fecha_respuesta": None,
    }
    try:
        resp = get_admin_client().table(TABLA).insert(fila).execute()
        reclamo = resp.data[0] if resp.data else fila
        _backup_reclamo(reclamo)
        return reclamo
    except Exception as exc:
        raise AppError(f"No se pudo registrar el reclamo: {exc}")


def listar_reclamos(
    estado: Optional[str] = None,
    tipo: Optional[str] = None,
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    q: Optional[str] = None,
) -> List[Dict[str, Any]]:
    try:
        query = get_reader().table(TABLA).select("*").order("fecha_registro", desc=True)
        if estado:
            query = query.eq("estado", estado)
        if tipo:
            query = query.eq("tipo_solicitud", tipo)
        if fecha_desde:
            query = query.gte("fecha_registro", fecha_desde)
        if fecha_hasta:
            query = query.lte("fecha_registro", fecha_hasta + "T23:59:59")
        resp = query.execute()
        reclamos = resp.data or []
        if q:
            ql = q.lower()
            reclamos = [
                r
                for r in reclamos
                if ql in str(r.get("numero_registro", "")).lower()
                or ql in str(r.get("consumidor_nombres", "")).lower()
                or ql in str(r.get("consumidor_apellidos", "")).lower()
                or ql in str(r.get("numero_documento", "")).lower()
                or ql in str(r.get("email", "")).lower()
            ]
        return reclamos
    except Exception:
        return []


def obtener_reclamo(reclamo_id: int) -> Optional[Dict[str, Any]]:
    try:
        resp = get_reader().table(TABLA).select("*").eq("id", reclamo_id).limit(1).execute()
        return resp.data[0] if resp.data else None
    except Exception:
        return None


def responder_reclamo(reclamo_id: int, estado: str, respuesta: str) -> Dict[str, Any]:
    datos = {"estado": estado}
    if respuesta:
        datos["respuesta"] = respuesta
        datos["fecha_respuesta"] = _now().isoformat()
    try:
        resp = get_admin_client().table(TABLA).update(datos).eq("id", reclamo_id).execute()
        reclamo = resp.data[0] if resp.data else {}
        _backup_reclamo(reclamo)
        return reclamo
    except Exception as exc:
        raise AppError(f"No se pudo actualizar el reclamo: {exc}")


def _backup_reclamo(reclamo: Dict[str, Any]):
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        numero = reclamo.get("numero_registro") or reclamo.get("id")
        filename = os.path.join(BACKUP_DIR, f"{numero}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(reclamo, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _enviar_email(destinatario: str, asunto: str, cuerpo_html: str, cuerpo_texto: str = ""):
    try:
        from app import create_app

        app = create_app()
        mail_host = app.config.get("MAIL_SERVER") or os.getenv("MAIL_SERVER")
        mail_port = int(app.config.get("MAIL_PORT") or os.getenv("MAIL_PORT") or 587)
        mail_user = app.config.get("MAIL_USERNAME") or os.getenv("MAIL_USERNAME")
        mail_pass = app.config.get("MAIL_PASSWORD") or os.getenv("MAIL_PASSWORD")
        mail_from = app.config.get("MAIL_DEFAULT_SENDER") or os.getenv("MAIL_DEFAULT_SENDER") or mail_user
        mail_use_tls = app.config.get("MAIL_USE_TLS", True)
    except Exception:
        mail_host = os.getenv("MAIL_SERVER")
        mail_port = int(os.getenv("MAIL_PORT") or 587)
        mail_user = os.getenv("MAIL_USERNAME")
        mail_pass = os.getenv("MAIL_PASSWORD")
        mail_from = os.getenv("MAIL_DEFAULT_SENDER") or mail_user
        mail_use_tls = True

    if not mail_host or not mail_user or not mail_pass:
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto
    msg["From"] = mail_from
    msg["To"] = destinatario
    if cuerpo_texto:
        msg.attach(MIMEText(cuerpo_texto, "plain", "utf-8"))
    msg.attach(MIMEText(cuerpo_html, "html", "utf-8"))

    try:
        server = smtplib.SMTP(mail_host, mail_port)
        if mail_use_tls:
            server.starttls()
        server.login(mail_user, mail_pass)
        server.sendmail(mail_from, destinatario, msg.as_string())
        server.quit()
    except Exception as exc:
        raise AppError(f"Error al enviar email: {exc}")


def enviar_emails(reclamo: Dict[str, Any]):
    numero = reclamo.get("numero_registro")
    fecha = reclamo.get("fecha_registro", "")
    proveedor = "Santa Lucia Servicio Técnico SAC (RUC: 20601234567)"
    texto_indecopi = "El proveedor deberá dar respuesta al reclamo en un plazo no mayor a treinta (30) días calendario, pudiendo ampliarse el plazo hasta por treinta (30) días más, previa comunicación al consumidor. La formulación del reclamo no impide acudir a otras vías de solución de controversias ni es requisito previo para interponer una denuncia ante el Indecopi."

    # Email al consumidor
    consumidor_email = reclamo.get("email")
    if consumidor_email:
        html_c = f"""
        <html><body style="font-family:Arial,sans-serif;">
        <h2>Confirmación de reclamo/queja - Libro de Reclamaciones</h2>
        <p>Estimado(a) {reclamo.get('consumidor_nombres')} {reclamo.get('consumidor_apellidos')},</p>
        <p>Hemos recibido tu {reclamo.get('tipo_solicitud').lower()} con los siguientes datos:</p>
        <ul>
            <li><strong>N° de Registro:</strong> {numero}</li>
            <li><strong>Fecha y hora:</strong> {fecha}</li>
            <li><strong>Proveedor:</strong> {proveedor}</li>
            <li><strong>Tipo de bien:</strong> {reclamo.get('tipo_bien')}</li>
            <li><strong>Detalle:</strong> {reclamo.get('detalle_hecho')}</li>
            <li><strong>Pedido:</strong> {reclamo.get('pedido_consumidor')}</li>
        </ul>
        <p><em>{texto_indecopi}</em></p>
        <p>Atentamente,<br>{proveedor}</p>
        </body></html>
        """
        try:
            _enviar_email(consumidor_email, f"Libro de Reclamaciones - N° {numero}", html_c)
        except Exception:
            pass

    # Email al proveedor
    try:
        html_p = f"""
        <html><body style="font-family:Arial,sans-serif;">
        <h2>Nuevo reclamo/queja registrado</h2>
        <p><strong>N° de Registro:</strong> {numero}</p>
        <p><strong>Fecha:</strong> {fecha}</p>
        <p><strong>Tipo:</strong> {reclamo.get('tipo_solicitud')}</p>
        <h3>Datos del consumidor</h3>
        <ul>
            <li><strong>Nombre:</strong> {reclamo.get('consumidor_nombres')} {reclamo.get('consumidor_apellidos')}</li>
            <li><strong>{reclamo.get('tipo_documento')}:</strong> {reclamo.get('numero_documento')}</li>
            <li><strong>Teléfono:</strong> {reclamo.get('telefono')}</li>
            <li><strong>Email:</strong> {reclamo.get('email')}</li>
            <li><strong>Domicilio:</strong> {reclamo.get('domicilio_consumidor')}</li>
        </ul>
        <h3>Bien contratado</h3>
        <ul>
            <li><strong>Tipo:</strong> {reclamo.get('tipo_bien')}</li>
            <li><strong>Descripción:</strong> {reclamo.get('descripcion_bien')}</li>
            <li><strong>Monto reclamado:</strong> {reclamo.get('monto_reclamado')}</li>
        </ul>
        <h3>Detalle</h3>
        <p><strong>Hecho:</strong><br>{reclamo.get('detalle_hecho')}</p>
        <p><strong>Pedido:</strong><br>{reclamo.get('pedido_consumidor')}</p>
        <p><em>{texto_indecopi}</em></p>
        </body></html>
        """
        _enviar_email("contacto@santalucia.pe", f"Nuevo reclamo - N° {numero}", html_p)
    except Exception:
        pass


def enviar_email_respuesta(reclamo: Dict[str, Any]):
    if not reclamo:
        return
    consumidor_email = reclamo.get("email")
    if not consumidor_email:
        return
    numero = reclamo.get("numero_registro")
    html = f"""
    <html><body style="font-family:Arial,sans-serif;">
    <h2>Respuesta a tu reclamo/queja - Libro de Reclamaciones</h2>
    <p>Estimado(a) {reclamo.get('consumidor_nombres')} {reclamo.get('consumidor_apellidos')},</p>
    <p>Hemos dado respuesta a tu {reclamo.get('tipo_solicitud').lower()} N° {numero}:</p>
    <div style="background:#f5f5f5;padding:15px;border-left:4px solid #0369a1;">
        {reclamo.get('respuesta','')}
    </div>
    <p><strong>Estado:</strong> {reclamo.get('estado')}</p>
    <p>Fecha de respuesta: {reclamo.get('fecha_respuesta')}</p>
    <p>Atentamente,<br>Santa Lucia Servicio Técnico SAC</p>
    </body></html>
    """
    try:
        _enviar_email(consumidor_email, f"Respuesta a reclamo N° {numero}", html)
    except Exception:
        pass
