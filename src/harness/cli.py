from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Annotated

import typer
from rich import print

from harness import consola
from harness.bucle import MAX_PASOS_POR_DEFECTO, ErrorRespuestaVacia, Paso
from harness.cambios import CambiosRepo, comparar, tomar_instantanea
from harness.config import ConfigModelo, ErrorConfiguracion, cargar_config, cargar_env
from harness.config_proyecto import (
    ConfigLimites,
    ConfigProyecto,
    ErrorConfigProyecto,
    cargar_config_proyecto,
)
from harness.contexto import ContextoProyecto, cargar_agents_md
from harness.ejecucion import Planificacion, ResultadoEjecucion, Validacion, ejecutar_ticket
from harness.entorno_docker import EntornoDocker, ErrorDocker
from harness.herramientas import crear_herramientas, crear_herramientas_lectura
from harness.herramientas.comandos import EjecutorComandos, validar_proyecto
from harness.herramientas.protegidos import GuardiaProtegidos
from harness.init import ErrorInit, archivos_faltantes, ejecutar_init, verificar_config
from harness.plan import Plan
from harness.planificacion import ErrorPlan, calcular_pasos_plan
from harness.prompts import crear_mensajes_iniciales
from harness.proveedores.base import ErrorProveedor, ProveedorLLM
from harness.proveedores.fabrica import crear_proveedor
from harness.registro import RegistroPasos
from harness.ticket import ErrorTicket, leer_ticket_local
from harness.variables import (
    ErrorVariables,
    VariablesResueltas,
    ocultar_secretos,
    resolver_variables,
)

CODIGO_CANCELADO = 130
MENSAJE_REQUIERE_AGENTS = (
    "Este proyecto requiere AGENTS.md (requiere_agents_md: true). Créalo con 'harness init'."
)
NOMBRE_LOG_INIT = "init"

app = typer.Typer(help="Harness de IA para generación de código")


@dataclass
class _Preparacion:
    """Todo lo que se valida antes de gastar pedidos al modelo o crear contenedores."""

    repo: Path
    ticket: str
    texto_ticket: str
    config: ConfigModelo
    config_proyecto: ConfigProyecto | None
    variables: VariablesResueltas
    contexto: ContextoProyecto
    es_con_plan: bool = True

    @property
    def secretos(self) -> list[str]:
        """Lo que nunca se escribe en el log ni se le muestra al modelo."""
        return [self.config.api_key, *self.variables.secretos]


class _Reporte:
    """Muestra en consola y registra en el log lo que pasa durante la ejecución."""

    def __init__(self, registro: RegistroPasos, es_aprobar_plan: bool = False) -> None:
        self.registro = registro
        self._es_aprobar_plan = es_aprobar_plan

    def paso(self, paso: Paso) -> None:
        consola.mostrar_paso(paso)
        self.registro.registrar_paso(paso)

    def reintento(self, mensaje: str) -> None:
        consola.mostrar_aviso(mensaje)
        self.registro.registrar_reintento(mensaje)

    def aviso(self, mensaje: str) -> None:
        consola.mostrar_aviso(mensaje)
        self.registro.registrar_aviso(mensaje)

    def plan(self, plan: Plan) -> bool:
        """Muestra el plan y, con --aprobar-plan, pregunta si se implementa."""
        consola.mostrar_plan(plan)
        es_aprobado = consola.preguntar_implementar() if self._es_aprobar_plan else True
        self.registro.registrar_plan(plan, es_aprobado)
        return es_aprobado

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
    sin_plan: Annotated[
        bool, typer.Option("--sin-plan", help="Implementar directamente, sin planificar.")
    ] = False,
    aprobar_plan: Annotated[
        bool,
        typer.Option("--aprobar-plan", help="Mostrar el plan y preguntar antes de implementar."),
    ] = False,
) -> None:
    """Procesa un ticket y genera el código en el repo objetivo."""
    if sin_plan and aprobar_plan:
        consola.mostrar_error("--sin-plan y --aprobar-plan no se pueden usar juntos.")
        raise typer.Exit(1)
    prep = _preparar_ejecucion(ticket, (repo or Path.cwd()).resolve())
    prep.es_con_plan = not sin_plan
    limites = prep.config_proyecto.limites if prep.config_proyecto else None
    max_pasos = max_pasos or (limites.max_pasos if limites else MAX_PASOS_POR_DEFECTO)
    registro = RegistroPasos(prep.repo, prep.ticket, secretos=prep.secretos)
    registro.registrar_inicio(prep.config.proveedor, prep.config.modelo, prep.contexto)
    consola.mostrar_inicio(prep.ticket, str(prep.repo), prep.config.proveedor, prep.config.modelo)
    consola.mostrar_contexto(prep.contexto)

    reporte = _Reporte(registro, aprobar_plan)
    _avisar_recorte_plan(prep, max_pasos, reporte)
    try:
        resultado, cambios = _ejecutar(prep, max_pasos, reporte)
    except (ErrorProveedor, ErrorDocker, ErrorRespuestaVacia, ErrorPlan) as error:
        registro.registrar_error(str(error))
        consola.mostrar_error(str(error))
        raise typer.Exit(1) from error
    except KeyboardInterrupt as error:
        registro.registrar_error("Ejecución cancelada por el usuario.")
        consola.mostrar_error("Ejecución cancelada (los contenedores se borraron).")
        raise typer.Exit(CODIGO_CANCELADO) from error

    registro.registrar_fin(resultado, cambios)
    consola.mostrar_fin(resultado, cambios, str(registro.ruta))
    if resultado.es_limite_alcanzado or resultado.es_validado is False:
        raise typer.Exit(1)


def _preparar_ejecucion(ticket: Path, repo: Path) -> _Preparacion:
    """Valida repo, ticket, configuración y harness.yaml antes de gastar pedidos al modelo."""
    if not repo.is_dir():
        consola.mostrar_error(f"No existe la carpeta del repo: {repo}")
        raise typer.Exit(1)
    try:
        texto_ticket = leer_ticket_local(ticket)
        # cargar_config carga el .env: después ya se pueden resolver los ${NOMBRE}.
        config = cargar_config()
        config_proyecto = cargar_config_proyecto(repo)
        variables = resolver_variables(config_proyecto) if config_proyecto else None
    except (ErrorTicket, ErrorConfiguracion, ErrorConfigProyecto, ErrorVariables) as error:
        consola.mostrar_error(str(error))
        raise typer.Exit(1) from error
    if config_proyecto is None:
        consola.mostrar_aviso(
            "El repo no tiene harness.yaml: se trabaja sin Docker, sin tests y sin validación. "
            "Créalo con 'harness init' o a mano (docs/harness-yaml.md)."
        )
    contexto = cargar_agents_md(repo)
    # Para ejecuciones sin nadie mirando la consola: el aviso de "falta AGENTS.md" no alcanza.
    if config_proyecto and config_proyecto.requiere_agents_md and not contexto.tiene_contenido:
        consola.mostrar_error(MENSAJE_REQUIERE_AGENTS)
        raise typer.Exit(1)
    return _Preparacion(
        repo, ticket.stem, texto_ticket, config, config_proyecto,
        variables or VariablesResueltas(), contexto,
    )


def _ejecutar(
    prep: _Preparacion, max_pasos: int, reporte: _Reporte
) -> tuple[ResultadoEjecucion, CambiosRepo]:
    proveedor = crear_proveedor(prep.config, reporte.reintento)
    if prep.config_proyecto is None:
        antes = tomar_instantanea(prep.repo)
        resultado = ejecutar_ticket(
            proveedor,
            crear_mensajes_iniciales(prep.texto_ticket, prep.contexto),
            crear_herramientas(prep.repo),
            max_pasos,
            al_paso=reporte.paso,
            al_aviso=reporte.aviso,
            planificacion=_planificacion(prep, reporte),
        )
        return resultado, comparar(antes, tomar_instantanea(prep.repo))
    # El with garantiza que los contenedores se borran aunque algo falle o haya Ctrl+C.
    with EntornoDocker(prep.repo, prep.config_proyecto, prep.ticket, prep.variables) as entorno:
        with consola.estado("Preparando Docker...") as estado:
            entorno.iniciar(al_avanzar=partial(consola.actualizar_estado, estado))
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
) -> tuple[ResultadoEjecucion, CambiosRepo]:
    limites = config_proyecto.limites
    # Después de instalar: lo que la instalación crea en el repo no es trabajo del modelo.
    antes = tomar_instantanea(prep.repo)
    # La copia se toma después de instalar: lo que cambie desde aquí lo cambió el modelo.
    guardia = GuardiaProtegidos(prep.repo, config_proyecto.archivos_protegidos)
    ejecutor = EjecutorComandos(
        entorno,
        guardia,
        limites.max_caracteres_salida,
        limites.timeout_comando_segundos,
        prep.variables.secretos,
    )
    comandos = config_proyecto.comandos
    resultado = ejecutar_ticket(
        proveedor,
        crear_mensajes_iniciales(prep.texto_ticket, prep.contexto, hay_validacion=True),
        crear_herramientas(prep.repo, config_proyecto, ejecutor, entorno.marcar_cambio),
        max_pasos,
        validar=partial(validar_proyecto, ejecutor, comandos.tests, comandos.lint),
        max_intentos_validacion=limites.max_intentos_validacion,
        al_paso=reporte.paso,
        al_validar=reporte.validacion,
        al_aviso=reporte.aviso,
        planificacion=_planificacion(prep, reporte),
    )
    return resultado, comparar(antes, tomar_instantanea(prep.repo))


def _avisar_recorte_plan(prep: _Preparacion, max_pasos: int, reporte: _Reporte) -> None:
    # harness.yaml ya garantiza max_pasos_plan <= max_pasos // 2: si se recorta, es porque
    # --max-pasos bajó el total.
    escrito = prep.config_proyecto.limites.max_pasos_plan if prep.config_proyecto else None
    if not prep.es_con_plan or escrito is None:
        return
    efectivo = calcular_pasos_plan(max_pasos, escrito)
    if efectivo < escrito:
        reporte.aviso(
            f"Pasos de planificación recortados de {escrito} a {efectivo} "
            f"por --max-pasos {max_pasos}."
        )


def _planificacion(prep: _Preparacion, reporte: _Reporte) -> Planificacion | None:
    if not prep.es_con_plan:
        return None
    limites = prep.config_proyecto.limites if prep.config_proyecto else ConfigLimites()
    return Planificacion(
        crear_herramientas_lectura(prep.repo), reporte.plan, limites.max_pasos_plan
    )


@app.command()
def init(
    repo: Annotated[
        Path | None, typer.Option(help="Repo objetivo; por defecto, la carpeta actual.")
    ] = None,
    verificar: Annotated[
        bool,
        typer.Option(
            "--verificar",
            help="Después, levantar el entorno con harness.yaml, instalar y correr los tests.",
        ),
    ] = False,
) -> None:
    """Genera AGENTS.md y harness.yaml si faltan (el modelo analiza el repo)."""
    raiz = (repo or Path.cwd()).resolve()
    if not raiz.is_dir():
        consola.mostrar_error(f"No existe la carpeta del repo: {raiz}")
        raise typer.Exit(1)
    faltantes = archivos_faltantes(raiz)
    if faltantes:
        _generar_archivos_init(raiz, faltantes)
    else:
        consola.mostrar_aviso("El repo ya tiene AGENTS.md y harness.yaml: no hay nada que generar.")
    if verificar:
        _verificar_init(raiz)


def _generar_archivos_init(repo: Path, faltantes: list[str]) -> None:
    try:
        config = cargar_config()
    except ErrorConfiguracion as error:
        consola.mostrar_error(str(error))
        raise typer.Exit(1) from error
    registro = RegistroPasos(repo, NOMBRE_LOG_INIT, secretos=[config.api_key])
    registro.registrar_inicio(config.proveedor, config.modelo)
    consola.mostrar_inicio_init(str(repo), config.proveedor, config.modelo, faltantes)
    reporte = _Reporte(registro)
    try:
        proveedor = crear_proveedor(config, reporte.reintento)
        resultado = ejecutar_init(proveedor, repo, reporte.paso, reporte.aviso)
    except (ErrorProveedor, ErrorRespuestaVacia, ErrorInit) as error:
        registro.registrar_error(str(error))
        consola.mostrar_error(str(error))
        raise typer.Exit(1) from error
    except KeyboardInterrupt as error:
        registro.registrar_error("Init cancelado por el usuario.")
        consola.mostrar_error("Init cancelado.")
        raise typer.Exit(CODIGO_CANCELADO) from error
    registro.registrar_fin_init(resultado)
    consola.mostrar_fin_init(resultado, str(registro.ruta))


def _verificar_init(repo: Path) -> None:
    try:
        # Las referencias ${NOMBRE} de harness.yaml se resuelven con el .env.
        cargar_env()
        config_proyecto = cargar_config_proyecto(repo)
        if config_proyecto is None:
            raise ErrorConfigProyecto("El repo no tiene harness.yaml: no hay nada que verificar.")
        variables = resolver_variables(config_proyecto)
        with consola.estado("Verificando harness.yaml...") as estado:
            resultado = verificar_config(
                repo, config_proyecto, variables, partial(consola.actualizar_estado, estado)
            )
    except (ErrorConfigProyecto, ErrorVariables, ErrorDocker) as error:
        consola.mostrar_error(str(error))
        raise typer.Exit(1) from error
    except KeyboardInterrupt as error:
        consola.mostrar_error("Verificación cancelada (los contenedores se borraron).")
        raise typer.Exit(CODIGO_CANCELADO) from error
    consola.mostrar_verificacion(resultado, ocultar_secretos(resultado.salida, variables.secretos))
    if resultado.codigo != 0 or resultado.es_tiempo_agotado:
        raise typer.Exit(1)


@app.command()
def version() -> None:
    """Muestra la versión del harness."""
    print("harness 0.1.0")


def main() -> None:
    consola.forzar_salida_utf8()
    app()
