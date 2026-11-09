import os
from collections.abc import Callable
from contextlib import ExitStack, nullcontext
from dataclasses import asdict, dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Annotated

import typer
from rich import print

from harness import consola, git, github
from harness.bucle import MAX_PASOS_POR_DEFECTO, ErrorBucle, ErrorRespuestaVacia, Paso
from harness.alcance import Alcance, EstadoAlcance
from harness.bucles import DetectorBucles
from harness.revision import EstadoRevision, Revision, armar_diff
from harness.cambios import CambiosRepo, Instantanea, comparar, tomar_instantanea
from harness.cliente_mcp import ErrorMCP, SesionMCP
from harness.comentario_pr import COMENTADO, YA_ESTABA, comentar_pr_en_jira
from harness.compactacion import Compactacion, ProveedorQueCompacta
from harness.config import (
    RUTA_MCP,
    VAR_JIRA_TOKEN,
    VAR_TOKEN_GITHUB,
    ConfigJira,
    ConfigModelo,
    ErrorConfiguracion,
    cargar_config,
    cargar_env,
    elegir_modelo,
    leer_config_jira,
    leer_herramientas_github_modelo,
    leer_respaldo,
    leer_token_github,
)
from harness.config_mcp import (
    ErrorConfigMCP,
    ServidorMCP,
    VAR_SERVIDOR_GITHUB,
    VAR_SERVIDOR_JIRA,
    cargar_config_mcp,
    elegir_servidor_github,
    elegir_servidor_jira,
    motivo_sin_servidor,
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
from harness.cache_dependencias import EstadoCache
from harness.entorno_docker import EntornoDocker, ErrorDocker, Instalacion
from harness.entrega import (
    ConfigEntrega,
    CrearPr,
    ResultadoEntrega,
    continuar_entrega,
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
from harness.init import (
    ErrorInit,
    archivos_faltantes,
    asegurar_gitignore,
    ejecutar_init,
    verificar_config,
)
from harness.memoria import (
    RUTA_MEMORIA,
    Memoria,
    cargar_memoria,
    crear_herramienta_memoria,
    esta_ignorada,
    notas_nuevas,
)
from harness.plan import Plan
from harness.planificacion import ErrorPlan, calcular_pasos_plan
from harness.prompts import crear_mensajes_iniciales
from harness.proveedores.base import ErrorProveedor, ProveedorLLM
from harness.proveedores.fabrica import crear_proveedor, identificador
from harness.proveedores.respaldo import CambioProveedor
from harness.registro import RegistroPasos
from harness.sesion import (
    CARPETA_SESIONES,
    ESTADO_ENTREGA,
    ESTADO_IMPLEMENTACION,
    ESTADO_PLAN,
    MOTIVO_BUCLE,
    MOTIVO_CTRL_C,
    MOTIVO_ERROR,
    MOTIVO_LIMITE_PASOS,
    MOTIVO_PROVEEDOR,
    MOTIVO_VALIDACION,
    ErrorSesion,
    Reanudacion,
    Sesion,
    SesionEnCurso,
    armar_reanudacion,
    cabeza,
    cargar_sesion,
    descartar_sesion,
    estado_alcance,
    estado_revision,
)
from harness.tipos import Herramienta, Mensaje
from harness.ticket import (
    FUENTE_JIRA,
    FUENTE_LOCAL,
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
# Archivo donde se copia la salida cruda de la consola (diagnóstico de líneas perdidas).
VAR_CONSOLA_CRUDA = "HARNESS_CONSOLA_CRUDA"
AYUDA_SIN_REVISION = (
    "No hacer el paso de revisión del diff (con los tests en verde) en esta ejecución."
)
AYUDA_SIN_CACHE = (
    "No usar la caché de dependencias: se descarga todo de nuevo (el volumen no se toca)."
)

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
    es_con_cache: bool = True
    es_con_revision: bool = True
    # HARNESS_RESPALDO: si el proveedor principal se cae, se sigue con estos, en orden.
    respaldo: list[ConfigModelo] = field(default_factory=list)
    # .harness/sessions/<TICKET>.json, para harness resume (se crea después de la rama).
    sesion: SesionEnCurso | None = None
    # Con harness resume: la conversación guardada y lo ya hecho (None: se empieza de cero).
    reanudacion: Reanudacion | None = None
    # Con harness resume: el texto del ticket tal como lo recibió el modelo la primera vez.
    texto_guardado: str | None = None
    # .harness/memory.md de la rama actual (lo que dejaron los tickets ya mergeados).
    memoria: Memoria = field(default_factory=Memoria)
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
        return self.texto_guardado or self.datos_ticket.a_markdown()

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
        respaldo = [config.api_key for config in self.respaldo]
        return [self.config.api_key, *respaldo, *self.variables.secretos, *tokens, *mcp]


class _Reporte:
    """Muestra en consola y registra en el log lo que pasa durante la ejecución."""

    def __init__(
        self, registro: RegistroPasos, es_aprobar_plan: bool = False, modelo: str = ""
    ) -> None:
        self.registro = registro
        self._es_aprobar_plan = es_aprobar_plan
        self.ultimo_paso = 0
        # Los modelos que respondieron, en orden (más de uno si se pasó al respaldo).
        self.modelos_usados = [modelo] if modelo else []
        self.sesion: SesionEnCurso | None = None
        self._es_memoria_revisada = False
        # Las notas se guardan durante el paso; se muestran después de él, no antes.
        self._notas_pendientes: list[tuple[Path, str]] = []

    def paso(self, paso: Paso) -> None:
        self.ultimo_paso = paso.numero
        consola.mostrar_paso(paso)
        self.registro.registrar_paso(paso)
        for repo, nota in self._notas_pendientes:
            self._mostrar_nota(repo, nota)
        self._notas_pendientes.clear()
        # Después de cada herramienta: un corte en cualquier momento pierde como mucho un paso.
        if self.sesion:
            self.sesion.paso(paso.numero)

    def cambio_proveedor(self, cambio: CambioProveedor) -> None:
        # El pedido que falló es el del paso siguiente al último que terminó.
        paso = self.ultimo_paso + 1
        self.modelos_usados.append(cambio.siguiente)
        consola.mostrar_cambio_proveedor(cambio, paso)
        self.registro.registrar_cambio_proveedor(cambio, paso)
        if self.sesion:
            self.sesion.cambio_de_proveedor(cambio.siguiente)

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
        if es_aprobado and self.sesion:
            self.sesion.plan_aprobado(plan.model_dump())
        return es_aprobado

    def fuera_del_plan(self, estado: EstadoAlcance) -> None:
        consola.mostrar_alcance(estado)
        self.registro.registrar_fuera_del_plan(estado)
        if self.sesion:
            self.sesion.alcance(estado)

    def revision(self, estado: EstadoRevision) -> None:
        consola.mostrar_revision(estado)
        self.registro.registrar_revision(estado)
        if self.sesion:
            self.sesion.revision(estado)

    def validacion(self, validacion: Validacion) -> None:
        consola.mostrar_validacion(validacion)
        self.registro.registrar_validacion(validacion)
        if self.sesion:
            self.sesion.validacion()

    def nota_guardada(self, repo: Path, nota: str) -> None:
        self._notas_pendientes.append((repo, nota))

    def _mostrar_nota(self, repo: Path, nota: str) -> None:
        consola.mostrar_nota_guardada(nota)
        self.registro.registrar_memoria(nota)
        if not self._es_memoria_revisada:
            self._es_memoria_revisada = True
            if esta_ignorada(repo):
                self.aviso(
                    f"{RUTA_MEMORIA} está ignorado por el .gitignore: la nota no entrará en el "
                    "commit. Ignora solo .harness/logs/ y .harness/sessions/."
                )

    def compactacion(self, compactacion: Compactacion) -> None:
        consola.mostrar_compactacion(compactacion)
        self.registro.registrar_compactacion(compactacion)

    def cache(self, cache: EstadoCache) -> None:
        consola.mostrar_cache(cache)
        self.registro.registrar_cache(cache)

    def instalacion(self, instalacion: Instalacion) -> None:
        consola.mostrar_instalacion(instalacion)
        self.registro.registrar_instalacion(instalacion)


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
    sin_cache: Annotated[
        bool,
        typer.Option("--sin-cache", help=AYUDA_SIN_CACHE),
    ] = False,
    sin_revision: Annotated[
        bool, typer.Option("--sin-revision", help=AYUDA_SIN_REVISION)
    ] = False,
    desde_cero: Annotated[
        bool,
        typer.Option(
            "--desde-cero",
            help="Si hay una sesión sin terminar del ticket, descartarla (se renombra, no se "
            "borra) y empezar de nuevo.",
        ),
    ] = False,
) -> None:
    """Procesa un ticket y genera el código en el repo objetivo."""
    if sin_plan and aprobar_plan:
        consola.mostrar_error("--sin-plan y --aprobar-plan no se pueden usar juntos.")
        raise typer.Exit(1)
    prep = _preparar_ejecucion(ticket, (repo or Path.cwd()).resolve())
    _revisar_sesion_pendiente(prep, desde_cero)
    prep.es_con_plan = not sin_plan
    prep.es_con_cache = not sin_cache
    prep.es_con_revision = not sin_revision
    limites = prep.config_proyecto.limites if prep.config_proyecto else None
    max_pasos = max_pasos or (limites.max_pasos if limites else MAX_PASOS_POR_DEFECTO)
    reporte = _empezar_registro(prep, aprobar_plan)
    consola.mostrar_ticket(prep.datos_ticket)
    if prep.datos_ticket.es_recortado:
        reporte.aviso(_aviso_recorte_ticket(prep.datos_ticket))
    consola.mostrar_contexto(prep.contexto)
    consola.mostrar_memoria(prep.memoria)
    _avisar_recorte_plan(prep, max_pasos, reporte)
    es_herramientas_github = not sin_herramientas_github and leer_herramientas_github_modelo()
    # El servidor MCP se abre en el preflight y se cierra al salir del with: también con
    # error o Ctrl+C.
    with ExitStack() as pila:
        conectar = partial(_conectar_mcp, prep, pila, reporte, es_herramientas_github)
        config_entrega = _preparar_rama(prep, not sin_push, not sin_pr, reporte, conectar)
        _iniciar_sesion(prep, config_entrega, reporte)
        resultado, es_entrega_ok = _ejecutar_y_entregar(prep, config_entrega, max_pasos, reporte)
    _sugerir_resume(prep)
    if resultado.es_limite_alcanzado or resultado.es_validado is False or not es_entrega_ok:
        raise typer.Exit(1)


def _empezar_registro(prep: _Preparacion, es_aprobar_plan: bool = False) -> _Reporte:
    """El log de la ejecución, con su inicio y el ticket, y la cabecera de la consola."""
    registro = RegistroPasos(prep.repo, prep.ticket, secretos=prep.secretos)
    respaldo = [identificador(config) for config in prep.respaldo]
    registro.registrar_inicio(
        prep.config.proveedor, prep.config.modelo, prep.contexto, respaldo=respaldo
    )
    registro.registrar_ticket(prep.datos_ticket)
    consola.mostrar_inicio(
        prep.ticket, str(prep.repo), prep.config.proveedor, prep.config.modelo, respaldo
    )
    return _Reporte(registro, es_aprobar_plan, identificador(prep.config))


def _motivo_de(error: Exception) -> str:
    """El motivo de corte que queda en la sesión para un error de la ejecución."""
    if isinstance(error, ErrorProveedor):
        return MOTIVO_PROVEEDOR
    if isinstance(error, ErrorBucle):
        return MOTIVO_BUCLE
    return MOTIVO_ERROR


def _ejecutar_y_entregar(
    prep: _Preparacion, config_entrega: ConfigEntrega | None, max_pasos: int, reporte: _Reporte
) -> tuple[ResultadoEjecucion, bool]:
    """Ejecuta y entrega; ante un corte (error, Ctrl+C) guarda la sesión y termina."""
    registro = reporte.registro
    try:
        resultado, cambios = _ejecutar(prep, max_pasos, reporte)
    except (ErrorProveedor, ErrorDocker, ErrorRespuestaVacia, ErrorPlan, ErrorBucle) as error:
        registro.registrar_error(str(error))
        consola.mostrar_error(str(error))
        motivo = _motivo_de(error)
        _cortar_sesion(prep, motivo, str(error))
        _cerrar_rama(prep, config_entrega, reporte)
        _sugerir_resume(prep)
        raise typer.Exit(1) from error
    except KeyboardInterrupt as error:
        registro.registrar_error("Ejecución cancelada por el usuario.")
        consola.mostrar_error("Ejecución cancelada (los contenedores se borraron).")
        _cortar_sesion(prep, MOTIVO_CTRL_C)
        _cerrar_rama(prep, config_entrega, reporte)
        _sugerir_resume(prep)
        raise typer.Exit(CODIGO_CANCELADO) from error
    registro.registrar_fin(resultado, cambios)
    consola.mostrar_fin(resultado, cambios, str(registro.ruta))
    _terminar_ejecucion_en_sesion(prep, resultado, config_entrega)
    es_entrega_ok = True
    if config_entrega:
        es_entrega_ok = _entregar(prep, config_entrega, resultado, cambios, reporte)
    return resultado, es_entrega_ok


@app.command()
def resume(
    ticket: Annotated[
        str, typer.Argument(help="Clave del ticket que se cortó (PROYECTO-123 o T-1).")
    ],
    repo: Annotated[
        Path | None, typer.Option(help="Repo objetivo; por defecto, la carpeta actual.")
    ] = None,
    max_pasos: Annotated[
        int | None,
        typer.Option(
            min=1, help="Pedidos al modelo para esta parte (por defecto, el de harness.yaml)."
        ),
    ] = None,
    proveedor: Annotated[
        str | None,
        typer.Option(help="Retomar con otro proveedor (con --modelo); después, el respaldo."),
    ] = None,
    modelo: Annotated[str | None, typer.Option(help="Retomar con otro modelo.")] = None,
    sin_cache: Annotated[
        bool, typer.Option("--sin-cache", help=AYUDA_SIN_CACHE)
    ] = False,
    sin_revision: Annotated[
        bool, typer.Option("--sin-revision", help=AYUDA_SIN_REVISION)
    ] = False,
    sin_herramientas_github: Annotated[
        bool,
        typer.Option(
            "--sin-herramientas-github",
            help="No darle al modelo las herramientas de GitHub por MCP (ahorra tokens).",
        ),
    ] = False,
) -> None:
    """Continúa una ejecución que se cortó, desde donde quedó (sin empezar de cero)."""
    raiz = (repo or Path.cwd()).resolve()
    sesion = _sesion_para_retomar(raiz, ticket)
    prep = _preparar_ejecucion(
        sesion.clave, raiz, guardado=_ticket_guardado(sesion), proveedor=proveedor, modelo=modelo
    )
    prep.es_con_plan = sesion.es_con_plan
    prep.es_con_cache = not sin_cache
    prep.es_con_revision = not sin_revision
    prep.texto_guardado = sesion.ticket.get("texto")
    limites = prep.config_proyecto.limites if prep.config_proyecto else None
    presupuesto = max_pasos or (limites.max_pasos if limites else MAX_PASOS_POR_DEFECTO)
    reporte = _empezar_registro(prep)
    # La memoria de la rama de hoy: el system prompt se regenera con ella al retomar.
    consola.mostrar_memoria(prep.memoria)
    es_herramientas_github = not sin_herramientas_github and leer_herramientas_github_modelo()
    with ExitStack() as pila:
        conectar = partial(_conectar_mcp, prep, pila, reporte, es_herramientas_github)
        config_entrega = _preparar_rama_para_retomar(prep, sesion, reporte, conectar)
        _retomar_sesion(prep, sesion, reporte)
        if sesion.estado == ESTADO_ENTREGA:
            resultado, es_entrega_ok = _retomar_entrega(prep, sesion, config_entrega, reporte)
        else:
            # Los pasos siguen contando desde el corte; el presupuesto es para esta parte.
            continuacion = prep.reanudacion.continuacion if prep.reanudacion else None
            hechos = continuacion.pasos_con_presupuesto if continuacion else 0
            resultado, es_entrega_ok = _ejecutar_y_entregar(
                prep, config_entrega, hechos + presupuesto, reporte
            )
    _sugerir_resume(prep)
    if resultado.es_limite_alcanzado or resultado.es_validado is False or not es_entrega_ok:
        raise typer.Exit(1)


def _sesion_para_retomar(repo: Path, ticket: str) -> Sesion:
    """La sesión sin terminar del ticket; si no hay, o ya terminó, lo dice y sale."""
    # Se acepta también la ruta del ticket local que se le pasó a run.
    clave = Path(ticket).stem if ticket.lower().endswith(".md") else ticket
    try:
        sesion = cargar_sesion(repo, clave) or cargar_sesion(repo, clave.upper())
    except ErrorSesion as error:
        consola.mostrar_error(str(error))
        raise typer.Exit(1) from error
    if sesion is None:
        consola.mostrar_error(
            f"No hay una ejecución de {clave} para retomar en {repo / CARPETA_SESIONES}."
        )
        raise typer.Exit(1)
    if sesion.es_completada:
        consola.mostrar_aviso(f"{sesion.clave} ya está completa: no hay nada que retomar.")
        raise typer.Exit(0)
    return sesion


def _ticket_guardado(sesion: Sesion) -> Ticket:
    """El ticket de la sesión: el texto completo ya está en la conversación guardada."""
    datos = sesion.ticket
    return Ticket(
        clave=sesion.clave, titulo=str(datos.get("titulo") or ""), descripcion="",
        fuente=str(datos.get("fuente") or FUENTE_LOCAL), url=datos.get("url"),
    )


def _preparar_rama_para_retomar(
    prep: _Preparacion, sesion: Sesion, reporte: _Reporte, conectar_github: Callable[[bool], None]
) -> ConfigEntrega | None:
    """Preflight de resume: la rama de la sesión (sin exigir el árbol limpio), GitHub y MCP."""
    if prep.config_proyecto is None or sesion.rama is None:
        conectar_github(False)
        return None
    rama = sesion.rama
    config = ConfigEntrega(
        prep.ticket, rama["nombre"], rama["base"], rama["remoto"],
        bool(rama.get("es_con_push", True)), bool(rama.get("es_con_pr", True)),
    )
    try:
        es_recreada = _volver_a_la_rama(prep.repo, config)
        config = _verificar_github(prep, config, reporte) if config.es_con_push else config
        conectar_github(config.es_con_pr)
    except ErrorGit as error:
        reporte.registro.registrar_error(str(error))
        consola.mostrar_error(str(error))
        raise typer.Exit(1) from error
    consola.mostrar_rama_retomada(config, es_recreada)
    return config


def _volver_a_la_rama(repo: Path, config: ConfigEntrega) -> bool:
    """Exige estar en la rama del ticket; si se borró porque quedó vacía, la crea de nuevo.

    Nunca cambia de rama sola: podría pisar trabajo del usuario. Devuelve True si la creó.
    """
    actual = git.rama_actual(repo)
    if actual == config.rama:
        return False
    if not git.existe_rama_local(repo, config.rama) and not git.archivos_sin_commitear(repo):
        crear_rama_del_ticket(repo, config)
        return True
    raise ErrorGit(
        f"El repo está en '{actual or 'HEAD suelto'}': cámbiate a la rama del ticket "
        f"(git switch {config.rama}) para retomar."
    )


def _retomar_sesion(prep: _Preparacion, sesion: Sesion, reporte: _Reporte) -> None:
    """Prepara la conversación guardada y sigue escribiendo en la misma sesión."""
    motivo = consola.describir_corte(sesion.motivo_corte)
    if sesion.estado == ESTADO_ENTREGA:
        motivo = "solo falta la entrega (push, PR o comentario en Jira)"
    cambiados = _cambios_desde_el_corte(prep, sesion, reporte)
    consola.mostrar_retomando(sesion.clave, motivo, sesion.pasos)
    reporte.registro.registrar_resume(sesion.estado, sesion.motivo_corte, cambiados)
    if sesion.estado == ESTADO_IMPLEMENTACION:
        prep.reanudacion = armar_reanudacion(sesion, motivo, cambiados)
    elif sesion.estado == ESTADO_PLAN:
        # Cortada en la ronda de plan: no escribió nada en el repo, se planifica de nuevo.
        sesion.mensajes, sesion.pasos = [], 0
    actual = identificador(prep.config)
    if sesion.modelos_usados[-1:] != [actual]:
        sesion.modelos_usados.append(actual)
    reporte.modelos_usados = list(sesion.modelos_usados)
    # Los pasos siguen la numeración de antes del corte (también en el aviso del respaldo).
    reporte.ultimo_paso = sesion.pasos
    sesion.proveedor = actual
    sesion.motivo_corte = sesion.detalle_corte = None
    prep.sesion = SesionEnCurso(prep.repo, sesion, prep.secretos, reporte.aviso)
    reporte.sesion = prep.sesion
    prep.sesion.guardar()


def _cambios_desde_el_corte(prep: _Preparacion, sesion: Sesion, reporte: _Reporte) -> list[str]:
    """Archivos que alguien tocó después del corte (se avisa y el modelo lo sabrá)."""
    head_al_cortar = (sesion.rama or {}).get("head_al_cortar")
    if head_al_cortar and cabeza(prep.repo) != head_al_cortar:
        reporte.aviso("La rama tiene commits nuevos (o distintos) desde el corte.")
    if not sesion.repo_al_cortar:
        return []
    cambios = comparar(sesion.repo_al_cortar, tomar_instantanea(prep.repo))
    cambiados = [*cambios.creados, *cambios.modificados, *cambios.eliminados]
    if cambiados:
        reporte.aviso(
            f"Desde el corte cambiaron {', '.join(cambiados)}: el modelo lo sabrá al retomar."
        )
    return cambiados


def _revisar_sesion_pendiente(prep: _Preparacion, desde_cero: bool) -> None:
    """Con una sesión sin terminar, run se detiene y sugiere resume; --desde-cero la descarta."""
    try:
        sesion = cargar_sesion(prep.repo, prep.ticket)
    except ErrorSesion as error:
        if not desde_cero:
            consola.mostrar_error(f"{error} Usa --desde-cero para descartarla.")
            raise typer.Exit(1) from error
        _descartar_sesion(prep)
        return
    if sesion is None or sesion.es_completada:
        return
    if not desde_cero:
        consola.mostrar_error(
            f"Hay una ejecución sin terminar de {prep.ticket} "
            f"({consola.describir_corte(sesion.motivo_corte)}): retómala con "
            f"'harness resume {prep.ticket}', o empieza de nuevo con --desde-cero."
        )
        raise typer.Exit(1)
    _descartar_sesion(prep)


def _descartar_sesion(prep: _Preparacion) -> None:
    destino = descartar_sesion(prep.repo, prep.ticket)
    consola.mostrar_aviso(
        f"Sesión anterior de {prep.ticket} descartada (quedó en {destino.name}). Si la rama del "
        "ticket ya existe, bórrala a mano para empezar limpio."
    )


def _iniciar_sesion(
    prep: _Preparacion, config: ConfigEntrega | None, reporte: _Reporte
) -> None:
    """Después del preflight y la rama: desde aquí, un corte se puede retomar."""
    ticket = prep.datos_ticket
    rama = None
    if config is not None:
        rama = {
            "nombre": config.rama, "base": config.base, "remoto": config.remoto,
            "es_con_push": config.es_con_push, "es_con_pr": config.es_con_pr,
            "head_al_cortar": None,
        }
    sesion = Sesion(
        ticket={"clave": ticket.clave, "titulo": ticket.titulo, "fuente": ticket.fuente,
                "url": ticket.url, "texto": prep.texto_ticket},
        estado=ESTADO_PLAN if prep.es_con_plan else ESTADO_IMPLEMENTACION,
        proveedor=identificador(prep.config),
        modelos_usados=list(reporte.modelos_usados),
        es_con_plan=prep.es_con_plan,
        rama=rama,
    )
    prep.sesion = SesionEnCurso(prep.repo, sesion, prep.secretos, reporte.aviso)
    reporte.sesion = prep.sesion
    prep.sesion.guardar()


def _cortar_sesion(prep: _Preparacion, motivo: str, detalle: str | None = None) -> None:
    if prep.sesion:
        prep.sesion.cortar(motivo, detalle)


def _terminar_ejecucion_en_sesion(
    prep: _Preparacion, resultado: ResultadoEjecucion, config: ConfigEntrega | None
) -> None:
    """Cómo terminó el bucle: cortado (se puede retomar), listo para entregar, o completo."""
    if prep.sesion is None:
        return
    datos = prep.sesion.sesion
    datos.pasos = resultado.pasos
    datos.tokens_entrada = resultado.tokens_entrada
    datos.tokens_salida = resultado.tokens_salida
    datos.texto_final = resultado.texto_final
    datos.resumen_tests = resultado.resumen_tests
    datos.pasos_extra = resultado.pasos_extra
    datos.revision = asdict(resultado.revision)
    datos.alcance = asdict(resultado.alcance)
    # Con plan o con validaciones, la ronda de plan ya pasó: un corte se retoma implementando.
    if datos.estado == ESTADO_PLAN and (resultado.plan is not None or resultado.validaciones):
        datos.estado = ESTADO_IMPLEMENTACION
        datos.plan = datos.plan or (resultado.plan.model_dump() if resultado.plan else None)
    if resultado.es_plan_rechazado:
        prep.sesion.completar()
    elif resultado.es_limite_alcanzado:
        prep.sesion.cortar(MOTIVO_LIMITE_PASOS)
    elif resultado.es_validado is False:
        prep.sesion.cortar(MOTIVO_VALIDACION)
    elif config is None:
        prep.sesion.completar()
    else:
        datos.estado = ESTADO_ENTREGA
        prep.sesion.guardar()


def _sugerir_resume(prep: _Preparacion) -> None:
    if prep.sesion and not prep.sesion.sesion.es_completada:
        consola.mostrar_como_retomar(prep.ticket)


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
        consola.mostrar_jira_rest(motivo_sin_servidor(VAR_SERVIDOR_JIRA, "Jira"))
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
            consola.mostrar_github_rest(motivo_sin_servidor(VAR_SERVIDOR_GITHUB, "GitHub"))
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
    pendiente: dict | None = None,
) -> bool:
    """Commit, push y PR; devuelve False si el PR falló (el push sí se hizo).

    Con `pendiente` (harness resume, el commit ya está) se hacen solo las etapas que faltan.
    """
    head_antes = cabeza(prep.repo)
    try:
        entrega = _hacer_entrega(prep, config, resultado, cambios, reporte, pendiente)
    except ErrorGit as error:
        reporte.registro.registrar_error(str(error))
        consola.mostrar_error(str(error))
        _cortar_en_la_entrega(prep, head_antes, str(error))
        raise typer.Exit(1) from error
    reporte.registro.registrar_entrega(entrega)
    consola.mostrar_entrega(entrega, config)
    _anotar_entrega(prep, entrega)
    # Solo con un PR (creado o que ya existía) y un ticket de Jira. Si el comentario falla,
    # solo se avisa: no cambia el código de salida.
    if entrega.pull_request is not None and prep.config_jira is not None:
        _comentar_en_jira(prep, entrega.pull_request, reporte)
    _cerrar_entrega_en_sesion(prep, config)
    return entrega.error_pr is None


def _hacer_entrega(
    prep: _Preparacion,
    config: ConfigEntrega,
    resultado: ResultadoEjecucion,
    cambios: CambiosRepo,
    reporte: _Reporte,
    pendiente: dict | None,
) -> ResultadoEntrega:
    mensaje = crear_mensaje_commit(prep.ticket, prep.titulo)

    def armar_pedido_pr(repo_github: RepoGitHub) -> PedidoPullRequest:
        comandos = prep.config_proyecto.comandos
        modelo = ", después ".join(reporte.modelos_usados)
        # El resumen lo escribió el modelo: podría repetir un secreto que vio en una salida.
        notas = notas_nuevas(prep.repo, config.base)
        cuerpo = ocultar_secretos(
            crear_cuerpo_pr(resultado, cambios, comandos, modelo, notas), prep.secretos
        )
        return PedidoPullRequest(repo_github, config.rama, config.base, mensaje, cuerpo)

    crear_pr = _crear_pr_por_mcp(prep, reporte) if prep.sesion_github else None
    if pendiente is None:
        return entregar(
            prep.repo, config, resultado, mensaje, armar_pedido_pr, prep.token_github, crear_pr
        )
    if pendiente.get("url_pr"):
        # El PR ya existe: solo falta el comentario en Jira.
        pull_request = PullRequest(pendiente["url_pr"], bool(pendiente.get("es_borrador")))
        return ResultadoEntrega(
            config.rama, hash_commit=pendiente["hash_commit"], es_pusheado=True,
            pull_request=pull_request,
        )
    return continuar_entrega(
        prep.repo, config, pendiente["hash_commit"], bool(pendiente.get("es_pusheado")),
        armar_pedido_pr, prep.token_github, crear_pr,
    )


def _cortar_en_la_entrega(prep: _Preparacion, head_antes: str | None, detalle: str) -> None:
    """Un error de Git a mitad de la entrega: si el commit llegó a hacerse, queda anotado."""
    if prep.sesion is None:
        return
    head_despues = cabeza(prep.repo)
    if head_despues and head_despues != head_antes:
        prep.sesion.sesion.entrega = {
            "hash_commit": head_despues, "es_pusheado": False, "url_pr": None,
            "es_borrador": None, "es_comentado_jira": False, "motivo": None,
        }
    prep.sesion.cortar(MOTIVO_ERROR, detalle)
    _sugerir_resume(prep)


def _retomar_entrega(
    prep: _Preparacion, sesion: Sesion, config: ConfigEntrega | None, reporte: _Reporte
) -> tuple[ResultadoEjecucion, bool]:
    """resume sin modelo ni Docker: solo las etapas de la entrega que faltan."""
    resultado = ResultadoEjecucion(
        sesion.texto_final, sesion.pasos, sesion.tokens_entrada, sesion.tokens_salida, False,
        True, sesion.validaciones, Plan.model_validate(sesion.plan) if sesion.plan else None,
        resumen_tests=sesion.resumen_tests, revision=estado_revision(sesion),
        alcance=estado_alcance(sesion), pasos_extra=sesion.pasos_extra,
    )
    if config is None:
        prep.sesion.completar()
        return resultado, True
    # Después del commit, el repo es el resultado del ticket: con la foto del principio
    # salen los mismos cambios que vio la primera parte.
    cambios = (
        comparar(sesion.repo_al_empezar, tomar_instantanea(prep.repo))
        if sesion.repo_al_empezar else CambiosRepo()
    )
    pendiente = dict(sesion.entrega) if sesion.entrega.get("hash_commit") else None
    return resultado, _entregar(prep, config, resultado, cambios, reporte, pendiente)


def _comentar_en_jira(prep: _Preparacion, pull_request: PullRequest, reporte: _Reporte) -> None:
    """El link del PR en el ticket de Jira, por MCP o por la API REST, sin repetirlo."""
    resultado = comentar_pr_en_jira(
        prep.config_jira, prep.ticket, pull_request, prep.comentarios_jira, reporte.aviso
    )
    if prep.sesion and resultado.estado in (COMENTADO, YA_ESTABA):
        prep.sesion.sesion.entrega["es_comentado_jira"] = True
    reporte.registro.registrar_comentario_jira(prep.ticket, resultado)
    consola.mostrar_comentario_jira(
        prep.ticket, prep.datos_ticket.url or "", pull_request.url, resultado
    )


def _anotar_entrega(prep: _Preparacion, entrega: ResultadoEntrega) -> None:
    """Commit, push y PR en la sesión, para que resume sepa qué etapa falta."""
    if prep.sesion is None or prep.sesion.sesion.estado != ESTADO_ENTREGA:
        return
    pull_request = entrega.pull_request
    prep.sesion.sesion.entrega = {
        "hash_commit": entrega.hash_commit,
        "es_pusheado": entrega.es_pusheado,
        "url_pr": pull_request.url if pull_request else None,
        "es_borrador": pull_request.es_borrador if pull_request else None,
        "es_comentado_jira": False,
        "motivo": entrega.motivo,
    }
    prep.sesion.guardar()


def _cerrar_entrega_en_sesion(prep: _Preparacion, config: ConfigEntrega) -> None:
    """Completa la sesión si no falta ninguna etapa; si falta alguna, queda para resume."""
    if prep.sesion is None or prep.sesion.sesion.estado != ESTADO_ENTREGA:
        return
    entrega = prep.sesion.sesion.entrega
    sin_commit = entrega.get("hash_commit") is None
    falta_push = config.es_con_push and not entrega.get("es_pusheado")
    falta_pr = config.es_con_pr and not entrega.get("url_pr")
    falta_comentario = (
        config.es_con_pr and prep.config_jira is not None and not entrega.get("es_comentado_jira")
    )
    # Sin commit porque no había nada que commitear: no queda nada por hacer.
    if sin_commit or not (falta_push or falta_pr or falta_comentario):
        prep.sesion.completar()
    else:
        prep.sesion.guardar()


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


def _preparar_ejecucion(
    ticket: str,
    repo: Path,
    guardado: Ticket | None = None,
    proveedor: str | None = None,
    modelo: str | None = None,
) -> _Preparacion:
    """Valida repo, ticket, configuración y harness.yaml antes de gastar pedidos al modelo.

    Un ticket de Jira se lee aquí, antes de todo lo de Git: sin ticket no hay nada que hacer,
    y así un error nunca deja una rama creada. Con `guardado` (harness resume) no se lee:
    el texto completo ya está en la conversación de la sesión.
    """
    if not repo.is_dir():
        consola.mostrar_error(f"No existe la carpeta del repo: {repo}")
        raise typer.Exit(1)
    try:
        # cargar_config carga el .env: después ya se pueden resolver los ${NOMBRE}.
        config = elegir_modelo(cargar_config(), proveedor, modelo)
        respaldo = leer_respaldo(config)
        config_proyecto = cargar_config_proyecto(repo)
        variables = resolver_variables(config_proyecto) if config_proyecto else None
        servidores = cargar_config_mcp(RUTA_MCP)
        servidor_github = elegir_servidor_github(servidores)
        servidor_jira = elegir_servidor_jira(servidores)
        datos_ticket, config_jira = _ticket_y_jira(ticket, guardado)
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
    _avisar_secretos_cortos([config, *respaldo], variables, servidor_github, servidor_jira)
    contexto = cargar_agents_md(repo)
    memoria = cargar_memoria(repo)
    # Para ejecuciones sin nadie mirando la consola: el aviso de "falta AGENTS.md" no alcanza.
    if config_proyecto and config_proyecto.requiere_agents_md and not contexto.tiene_contenido:
        consola.mostrar_error(MENSAJE_REQUIERE_AGENTS)
        raise typer.Exit(1)
    return _Preparacion(
        repo, datos_ticket, config, config_proyecto,
        variables or VariablesResueltas(), contexto, token_github=leer_token_github(),
        servidor_github=servidor_github, config_jira=config_jira, servidor_jira=servidor_jira,
        respaldo=respaldo, memoria=memoria,
    )


def _ticket_y_jira(
    ticket: str, guardado: Ticket | None
) -> tuple[Ticket, ConfigJira | None]:
    if guardado is None:
        return _obtener_ticket(ticket)
    # El comentario con el link del PR necesita Jira aunque el ticket ya no se lea.
    config_jira = leer_config_jira() if guardado.fuente == FUENTE_JIRA else None
    return guardado, config_jira


def _avisar_secretos_cortos(
    configs: list[ConfigModelo],
    variables: VariablesResueltas | None,
    servidor_github: ServidorMCP | None,
    servidor_jira: ServidorMCP | None,
) -> None:
    """Los secretos de menos de MIN_CARACTERES_SECRETO no se ocultan: se avisa una vez."""
    claves = [config.variable_api_key or "" for config in configs]
    tokens = [*dict.fromkeys(claves), VAR_TOKEN_GITHUB, VAR_JIRA_TOKEN]
    for servidor in (servidor_github, servidor_jira):
        tokens += list(servidor.variables_secretas) if servidor else []
    avisos = avisos_secretos_cortos(tokens)
    avisos += avisos_variables_cortas_yaml(variables.nombres_secretos if variables else [])
    for aviso in avisos:
        consola.mostrar_aviso(aviso)


def _obtener_ticket(
    texto: str, con_indicador: bool = True
) -> tuple[Ticket, ConfigJira | None]:
    """El ticket de un .md local o de Jira (y la configuración de Jira, para comentar después).

    De Jira se lee siempre por la API REST: su ADF pasa por nuestro conversor sin perder nada.
    Sin indicador cuando lo que sigue es imprimir el ticket entero (harness ticket).
    Lanza ErrorTicket, ErrorConfiguracion o ErrorJira.
    """
    origen = resolver_origen(texto)
    if origen.ruta is not None:
        return crear_ticket_local(origen.ruta), None
    config_jira = leer_config_jira()
    mensaje = f"Leyendo {origen.clave} de Jira..."
    with consola.estado(mensaje) if con_indicador else nullcontext():
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
    limites = prep.config_proyecto.limites if prep.config_proyecto else ConfigLimites()
    proveedor = ProveedorQueCompacta(
        crear_proveedor(prep.config, reporte.reintento, prep.respaldo, reporte.cambio_proveedor),
        limites.max_caracteres_conversacion,
        reporte.compactacion,
    )
    if prep.config_proyecto is None:
        antes = _repo_al_empezar(prep)
        mensajes = _mensajes_iniciales(prep, hay_validacion=False)
        _seguir_en_sesion(prep, mensajes, antes)
        resultado = ejecutar_ticket(
            proveedor,
            mensajes,
            crear_herramientas(prep.repo, extras=_herramientas_extra(prep, reporte)),
            max_pasos,
            al_paso=reporte.paso,
            al_aviso=reporte.aviso,
            planificacion=_planificacion(prep, reporte),
            continuacion=_continuacion(prep),
            revisar_paso=DetectorBucles(prep.repo).revisar,
        )
        return resultado, comparar(antes, tomar_instantanea(prep.repo))
    # El with garantiza que los contenedores se borran aunque algo falle o haya Ctrl+C.
    entorno_docker = EntornoDocker(
        prep.repo,
        prep.config_proyecto,
        prep.ticket,
        prep.variables,
        usar_cache=prep.es_con_cache,
        al_cache=reporte.cache,
        al_instalar=reporte.instalacion,
    )
    with entorno_docker as entorno:
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
    antes = _repo_al_empezar(prep)
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
    mensajes = _mensajes_iniciales(prep, hay_validacion=True)
    _seguir_en_sesion(prep, mensajes, antes)
    resultado = ejecutar_ticket(
        proveedor,
        mensajes,
        crear_herramientas(
            prep.repo,
            config_proyecto,
            ejecutor,
            entorno.marcar_cambio,
            _herramientas_extra(prep, reporte),
        ),
        max_pasos,
        validar=partial(validar_proyecto, ejecutor, comandos.tests, comandos.lint),
        max_intentos_validacion=limites.max_intentos_validacion,
        al_paso=reporte.paso,
        al_validar=reporte.validacion,
        al_aviso=reporte.aviso,
        planificacion=_planificacion(prep, reporte),
        continuacion=_continuacion(prep),
        revisar_paso=DetectorBucles(prep.repo).revisar,
        revision=_revision(prep, config_proyecto, antes, reporte),
        alcance=Alcance(
            prep.repo, antes, crear_herramientas_lectura(prep.repo), entorno.marcar_cambio,
            reporte.fuera_del_plan,
        ),
    )
    return resultado, comparar(antes, tomar_instantanea(prep.repo))


def _revision(
    prep: _Preparacion, config_proyecto: ConfigProyecto, antes: Instantanea, reporte: _Reporte
) -> Revision | None:
    """El paso de revisión, si harness.yaml lo tiene activo y no se pidió --sin-revision."""
    configuracion = config_proyecto.revision
    if not configuracion.activa or not prep.es_con_revision:
        return None

    def obtener_diff() -> str:
        cambios = comparar(antes, tomar_instantanea(prep.repo))
        return armar_diff(prep.repo, cambios, prep.secretos)

    return Revision(
        crear_herramientas_lectura(prep.repo), obtener_diff, configuracion.max_vueltas,
        configuracion.max_pasos, reporte.revision,
    )


def _mensajes_iniciales(prep: _Preparacion, hay_validacion: bool) -> list[Mensaje]:
    """La conversación inicial; al retomar, el system prompt de hoy y lo guardado."""
    mensajes = crear_mensajes_iniciales(
        prep.texto_ticket,
        prep.contexto,
        hay_validacion=hay_validacion,
        hay_github_mcp=bool(prep.herramientas_github),
        memoria=prep.memoria,
    )
    if prep.reanudacion is None:
        return mensajes
    # El system prompt se regenera: AGENTS.md, la memoria o el propio prompt pueden haber
    # cambiado desde el corte.
    return [mensajes[0], *prep.reanudacion.mensajes]


def _herramientas_extra(prep: _Preparacion, reporte: _Reporte) -> list[Herramienta]:
    """Las de GitHub por MCP y guardar_memoria (solo en la implementación, no en el plan)."""
    memoria = crear_herramienta_memoria(
        prep.repo, prep.ticket, prep.secretos, partial(reporte.nota_guardada, prep.repo)
    )
    return [*prep.herramientas_github, memoria]


def _repo_al_empezar(prep: _Preparacion) -> Instantanea:
    # Al retomar, la foto de la primera parte: los cambios del PR son los del ticket entero.
    if prep.reanudacion is not None:
        return prep.reanudacion.repo_al_empezar
    return tomar_instantanea(prep.repo)


def _continuacion(prep: _Preparacion) -> ResultadoEjecucion | None:
    return prep.reanudacion.continuacion if prep.reanudacion else None


def _seguir_en_sesion(prep: _Preparacion, mensajes: list[Mensaje], antes: Instantanea) -> None:
    """La sesión guarda la conversación viva (el bucle la va agregando) desde el principio."""
    if prep.sesion:
        prep.sesion.usar_mensajes(mensajes, antes)


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
    # Al retomar, el plan ya está aprobado (o se cortó en el plan y se rehace sin reanudación).
    if not prep.es_con_plan or prep.reanudacion is not None:
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
    sin_cache: Annotated[
        bool,
        typer.Option("--sin-cache", help=f"Con --verificar. {AYUDA_SIN_CACHE}"),
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
    agregadas = asegurar_gitignore(raiz)
    if agregadas:
        consola.mostrar_gitignore(agregadas)
    if verificar:
        _verificar_init(raiz, usar_cache=not sin_cache)


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


def _verificar_init(repo: Path, usar_cache: bool) -> None:
    try:
        # Las referencias ${NOMBRE} de harness.yaml se resuelven con el .env.
        cargar_env()
        config_proyecto = cargar_config_proyecto(repo)
        if config_proyecto is None:
            raise ErrorConfigProyecto("El repo no tiene harness.yaml: no hay nada que verificar.")
        variables = resolver_variables(config_proyecto)
        with consola.estado("Verificando harness.yaml...") as estado:
            # Sin log: init --verificar solo muestra la caché y el tiempo de instalación.
            resultado = verificar_config(
                repo,
                config_proyecto,
                variables,
                partial(consola.actualizar_estado, estado),
                usar_cache=usar_cache,
                al_cache=consola.mostrar_cache,
                al_instalar=consola.mostrar_instalacion,
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
        datos, _ = _obtener_ticket(ticket, con_indicador=False)
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
    # El .env se carga antes de lo habitual solo para que HARNESS_CONSOLA_CRUDA pueda estar ahí.
    cargar_env()
    ruta_cruda = os.environ.get(VAR_CONSOLA_CRUDA, "").strip()
    if ruta_cruda:
        consola.grabar_salida_cruda(Path(ruta_cruda))
    app()
