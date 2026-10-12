"""Comandos CLI para Supabase.

Uso:
  flask --app app.py supabase:test        # Verificar conexión a Supabase
  flask --app app.py supabase:seed-demo   # Técnicos + agenda demo desde usuarios rol TECNICO
  flask --app app.py kpi:snapshot         # Guardar la foto semanal de KPI-04
"""

import click
from flask.cli import with_appcontext


def register_cli(app):
    @app.cli.command("supabase:test")
    @with_appcontext
    def supabase_test():
        """Comprueba la conexión a Supabase usando SUPABASE_URL y SUPABASE_KEY."""
        from app.supabase_client import test_connection

        reporte = test_connection()
        click.echo("--- Reporte Supabase ---")
        for k, v in reporte.items():
            click.echo(f"  {k}: {v}")
        click.echo("------------------------")
        if reporte.get("ok"):
            click.echo("Conexión OK.")
        else:
            click.echo("Conexión con problemas.")

    @app.cli.command("supabase:seed-demo")
    @with_appcontext
    def supabase_seed_demo():
        """Crea técnicos (desde usuarios rol TECNICO) y su agenda demo."""
        from app.services import cita_service

        totales = cita_service.seed_demo()
        click.echo("--- Seed demo ---")
        for k, v in totales.items():
            click.echo(f"  {k}: {v}")
        click.echo("-----------------")

    @app.cli.command("kpi:snapshot")
    @with_appcontext
    def kpi_snapshot():
        """Guarda la foto semanal de disponibilidad del catálogo (KPI-04).

        El inventario no guarda histórico, así que la evolución semanal se
        acumula en data/kpi_snapshots.jsonl (un renglón por semana). Conviene
        correrlo una vez por semana (por ejemplo, con el Programador de tareas
        de Windows o un cron en Linux).
        """
        from app.services import kpi_service

        try:
            fila = kpi_service.guardar_snapshot(kpi_service._catalogo())
        except kpi_service.KpiError as exc:
            click.echo(f"No se pudo guardar el snapshot: {exc}")
            raise SystemExit(1) from exc
        click.echo("--- Snapshot KPI-04 ---")
        click.echo(f"  semana: {fila['periodo']}")
        click.echo(f"  valor:   {fila['valor']} %")
        click.echo(f"  detalle: {fila['numerador']}/{fila['denominador']} productos con stock")
        click.echo("-----------------------")
    @app.cli.command("ip:desbloquear")
    @click.argument("ip")
    def ip_desbloquear(ip):
        """Desbloquea una IP por consola (recuperacion si el admin quedo bloqueado)."""
        from app.services import login_guard

        login_guard.desbloquear(ip, "cli")
        click.echo(f"IP {ip} desbloqueada.")
