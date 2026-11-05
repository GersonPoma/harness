from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Annotated

import typer
from rich import print

from harness import consola, github
from harness.bucle import MAX_PASOS_POR_DEFECTO, ErrorRespuestaVacia, Paso
from harness.cambios import CambiosRepo, comparar, tomar_instantanea
from harness.cliente_mcp import ErrorMCP, SesionMCP
from harness.comentario_pr import comentar_pr_en_jira
from harness.config import (
    RUTA_MCP,
    VAR_JIRA_TOKEN,
    VAR_TOKEN_GITHUB,
    ConfigJira,
    ConfigModelo,
    ErrorConfiguracion,
    cargar_config,
    cargar_env,
    leer_config_jira,
    leer_herramientas_github_modelo,
    leer_token_github,
)
from harness.config_mcp import (
    ErrorConfigMCP,
    ServidorMCP,
    cargar_config_mcp,
    elegir_servidor_github,
    elegir_servidor_jira,
)
from harness.github_mcp import crear_pull_request_mcp, puede_crear_pull_request
from harness.config_proyecto import (
    ConfigLimites,
    ConfigProyecto,
    ErrorConfigProyecto,
    cargar_config_proyecto,
)
from harness.contexto import ContextoProyecto, cargar_agents_md
from harness.ejecucion import Planificacion, ResultadoEjecucion, Validacion, ejecutar_ticket
from harness.entorno_docker import EntornoDocker, ErrorDocker
from harness.entrega import (
    ConfigEntrega,
    CrearPr,
    cerrar_sin_entregar,
    crear_config_entrega,
    crear_cuerpo_pr,
    crear_mensaje_commit,
    crear_rama_del_ticket,
    entregar,
    verificar_github,
    verificar_repo_para_entrega,
)
from harness.github import ErrorGitHub, PedidoPullRequest, PullRequest, RepoGitHub
from harness.git import ErrorGit
from harness.herramientas import crear_herramientas, crear_herramientas_lectura
from harness.herramientas.comandos import EjecutorComandos, validar_proyecto
from harness.herramientas.protegidos import GuardiaProtegidos
from harness.jira import ErrorJira, leer_ticket, verificar_credenciales
from harness.jira_mcp import ComentariosJiraMCP, herramientas_usadas, preparar_comentarios
from harness.herramientas.remotas import (
    HERRAMIENTAS_LECTURA_GITHUB,
    PREFIJO_GITHUB,
    crear_herramientas_mcp,
)
from harness.init import ErrorInit, archivos_faltantes, ejecutar_init, verificar_config
from harness.plan import Plan
from harness.planificacion import ErrorPlan, calcular_pasos_plan
from harness.prompts import crear_mensajes_iniciales
from harness.proveedores.base import ErrorProveedor, ProveedorLLM
from harness.proveedores.fabrica import crear_proveedor
from harness.registro import RegistroPasos
from harness.tipos import Herramienta
from harness.ticket import (
    MAX_CARACTERES_DESCRIPCION,
    ErrorTicket,
    Ticket,
    crear_ticket_local,
    resolver_origen,
)
from harness.variables import (
    ErrorVariables,
    VariablesResueltas,
    avisos_secretos_cortos,
    avisos_variables_cortas_yaml,
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
    datos_ticket: Ticket
    config: ConfigModelo
    config_proyecto: ConfigProyecto | None
    variables: VariablesResueltas
    contexto: ContextoProyecto
    es_con_plan: bool = True
    token_github: str | None = None
    # Servidor MCP de GitHub de mcp.json (None: no hay) y sus herramientas para el modelo.
    servidor_github: ServidorMCP | None = None
    herramientas_github: list[Herramienta] = field(default_factory=list)
    # Abierta solo si el PR se crea por MCP (el servidor tiene create_pull_request).
    sesion_github: SesionMCP | None = None
    # Solo si el ticket vino de Jira: su sitio y credenciales, y el servidor MCP de Jira de
    # mcp.json con la sesión abierta para comentar el link del PR (None: por la API REST).
    config_jira: ConfigJira | None = None
    servidor_jira: ServidorMCP | None = None
    comentarios_jira: ComentariosJiraMCP | None = None

    @property
    def ticket(self) -> str:
        """Clave del ticket: nombre de la rama, del log y del commit."""
        return self.datos_ticket.clave

    @property
    def texto_ticket(self) -> str:
        """El ticket tal como lo recibe el modelo."""
        return self.datos_ticket.a_markdown()

    @property
    def titulo(self) -> str:
        """Título del ticket, para el commit."""
        return self.datos_ticket.titulo

    @property
    def secretos(self) -> list[str]:
        """Lo que nunca se escribe en el log ni se le muestra al modelo."""
        token_jira = self.config_jira.token if self.config_jira else None
        tokens = [t for t in (self.token_github, token_jira) if t]
        mcp = [s for servidor in (self.servidor_github, self.servidor_jira) if servidor
               for s in servidor.secretos]
        return [self.config.api_key, *self.variables.secretos, *tokens, *mcp]


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
    ticket: Annotated[
        str,
        typer.Argument(help="Clave de Jira (PROYECTO-123) o ruta a un ticket local (.md)."),
    ],
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
    sin_push: Annotated[
        bool,
        typer.Option("--sin-push", help="Hacer el commit en la rama, pero no el push ni el PR."),
    ] = False,
    sin_pr: Annotated[
        bool, typer.Option("--sin-pr", help="Hacer commit y push, pero no el pull request.")
    ] = False,
    sin_herramientas_github: Annotated[
        bool,
        typer.Option(
            "--sin-herramientas-github",
            help="No darle al modelo las herramientas de GitHub por MCP (ahorra tokens).",
        ),
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
    registro.registrar_ticket(prep.datos_ticket)
    consola.mostrar_inicio(prep.ticket, str(prep.repo), prep.config.proveedor, prep.config.modelo)
    consola.mostrar_ticket(prep.datos_ticket)
    if prep.datos_ticket.es_recortado:
        aviso = _aviso_recorte_ticket(prep.datos_ticket)
        registro.registrar_aviso(aviso)
        consola.mostrar_aviso(aviso)
    consola.mostrar_contexto(prep.contexto)

    reporte = _Reporte(registro, aprobar_plan)
    _avisar_recorte_plan(prep, max_pasos, reporte)
    es_herramientas_github = not sin_herramientas_github and leer_herramientas_github_modelo()
    # El servidor MCP se abre en el preflight y se cierra al salir del with: también con
    # error o Ctrl+C.
    with ExitStack() as pila:
        conectar = partial(_conectar_mcp, prep, pila, reporte, es_herramientas_github)
        config_entrega = _preparar_rama(prep, not sin_push, not sin_pr, reporte, conectar)
        try:
            resultado, cambios = _ejecutar(prep, max_pasos, reporte)
        except (ErrorProveedor, ErrorDocker, ErrorRespuestaVacia, ErrorPlan) as error:
            registro.registrar_error(str(error))
            consola.mostrar_error(str(error))
            _cerrar_rama(prep, config_entrega, reporte)
            raise typer.Exit(1) from error
        except KeyboardInterrupt as error:
            registro.registrar_error("Ejecución cancelada por el usuario.")
            consola.mostrar_error("Ejecución cancelada (los contenedores se borraron).")
            _cerrar_rama(prep, config_entrega, reporte)
            raise typer.Exit(CODIGO_CANCELADO) from error

        registro.registrar_fin(resultado, cambios)
        consola.mostrar_fin(resultado, cambios, str(registro.ruta))
        es_entrega_ok = True
        if config_entrega:
            es_entrega_ok = _entregar(prep, config_entrega, resultado, cambios, reporte)
    if resultado.es_limite_alcanzado or resultado.es_validado is False or not es_entrega_ok:
        raise typer.Exit(1)


def _conectar_mcp(
    prep: _Preparacion,
    pila: ExitStack,
    reporte: _Reporte,
    es_herramientas: bool,
    es_con_pr: bool,
) -> None:
    """Los servidores MCP del preflight: GitHub (herramientas y PR) y Jira (comentario)."""
    _conectar_github_mcp(prep, pila, reporte, es_herramientas, es_con_pr)
    # El comentario con el link del PR solo existe si hay PR y el ticket vino de Jira.
    if es_con_pr and prep.config_jira is not None:
        _conectar_jira_mcp(prep, pila, reporte)


def _conectar_jira_mcp(prep: _Preparacion, pila: ExitStack, reporte: _Reporte) -> None:
    """Decide por dónde se comenta en Jira: MCP o la API REST. Nunca da herramientas al modelo.

    Si el servidor no conecta o no sirve, se avisa y se sigue: la API REST alcanza.
    """
    servidor = prep.servidor_jira
    if servidor is None:
        consola.mostrar_jira_rest("no hay servidor MCP de Jira en mcp.json")
        return
    ruta_log = reporte.registro.ruta.parent / f"{prep.ticket}-mcp-{servidor.nombre}.log"
    try:
        with consola.estado(f"Conectando con el servidor MCP {servidor.nombre}..."):
            sesion = pila.enter_context(SesionMCP(servidor, ruta_log, prep.ticket))
        comentarios = preparar_comentarios(sesion, prep.config_jira)
    except (ErrorMCP, ErrorJira) as error:
        reporte.registro.registrar_mcp(servidor, [], es_pr=False, error=str(error))
        reporte.aviso(f"{error} El link del PR se comenta por la API REST de Jira.")
        return
    if comentarios is None:
        reporte.aviso(
            f"El servidor MCP '{servidor.nombre}' no tiene las herramientas para comentar en "
            "Jira: el link del PR se comenta por la API REST."
        )
        return
    prep.comentarios_jira = comentarios
    usadas = herramientas_usadas(sesion.herramientas)
    reporte.registro.registrar_mcp(servidor, [], es_pr=False, usadas=usadas)
    consola.mostrar_jira_mcp(servidor.nombre, servidor.transporte)


def _conectar_github_mcp(
    prep: _Preparacion,
    pila: ExitStack,
    reporte: _Reporte,
    es_herramientas: bool,
    es_con_pr: bool,
) -> None:
    """Decide por dónde va GitHub: MCP (herramientas del modelo y/o el PR) o la API REST.

    Si el servidor no conecta, se avisa y se sigue: la API REST alcanza para el PR.
    """
    servidor = prep.servidor_github
    if servidor is None:
        if es_con_pr:
            consola.mostrar_github_rest("no hay servidor MCP de GitHub en mcp.json")
        return
    if not es_herramientas and not es_con_pr:
        consola.mostrar_github_mcp_sin_uso()
        return
    ruta_log = reporte.registro.ruta.parent / f"{prep.ticket}-mcp-{servidor.nombre}.log"
    try:
        with consola.estado(f"Conectando con el servidor MCP {servidor.nombre}..."):
            sesion = pila.enter_context(SesionMCP(servidor, ruta_log, prep.ticket))
    except ErrorMCP as error:
        reporte.registro.registrar_mcp(servidor, [], es_pr=False, error=str(error))
        reporte.aviso(f"{error} {_sin_mcp(es_herramientas, es_con_pr)}")
        return
    _usar_sesion_github(prep, sesion, reporte, es_herramientas, es_con_pr)


def _usar_sesion_github(
    prep: _Preparacion,
    sesion: SesionMCP,
    reporte: _Reporte,
    es_herramientas: bool,
    es_con_pr: bool,
) -> None:
    """Arma las herramientas del modelo y decide si el PR va por MCP; lo avisa y lo registra."""
    servidor = sesion.servidor
    if es_herramientas:
        limites = prep.config_proyecto.limites if prep.config_proyecto else ConfigLimites()
        prep.herramientas_github = crear_herramientas_mcp(
            sesion,
            PREFIJO_GITHUB,
            HERRAMIENTAS_LECTURA_GITHUB,
            prep.secretos,
            limites.max_caracteres_salida,
        )
    if es_con_pr and puede_crear_pull_request(sesion.herramientas):
        prep.sesion_github = sesion
    elif es_con_pr:
        reporte.aviso(
            f"El servidor MCP '{servidor.nombre}' no tiene create_pull_request con los "
            "parámetros que usa el harness (¿falta el toolset pull_requests?): el PR se crea "
            "por la API REST."
        )
    nombres = [h.nombre for h in prep.herramientas_github]
    es_pr = prep.sesion_github is not None
    reporte.registro.registrar_mcp(servidor, nombres, es_pr)
    consola.mostrar_github_mcp(servidor.nombre, servidor.transporte, len(nombres), es_pr)


def _sin_mcp(es_herramientas: bool, es_con_pr: bool) -> str:
    sin = ["sin las herramientas de GitHub para el modelo"] if es_herramientas else []
    sin += ["con el PR por la API REST"] if es_con_pr else []
    return f"Se sigue {' y '.join(sin)}."


def _preparar_rama(
    prep: _Preparacion,
    es_con_push: bool,
    es_con_pr: bool,
    reporte: _Reporte,
    conectar_github: Callable[[bool], None],
) -> ConfigEntrega | None:
    """Preflight antes de gastar pedidos: Git, GitHub (token, y MCP o REST) y la rama.

    Sin harness.yaml no hay entrega: solo se conecta el MCP, para las herramientas del modelo.
    """
    if prep.config_proyecto is None:
        conectar_github(False)
        return None
    config = crear_config_entrega(
        prep.config_proyecto.git, prep.ticket, es_con_push, es_con_pr
    )
    try:
        verificar_repo_para_entrega(prep.repo, config, reporte.aviso)
        config = _verificar_github(prep, config, reporte)
        # Después del token: si se eligió seguir sin PR, el MCP ya no se usa para el PR.
        conectar_github(config.es_con_pr)
        crear_rama_del_ticket(prep.repo, config)
    except ErrorGit as error:
        reporte.registro.registrar_error(str(error))
        consola.mostrar_error(str(error))
        raise typer.Exit(1) from error
    reporte.registro.registrar_rama(config)
    consola.mostrar_rama(config)
    return config


def _verificar_github(
    prep: _Preparacion, config: ConfigEntrega, reporte: _Reporte
) -> ConfigEntrega:
    """Antes de gastar pedidos: si el token no sirve para el PR, sigue sin PR o se detiene."""
    if not config.es_con_pr:
        return config
    try:
        repo_github = verificar_github(prep.repo, config, prep.token_github)
    except ErrorGitHub as error:
        return _decidir_sin_pr(config, str(error), reporte)
    if repo_github is not None:
        consola.mostrar_github_verificado(str(repo_github))
    return config


def _decidir_sin_pr(config: ConfigEntrega, problema: str, reporte: _Reporte) -> ConfigEntrega:
    """Pregunta si se sigue como --sin-pr; sin terminal o con un no, se detiene sin rama."""
    if not consola.es_terminal_interactiva():
        mensaje = f"{problema} Sin terminal para preguntar: usa --sin-pr para seguir sin PR."
        reporte.registro.registrar_error(mensaje)
        consola.mostrar_error(mensaje)
        raise typer.Exit(1)
    reporte.aviso(problema)
    if not consola.preguntar_continuar_sin_pr():
        mensaje = "Detenido antes de crear la rama: arregla el token o usa --sin-pr."
        reporte.registro.registrar_error(mensaje)
        consola.mostrar_error(mensaje)
        raise typer.Exit(1)
    reporte.aviso("Se sigue sin PR, como con --sin-pr: al terminar, solo commit y push.")
    return replace(config, es_con_pr=False)


def _entregar(
    prep: _Preparacion,
    config: ConfigEntrega,
    resultado: ResultadoEjecucion,
    cambios: CambiosRepo,
    reporte: _Reporte,
) -> bool:
    """Commit, push y PR; devuelve False si el PR falló (el push sí se hizo)."""
    mensaje = crear_mensaje_commit(prep.ticket, prep.titulo)

    def armar_pedido_pr(repo_github: RepoGitHub) -> PedidoPullRequest:
        comandos = prep.config_proyecto.comandos
        modelo = f"{prep.config.proveedor}/{prep.config.modelo}"
        # El resumen lo escribió el modelo: podría repetir un secreto que vio en una salida.
        cuerpo = ocultar_secretos(
            crear_cuerpo_pr(resultado, cambios, comandos, modelo), prep.secretos
        )
        return PedidoPullRequest(repo_github, config.rama, config.base, mensaje, cuerpo)

    crear_pr = _crear_pr_por_mcp(prep, reporte) if prep.sesion_github else None
    try:
        entrega = entregar(
            prep.repo, config, resultado, mensaje, armar_pedido_pr, prep.token_github, crear_pr
        )
    except ErrorGit as error:
        reporte.registro.registrar_error(str(error))
        consola.mostrar_error(str(error))
        raise typer.Exit(1) from error
    reporte.registro.registrar_entrega(entrega)
    consola.mostrar_entrega(entrega, config)
    # Solo con un PR (creado o que ya existía) y un ticket de Jira. Si el comentario falla,
    # solo se avisa: no cambia el código de salida.
    if entrega.pull_request is not None and prep.config_jira is not None:
        _comentar_en_jira(prep, entrega.pull_request, reporte)
    return entrega.error_pr is None


def _comentar_en_jira(prep: _Preparacion, pull_request: PullRequest, reporte: _Reporte) -> None:
    """El link del PR en el ticket de Jira, por MCP o por la API REST, sin repetirlo."""
    resultado = comentar_pr_en_jira(
        prep.config_jira, prep.ticket, pull_request, prep.comentarios_jira, reporte.aviso
    )
    reporte.registro.registrar_comentario_jira(prep.ticket, resultado)
    consola.mostrar_comentario_jira(
        prep.ticket, prep.datos_ticket.url or "", pull_request.url, resultado
    )


def _crear_pr_por_mcp(prep: _Preparacion, reporte: _Reporte) -> CrearPr:
    """PR con create_pull_request del servidor MCP; si la sesión se cortó, por la API REST."""

    def crear(pedido: PedidoPullRequest) -> PullRequest:
        try:
            return crear_pull_request_mcp(pedido, prep.sesion_github)
        except ErrorMCP as error:
            # El push ya está hecho: no vale la pena perder el PR por el servidor MCP.
            reporte.aviso(f"{error} Se reintenta el PR por la API REST.")
            return github.crear_pull_request(pedido, prep.token_github)

    return crear


def _cerrar_rama(
    prep: _Preparacion, config: ConfigEntrega | None, reporte: _Reporte
) -> None:
    """Después de un error: borra la rama si quedó vacía; si no, avisa dónde quedaron."""
    if config is None:
        return
    try:
        entrega = cerrar_sin_entregar(prep.repo, config)
    except ErrorGit as error:
        reporte.aviso(f"No se pudo cerrar la rama {config.rama}: {error}")
        return
    reporte.registro.registrar_entrega(entrega)
    consola.mostrar_entrega(entrega, config)


def _preparar_ejecucion(ticket: str, repo: Path) -> _Preparacion:
    """Valida repo, ticket, configuración y harness.yaml antes de gastar pedidos al modelo.

    Un ticket de Jira se lee aquí, antes de todo lo de Git: sin ticket no hay nada que hacer,
    y así un error nunca deja una rama creada.
    """
    if not repo.is_dir():
        consola.mostrar_error(f"No existe la carpeta del repo: {repo}")
        raise typer.Exit(1)
    try:
        # cargar_config carga el .env: después ya se pueden resolver los ${NOMBRE}.
        config = cargar_config()
        config_proyecto = cargar_config_proyecto(repo)
        variables = resolver_variables(config_proyecto) if config_proyecto else None
        servidores = cargar_config_mcp(RUTA_MCP)
        servidor_github = elegir_servidor_github(servidores)
        servidor_jira = elegir_servidor_jira(servidores)
        datos_ticket, config_jira = _obtener_ticket(ticket)
    except (
        ErrorTicket, ErrorConfiguracion, ErrorConfigProyecto, ErrorVariables, ErrorConfigMCP,
        ErrorJira,
    ) as error:
        consola.mostrar_error(str(error))
        raise typer.Exit(1) from error
    if config_proyecto is None:
        consola.mostrar_aviso(
            "El repo no tiene harness.yaml: se trabaja sin Docker, sin tests y sin validación, "
            "y por eso sin commit ni push. Créalo con 'harness init' o a mano "
            "(docs/harness-yaml.md)."
        )
    _avisar_secretos_cortos(config, variables, servidor_github, servidor_jira)
    contexto = cargar_agents_md(repo)
    # Para ejecuciones sin nadie mirando la consola: el aviso de "falta AGENTS.md" no alcanza.
    if config_proyecto and config_proyecto.requiere_agents_md and not contexto.tiene_contenido:
        consola.mostrar_error(MENSAJE_REQUIERE_AGENTS)
        raise typer.Exit(1)
    return _Preparacion(
        repo, datos_ticket, config, config_proyecto,
        variables or VariablesResueltas(), contexto, token_github=leer_token_github(),
        servidor_github=servidor_github, config_jira=config_jira, servidor_jira=servidor_jira,
    )


def _avisar_secretos_cortos(
    config: ConfigModelo,
    variables: VariablesResueltas | None,
    servidor_github: ServidorMCP | None,
    servidor_jira: ServidorMCP | None,
) -> None:
    """Los secretos de menos de MIN_CARACTERES_SECRETO no se ocultan: se avisa una vez."""
    tokens = [config.variable_api_key or "", VAR_TOKEN_GITHUB, VAR_JIRA_TOKEN]
    for servidor in (servidor_github, servidor_jira):
        tokens += list(servidor.variables_secretas) if servidor else []
    avisos = avisos_secretos_cortos(tokens)
    avisos += avisos_variables_cortas_yaml(variables.nombres_secretos if variables else [])
    for aviso in avisos:
        consola.mostrar_aviso(aviso)


def _obtener_ticket(texto: str) -> tuple[Ticket, ConfigJira | None]:
    """El ticket de un .md local o de Jira (y la configuración de Jira, para comentar después).

    De Jira se lee siempre por la API REST: su ADF pasa por nuestro conversor sin perder nada.
    Lanza ErrorTicket, ErrorConfiguracion o ErrorJira.
    """
    origen = resolver_origen(texto)
    if origen.ruta is not None:
        return crear_ticket_local(origen.ruta), None
    config_jira = leer_config_jira()
    with consola.estado(f"Leyendo {origen.clave} de Jira..."):
        verificar_credenciales(config_jira)
        return leer_ticket(config_jira, origen.clave), config_jira


def _aviso_recorte_ticket(ticket: Ticket) -> str:
    return (
        f"La descripción de {ticket.clave} tiene {ticket.caracteres_originales} caracteres: "
        f"el modelo recibe los primeros {MAX_CARACTERES_DESCRIPCION}."
    )


def _ejecutar(
    prep: _Preparacion, max_pasos: int, reporte: _Reporte
) -> tuple[ResultadoEjecucion, CambiosRepo]:
    proveedor = crear_proveedor(prep.config, reporte.reintento)
    if prep.config_proyecto is None:
        antes = tomar_instantanea(prep.repo)
        resultado = ejecutar_ticket(
            proveedor,
            crear_mensajes_iniciales(
                prep.texto_ticket, prep.contexto, hay_github_mcp=bool(prep.herramientas_github)
            ),
            crear_herramientas(prep.repo, extras=prep.herramientas_github),
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
        crear_mensajes_iniciales(
            prep.texto_ticket,
            prep.contexto,
            hay_validacion=True,
            hay_github_mcp=bool(prep.herramientas_github),
        ),
        crear_herramientas(
            prep.repo, config_proyecto, ejecutor, entorno.marcar_cambio, prep.herramientas_github
        ),
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
        crear_herramientas_lectura(prep.repo, prep.herramientas_github),
        reporte.plan,
        limites.max_pasos_plan,
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


@app.command(name="ticket")
def ver_ticket(
    ticket: Annotated[
        str,
        typer.Argument(help="Clave de Jira (PROYECTO-123) o ruta a un ticket local (.md)."),
    ],
) -> None:
    """Muestra el ticket tal como lo recibe el modelo, sin ejecutar nada."""
    try:
        cargar_env()
        datos, _ = _obtener_ticket(ticket)
    except (ErrorTicket, ErrorConfiguracion, ErrorJira) as error:
        consola.mostrar_error(str(error))
        raise typer.Exit(1) from error
    consola.mostrar_ticket(datos)
    if datos.es_recortado:
        consola.mostrar_aviso(_aviso_recorte_ticket(datos))
    consola.mostrar_texto_ticket(datos.a_markdown())


@app.command()
def version() -> None:
    """Muestra la versión del harness."""
    print("harness 0.1.0")


def main() -> None:
    consola.forzar_salida_utf8()
    app()
