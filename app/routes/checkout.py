"""Checkout simulado (el pago se registra en la base de datos como demo).

El usuario debe iniciar sesión para pagar: la venta, su detalle, el comprobante
y el pago se guardan en Supabase asociados al usuario (historial de compras), y
el carrito se vacía al confirmar el pago.

El personal interno no compra: estas rutas se cierran para su rol con un 403
(vía `app.decorators.bloquear_compras_sin_rol`, registrado en la app).
"""

import os
import uuid
from werkzeug.utils import secure_filename
from datetime import datetime, timezone

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.decorators import login_required
from app.exceptions import AppError, ValidationError
from app.services import carrito_service, rest_auth_service

bp = Blueprint("checkout", __name__)

IGV_TASA = 0.18


def _usuario_sesion() -> str:
    if session.get("usuario_id"):
        return f"user:{session['usuario_id']}"
    token = session.get("carrito_sesion")
    if not token:
        token = carrito_service.nueva_sesion()
        session["carrito_sesion"] = token
    return f"anon:{token}"


def _totales(items) -> tuple[float, float, float]:
    subtotal = sum(i["subtotal_num"] for i in items)
    igv = round(subtotal * IGV_TASA, 2)
    total = round(subtotal + igv, 2)
    return subtotal, igv, total


@bp.get("/checkout")
@login_required
def checkout():
    carrito_id = session.get("carrito_id")
    if not carrito_id:
        flash("Agrega productos al carrito para pagar.", "error")
        return redirect(url_for("carrito.ver"))

    items = carrito_service.listar(carrito_id)
    if not items:
        flash("Tu carrito está vacío. Agrega productos para continuar.", "error")
        return redirect(url_for("carrito.ver"))

    _subtotal, igv, total = _totales(items)

    return render_template(
        "checkout.html",
        items=items,
        subtotal=f"S/ {_subtotal:,.2f}",
        igv=f"S/ {igv:,.2f}",
        total=f"S/ {total:,.2f}",
        metodos_pago=carrito_service.listar_metodos_pago(),
        documentos=rest_auth_service.listar_tipos_documento(),
        vigencia_horas=carrito_service.HORAS_VIGENCIA,
    )


@bp.post("/checkout/procesar")
@login_required
def procesar():
    carrito_id = session.get("carrito_id")
    if not carrito_id:
        flash("Tu carrito está vacío.", "error")
        return redirect(url_for("carrito.ver"))

    items = carrito_service.listar(carrito_id)
    if not items:
        flash("Tu carrito está vacío. Agrega productos para continuar.", "error")
        return redirect(url_for("carrito.ver"))

    metodo_pago_id = request.form.get("metodo_pago_id", type=int)

    # Codigo del metodo seleccionado
    metodo = None
    try:
        from app.supabase_client import get_reader
        filas = get_reader().table("metodos_pago").select("codigo,nombre").eq("id", metodo_pago_id).limit(1).execute().data or []
        metodo = filas[0] if filas else None
    except Exception:  # noqa: BLE001
        metodo = None
    codigo_metodo = (metodo or {}).get("codigo", "")

    # Exigir telefono para pedidos en efectivo.
    if codigo_metodo == "EFECTIVO":
        telefono = (request.form.get("telefono") or "").strip()
        if not telefono:
            flash("El telefono es obligatorio para pedidos en efectivo.", "error")
            return redirect(url_for("checkout.checkout"))

    tipo = (request.form.get("tipo_comprobante", "BOLETA") or "BOLETA").upper()
    if tipo not in ("BOLETA", "FACTURA"):
        tipo = "BOLETA"

    # Validacion de comprobante: factura requiere RUC (11 digitos).
    if tipo == "FACTURA":
        tipo_doc_id = int(request.form.get("tipo_documento_id") or 0)
        numero_documento = (request.form.get("numero_documento") or "").strip()
        # Buscar codigo del tipo de documento
        codigo_doc = ""
        try:
            from app.supabase_client import get_reader
            filas = get_reader().table("tipos_documento").select("id,codigo").eq("id", tipo_doc_id).limit(1).execute().data or []
            if filas:
                codigo_doc = (filas[0].get("codigo") or "").upper()
        except Exception:  # noqa: BLE001
            pass
        if codigo_doc != "RUC" or len(numero_documento) != 11 or not numero_documento.isdigit():
            flash("Para factura necesitas un RUC de 11 digitos.", "error")
            return redirect(url_for("checkout.checkout"))

    usar_cliente = request.form.get("usar_cliente") == "1"
    cliente_nombre = "Cliente anónimo (mostrador)"
    cliente_documento = "—"
    tipo_doc_id = int(request.form.get("tipo_documento_id") or 1)
    if usar_cliente:
        nombres = request.form.get("nombres", "").strip()
        apellidos = request.form.get("apellidos", "").strip()
        if nombres or apellidos:
            cliente_nombre = " ".join(p for p in (nombres, apellidos) if p)
        cliente_documento = request.form.get("numero_documento", "").strip() or "—"

    try:
        resul = carrito_service.pagar(
            carrito_id=carrito_id,
            metodo_pago_id=metodo_pago_id,
            usuario_id=session.get("usuario_id"),
            tipo_comprobante=tipo,
        )
    except (AppError, ValidationError) as exc:
        flash(exc.message if isinstance(exc, AppError) else str(exc), "error")
        return redirect(url_for("checkout.checkout"))
    except Exception:  # noqa: BLE001
        flash("No se pudo registrar el pago. Intentalo de nuevo.", "error")
        return redirect(url_for("checkout.checkout"))

    venta_id = resul["venta_id"]
    numero_operacion = (request.form.get("numero_operacion") or "").strip()
    archivo = request.files.get("comprobante")
    ruta_comprobante = None
    if archivo and archivo.filename:
        ext = os.path.splitext(archivo.filename)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".pdf"):
            flash("Formato de comprobante no permitido.", "error")
            return redirect(url_for("checkout.checkout"))
        archivo.seek(0, os.SEEK_END)
        size = archivo.tell()
        archivo.seek(0)
        if size > 5 * 1024 * 1024:
            flash("El comprobante no puede superar 5 MB.", "error")
            return redirect(url_for("checkout.checkout"))
        carpeta = os.path.join("app", "static", "uploads", "comprobantes")
        os.makedirs(carpeta, exist_ok=True)
        nombre = f"{uuid.uuid4().hex}{ext}"
        archivo.save(os.path.join(carpeta, nombre))
        ruta_comprobante = f"uploads/comprobantes/{nombre}"

    # EFECTIVO: reservar stock (no descontar) y fecha limite de pago.
    if codigo_metodo == "EFECTIVO":
        try:
            from datetime import datetime, timedelta, timezone
            from app.supabase_client import get_admin_client, get_reader
            from app.config import Config
            horas = getattr(Config, "PAGO_EFECTIVO_VENCIMIENTO_HORAS", 48)
            limite = (datetime.now(timezone.utc) + timedelta(hours=horas)).isoformat()
            # Restaurar stock descontado por pagar() y reservarlo.
            for i in items:
                pid = i.get("producto_id")
                cantidad = float(i.get("cantidad") or 0)
                if not pid or cantidad <= 0:
                    continue
                filas = get_reader().table("inventario").select("id,stock_actual,stock_reservado").eq("producto_id", pid).execute().data or []
                if filas:
                    f = filas[0]
                    actual = float(f.get("stock_actual") or 0) + cantidad
                    reservado = float(f.get("stock_reservado") or 0) + cantidad
                    get_admin_client().table("inventario").update({
                        "stock_actual": actual,
                        "stock_reservado": reservado,
                    }).eq("id", f["id"]).execute()
            get_admin_client().table("ventas").update({
                "fecha_limite_pago": limite,
            }).eq("id", venta_id).execute()
        except Exception:  # noqa: BLE001
            pass

    # Actualizar estado del pago segun metodo
    try:
        from app.supabase_client import get_admin_client
        estado_pago = "PENDIENTE"
        if codigo_metodo in ("YAPE", "TRANSFERENCIA", "DEPOSITO"):
            estado_pago = "PENDIENTE_VERIFICACION"
        obs = []
        if ruta_comprobante:
            obs.append(f"comprobante={ruta_comprobante}")
        if numero_operacion:
            obs.append(f"operacion={numero_operacion}")
        get_admin_client().table("pagos").update({
            "estado": estado_pago,
            "observaciones": "; ".join(obs) if obs else None,
        }).eq("venta_id", venta_id).execute()
    except Exception:  # noqa: BLE001
        pass

    except (AppError, ValidationError) as exc:
        flash(exc.message if isinstance(exc, AppError) else str(exc), "error")
        return redirect(url_for("checkout.checkout"))
    except Exception:  # noqa: BLE001
        flash("No se pudo registrar el pago. Inténtalo de nuevo.", "error")
        return redirect(url_for("checkout.checkout"))

    subtotal = sum(i["subtotal_num"] for i in items)
    igv = round(subtotal * IGV_TASA, 2)
    total = round(subtotal + igv, 2)
    codigo_pedido = resul["codigo_pedido"]

    # Al pagar el carrito se vacía (detalle + carrito quedan fuera del carrito activo).
    try:
        carrito_service.vaciar(carrito_id)
    except Exception:  # noqa: BLE001
        pass
    session.pop("carrito_id", None)
    session.pop("carrito", None)
    session["carrito_n"] = 0

    # Comprobante guardado en la sesión para permitir la descarga del PDF.
    resumen = {
        "venta_id": resul["venta_id"],
        "codigo_pedido": codigo_pedido,
        "tipo_comprobante": resul["tipo_comprobante"],
        "serie": resul["serie"],
        "fecha_emision": datetime.now(timezone.utc).isoformat(),
        "cliente_nombre": cliente_nombre,
        "cliente_documento": cliente_documento,
        "cliente_tipo_documento_id": str(tipo_doc_id),
        "metodo_pago": resul["metodo_pago"],
        "transaction_id": f"SIM-{resul['venta_id']}",
        "estado_pago": "PAGO_CONFIRMADO",
        "subtotal": subtotal,
        "igv": igv,
        "total": total,
        "lineas": items,
        "es_efectivo": codigo_metodo == "EFECTIVO",
    }
    if codigo_metodo == "EFECTIVO":
        resumen["fecha_limite_pago"] = limite if "limite" in locals() else None
        resumen["direccion_tienda"] = current_app.config.get("CONTACTO_DIRECCION", "")
        resumen["horario_tienda"] = "Lun a Sab 9:00 - 19:00"
    session["pago_simulado"] = resumen

    flash(
        f"Pedido {codigo_pedido} registrado. Tu pago sera verificado por el equipo.",
        "success",
    )
    return render_template("confirmacion.html", resumen=resumen)


@bp.get("/checkout/pdf")
def pdf():
    resumen = session.get("pago_simulado")
    if not resumen:
        flash("No hay un comprobante reciente para descargar.", "error")
        return redirect(url_for("main.tienda"))

    from io import BytesIO

    from flask import Response

    from app.services.pdf_service import generar_comprobante_pdf

    datos = generar_comprobante_pdf(resumen)
    nombre = f"{resumen['tipo_comprobante']}_{resumen['serie']}.pdf"
    return Response(
        BytesIO(datos),
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{nombre}"',
        },
    )
