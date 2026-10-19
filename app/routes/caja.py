"""Cierre de caja: arqueo diario y registro de cierres (solo ADMINISTRADOR).

El cálculo del día (ventas, efectivo y otros medios a partir de los pagos
confirmados) vive en `app.services.caja_service`. Aquí solo se pide la fecha,
se devuelve HTML o JSON y se guarda el cierre en la tabla `cierres_caja`.
"""

from __future__ import annotations

from datetime import date, timedelta

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.decorators import roles_required
from app.exceptions import AppError, NotFoundError, ValidationError
from app.services import caja_service

bp = Blueprint("caja", __name__)


def _fecha_arg(nombre: str) -> date | None:
    texto = (request.args.get(nombre) or "").strip()[:10]
    try:
        return date.fromisoformat(texto)
    except ValueError:
        return None


@bp.get("/admin/caja")
@roles_required("admin")
def listado():
    """Registro de cierres de caja con filtro por fecha y paginación."""
    desde = _fecha_arg("desde")
    hasta = _fecha_arg("hasta")
    pagina = max(request.args.get("p", 1, type=int) or 1, 1)

    error = None
    resultado = {"cierres": [], "total": 0, "paginas": 1, "pagina": 1}
    resumen = {"n_cierres": 0, "total_ventas": 0.0, "total_efectivo": 0.0,
               "total_contado": 0.0, "diferencia": 0.0}
    hay_tabla = True
    try:
        resultado = caja_service.listar_cierres(desde=desde, hasta=hasta, pagina=pagina)
        resumen = caja_service.resumen_cierres(desde=desde, hasta=hasta)
    except ValidationError as exc:
        error = exc.message
        hay_tabla = False

    hoy = date.today()
    atajos = {
        "Hoy": (hoy, hoy),
        "7 días": (hoy - timedelta(days=6), hoy),
        "Este mes": (hoy.replace(day=1), hoy),
    }
    args = {k: v for k, v in {
        "desde": desde.isoformat() if desde else "",
        "hasta": hasta.isoformat() if hasta else "",
    }.items() if v}
    return render_template(
        "caja/listado.html",
        cierres=resultado["cierres"],
        total=resultado["total"],
        paginas=resultado["paginas"],
        pagina=resultado["pagina"],
        resumen=resumen,
        error=error,
        hay_tabla=hay_tabla,
        atajos=atajos,
        f=args,
        hoy=hoy.isoformat(),
    )


@bp.route("/admin/caja/nuevo", methods=["GET", "POST"])
@roles_required("admin")
def nuevo():
    """Registra el cierre de un día: calcula lo esperado y guarda el arqueo."""
    error = None
    valores = {"fecha": date.today().isoformat(), "monto_apertura": "0",
               "monto_contado": "", "observaciones": ""}
    preview = None

    if request.method == "POST":
        valores = {
            "fecha": (request.form.get("fecha") or "").strip(),
            "monto_apertura": (request.form.get("monto_apertura") or "0").strip(),
            "monto_contado": (request.form.get("monto_contado") or "").strip(),
            "observaciones": (request.form.get("observaciones") or "").strip(),
        }
        try:
            caja_service.registrar_cierre(
                fecha=valores["fecha"],
                monto_apertura=valores["monto_apertura"] or 0,
                monto_contado=valores["monto_contado"] or 0,
                observaciones=valores["observaciones"],
                responsable=session.get("nombre") or session.get("username") or "",
                usuario_id=session.get("usuario_id"),
            )
            flash("Cierre de caja registrado correctamente.", "success")
            return redirect(url_for("caja.listado"))
        except (AppError, ValidationError) as exc:
            error = exc.message if isinstance(exc, AppError) else str(exc)
        except Exception:  # noqa: BLE001
            error = "No se pudo registrar el cierre. Revisa los datos e inténtalo de nuevo."
    # Vista previa del día seleccionado (para el resumen en pantalla).
    try:
        dia = valores["fecha"] or date.today().isoformat()
        preview = caja_service.calcular_dia(dia)
        preview["esperado"] = round(
            float(valores["monto_apertura"] or 0) + preview["total_efectivo"], 2
        )
        preview["ya_existe"] = caja_service.cierre_de_fecha(dia)
    except ValidationError as exc:
        if not error:
            error = exc.message

    return render_template(
        "caja/form.html",
        error=error,
        valores=valores,
        preview=preview,
        hoy=date.today().isoformat(),
    )


@bp.get("/admin/caja/preview")
@roles_required("admin")
def preview():
    """Vista previa en JSON del día (ventas, efectivo, otros) para el formulario."""
    fecha = (request.args.get("fecha") or "").strip()[:10]
    try:
        datos = caja_service.calcular_dia(fecha)
    except ValidationError as exc:
        return jsonify({"ok": False, "error": exc.message}), 503
    datos["ya_existe"] = bool(caja_service.cierre_de_fecha(fecha))
    return jsonify({"ok": True, **datos})


@bp.get("/admin/caja/<int:cierre_id>")
@roles_required("admin")
def detalle(cierre_id):
    """Detalle de un cierre registrado."""
    try:
        cierre = caja_service.obtener_cierre(cierre_id)
    except (NotFoundError, ValidationError) as exc:
        flash(exc.message, "error")
        return redirect(url_for("caja.listado"))
    cierre["esperado"] = round(
        float(cierre.get("monto_apertura") or 0) + float(cierre.get("total_efectivo") or 0), 2
    )
    return render_template("caja/detalle.html", cierre=cierre)
