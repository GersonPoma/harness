from pathlib import Path
from typing import Annotated

import typer
from rich import print

from harness import consola
from harness.bucle import MAX_PASOS_POR_DEFECTO, Paso, ejecutar_bucle
from harness.config import ConfigModelo, ErrorConfiguracion, cargar_config
from harness.herramientas import crear_herramientas
from harness.prompts import crear_mensajes_iniciales
from harness.proveedores.base import ErrorProveedor
from harness.proveedores.fabrica import crear_proveedor
from harness.registro import RegistroPasos
from harness.ticket import ErrorTicket, leer_ticket_local

app = typer.Typer(help="Harness de IA para generación de código")


@app.command()
def run(
    ticket: Annotated[Path, typer.Argument(help="Ruta a un ticket local (.md).")],
    repo: Annotated[
        Path | None, typer.Option(help="Repo objetivo; por defecto, la carpeta actual.")
    ] = None,
    max_pasos: Annotated[
        int, typer.Option(min=1, help="Máximo de pedidos al modelo.")
    ] = MAX_PASOS_POR_DEFECTO,
) -> None:
    """Procesa un ticket y genera el código en el repo objetivo."""
    repo = (repo or Path.cwd()).resolve()
    texto_ticket, config = _preparar_ejecucion(ticket, repo)
    registro = RegistroPasos(repo, ticket.stem, secretos=[config.api_key])
    registro.registrar_inicio(config.proveedor, config.modelo)
    consola.mostrar_inicio(ticket.stem, str(repo), config.proveedor, config.modelo)

    def al_paso(paso: Paso) -> None:
        consola.mostrar_paso(paso)
        registro.registrar_paso(paso)

    try:
        resultado = ejecutar_bucle(
            crear_proveedor(config),
            crear_mensajes_iniciales(texto_ticket),
            crear_herramientas(repo),
            max_pasos,
            al_paso,
        )
    except ErrorProveedor as error:
        registro.registrar_error(str(error))
        consola.mostrar_error(str(error))
        raise typer.Exit(1) from error

    registro.registrar_fin(resultado)
    consola.mostrar_fin(resultado, str(registro.ruta))
    if resultado.es_limite_alcanzado:
        raise typer.Exit(1)


def _preparar_ejecucion(ticket: Path, repo: Path) -> tuple[str, ConfigModelo]:
    """Valida repo, ticket y configuración antes de gastar pedidos al modelo."""
    if not repo.is_dir():
        consola.mostrar_error(f"No existe la carpeta del repo: {repo}")
        raise typer.Exit(1)
    try:
        return leer_ticket_local(ticket), cargar_config()
    except (ErrorTicket, ErrorConfiguracion) as error:
        consola.mostrar_error(str(error))
        raise typer.Exit(1) from error


@app.command()
def version() -> None:
    """Muestra la versión del harness."""
    print("harness 0.1.0")


def main() -> None:
    app()
