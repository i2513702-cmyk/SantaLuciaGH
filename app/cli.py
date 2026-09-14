"""Comandos CLI para Supabase.

Uso:
  flask --app app.py supabase:test        # Verificar conexión a Supabase
  flask --app app.py supabase:seed-demo   # Técnicos + agenda demo desde usuarios rol TECNICO
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