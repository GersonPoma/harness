from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Annotated

import typer
from rich import print

from harness import consola
from harness.bucle import MAX_PASOS_POR_DEFECTO, Paso
from harness.config import ConfigModelo, ErrorConfiguracion, cargar_config
from harness.config_proyecto import ConfigProyecto, ErrorConfigProyecto, cargar_config_proyecto
from harness.ejecucion import ResultadoEjecucion, Validacion, ejecutar_ticket
from harness.entorno_docker import EntornoDocker, ErrorDocker
from harness.herramientas import crear_herramientas
from harness.herramientas.comandos import EjecutorComandos, validar_proyecto
from harness.herramientas.protegidos import GuardiaProtegidos
from harness.prompts import crear_mensajes_iniciales
from harness.proveedores.base import ErrorProveedor, ProveedorLLM
from harness.proveedores.fabrica import crear_proveedor
from harness.registro import RegistroPasos
from harness.ticket import ErrorTicket, leer_ticket_local

CODIGO_CANCELADO = 130

app = typer.Typer(help="Harness de IA para generación de código")


@dataclass
class _Preparacion:
    """Todo lo que se valida antes de gastar pedidos al modelo o crear contenedores."""

    repo: Path
    ticket: str
    texto_ticket: str
    config: ConfigModelo
    config_proyecto: ConfigProyecto | None


class _Reporte:
    """Muestra en consola y registra en el log lo que pasa durante la ejecución."""

    def __init__(self, registro: RegistroPasos) -> None:
        self.registro = registro

    def paso(self, paso: Paso) -> None:
        consola.mostrar_paso(paso)
        self.registro.registrar_paso(paso)

    def reintento(self, mensaje: str) -> None:
        consola.mostrar_aviso(mensaje)
        self.registro.registrar_reintento(mensaje)

    def validacion(self, validacion: Validacion) -> None:
        consola.mostrar_validacion(validacion)
        self.registro.registrar_validacion(validacion)


@app.command()
def run(
    ticket: Annotated[Path, typer.Argument(help="Ruta a un ticket local (.md).")],
    repo: Annotated[
        Path | None, typer.Option(help="Repo objetivo; por defecto, la carpeta actual.")
    ] = None,
    max_pasos: Annotated[
        int | None,
        typer.Option(min=1, help="Máximo de pedidos al modelo (por defecto, el de harness.yaml)."),
    ] = None,
) -> None:
    """Procesa un ticket y genera el código en el repo objetivo."""
    prep = _preparar_ejecucion(ticket, (repo or Path.cwd()).resolve())
    limites = prep.config_proyecto.limites if prep.config_proyecto else None
    max_pasos = max_pasos or (limites.max_pasos if limites else MAX_PASOS_POR_DEFECTO)
    registro = RegistroPasos(prep.repo, prep.ticket, secretos=[prep.config.api_key])
    registro.registrar_inicio(prep.config.proveedor, prep.config.modelo)
    consola.mostrar_inicio(prep.ticket, str(prep.repo), prep.config.proveedor, prep.config.modelo)

    reporte = _Reporte(registro)
    try:
        resultado = _ejecutar(prep, max_pasos, reporte)
    except (ErrorProveedor, ErrorDocker) as error:
        registro.registrar_error(str(error))
        consola.mostrar_error(str(error))
        raise typer.Exit(1) from error
    except KeyboardInterrupt as error:
        registro.registrar_error("Ejecución cancelada por el usuario.")
        consola.mostrar_error("Ejecución cancelada (los contenedores se borraron).")
        raise typer.Exit(CODIGO_CANCELADO) from error

    registro.registrar_fin(resultado)
    consola.mostrar_fin(resultado, str(registro.ruta))
    if resultado.es_limite_alcanzado or resultado.es_validado is False:
        raise typer.Exit(1)


def _preparar_ejecucion(ticket: Path, repo: Path) -> _Preparacion:
    """Valida repo, ticket, configuración y harness.yaml antes de gastar pedidos al modelo."""
    if not repo.is_dir():
        consola.mostrar_error(f"No existe la carpeta del repo: {repo}")
        raise typer.Exit(1)
    try:
        texto_ticket = leer_ticket_local(ticket)
        config = cargar_config()
        config_proyecto = cargar_config_proyecto(repo)
    except (ErrorTicket, ErrorConfiguracion, ErrorConfigProyecto) as error:
        consola.mostrar_error(str(error))
        raise typer.Exit(1) from error
    if config_proyecto is None:
        consola.mostrar_aviso(
            "El repo no tiene harness.yaml: se trabaja sin Docker, sin tests y sin validación. "
            "Créalo con 'harness init' (próximamente) o a mano (docs/harness-yaml.md)."
        )
    return _Preparacion(repo, ticket.stem, texto_ticket, config, config_proyecto)


def _ejecutar(prep: _Preparacion, max_pasos: int, reporte: _Reporte) -> ResultadoEjecucion:
    proveedor = crear_proveedor(prep.config, reporte.reintento)
    if prep.config_proyecto is None:
        return ejecutar_ticket(
            proveedor,
            crear_mensajes_iniciales(prep.texto_ticket),
            crear_herramientas(prep.repo),
            max_pasos,
            al_paso=reporte.paso,
        )
    # El with garantiza que los contenedores se borran aunque algo falle o haya Ctrl+C.
    with EntornoDocker(prep.repo, prep.config_proyecto, prep.ticket) as entorno:
        imagen = prep.config_proyecto.docker.imagen
        with consola.estado(f"Preparando contenedor ({imagen}) e instalando dependencias..."):
            entorno.iniciar()
        return _ejecutar_con_validacion(
            prep, prep.config_proyecto, entorno, proveedor, max_pasos, reporte
        )


def _ejecutar_con_validacion(
    prep: _Preparacion,
    config_proyecto: ConfigProyecto,
    entorno: EntornoDocker,
    proveedor: ProveedorLLM,
    max_pasos: int,
    reporte: _Reporte,
) -> ResultadoEjecucion:
    limites = config_proyecto.limites
    # La copia se toma después de instalar: lo que cambie desde aquí lo cambió el modelo.
    guardia = GuardiaProtegidos(prep.repo, config_proyecto.archivos_protegidos)
    ejecutor = EjecutorComandos(
        entorno, guardia, limites.max_caracteres_salida, limites.timeout_comando_segundos
    )
    comandos = config_proyecto.comandos
    return ejecutar_ticket(
        proveedor,
        crear_mensajes_iniciales(prep.texto_ticket, hay_validacion=True),
        crear_herramientas(prep.repo, config_proyecto, ejecutor, entorno.marcar_cambio),
        max_pasos,
        validar=partial(validar_proyecto, ejecutor, comandos.tests, comandos.lint),
        max_intentos_validacion=limites.max_intentos_validacion,
        al_paso=reporte.paso,
        al_validar=reporte.validacion,
    )


@app.command()
def version() -> None:
    """Muestra la versión del harness."""
    print("harness 0.1.0")


def main() -> None:
    app()
