"""Salida por consola de una ejecución, con rich."""

import atexit
import sys
from pathlib import Path
from typing import Any, ClassVar, TextIO

from rich.console import Console, ConsoleDimensions, Group
from rich.markup import escape
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from harness.bucle import Paso
from harness.cambios import CambiosRepo
from harness.comentario_pr import COMENTADO, POR_MCP, SIN_URL, YA_ESTABA, ResultadoComentario
from harness.compactacion import MODO_RESUMEN, Compactacion
from harness.contexto import MAX_CARACTERES_AGENTS, ContextoProyecto
from harness.ejecucion import ResultadoEjecucion, Validacion
from harness.cache_dependencias import MOTIVO_DESACTIVADA, EstadoCache
from harness.entorno_docker import MOTIVO_SIN_CACHE, Conservados, Instalacion, ResultadoComando
from harness.entrega import ConfigEntrega, ResultadoEntrega
from harness.alcance import EstadoAlcance
from harness.github import PullRequest
from harness.revision import EstadoRevision
from harness.init import ResultadoInit
from harness.limpieza import IMAGEN as TIPO_IMAGEN
from harness.limpieza import Elemento, PlanLimpieza, ResultadoLimpieza
from harness.memoria import MAX_CARACTERES_MEMORIA, RUTA_MEMORIA, Memoria
from harness.plan import Plan, archivos_fuera_del_plan
from harness.proveedores.base import MOTIVO_CAIDO, MOTIVO_CUOTA_DIARIA, MOTIVO_LIMITE
from harness.proveedores.respaldo import CambioProveedor
from harness.sesion import (
    MOTIVO_BUCLE,
    MOTIVO_CTRL_C,
    MOTIVO_ERROR,
    MOTIVO_LIMITE_PASOS,
    MOTIVO_PROVEEDOR,
    MOTIVO_VALIDACION,
)
from harness.ticket import FUENTE_LOCAL, Ticket

MAX_CARACTERES_RESUMEN = 120
AVISO_FIN_SIN_AGENTS = (
    "El repo no tiene AGENTS.md: el modelo trabajó sin las convenciones del proyecto. "
    "Créalo con 'harness init'."
)
MAX_CARACTERES_ARGUMENTO = 60
MAX_CARACTERES_RUTA = 60
ELIPSIS = "…"
LINEAS_INFORME_VALIDACION = 8
SIMBOLOS_ACCION = {"crear": "[green]+[/]", "modificar": "[yellow]~[/]", "eliminar": "[red]-[/]"}
UNIDADES_TAMANO = (("GB", 10**9), ("MB", 10**6), ("kB", 10**3))
# Hasta 10 de una unidad se muestra un decimal (1.2 GB); desde ahí, sin decimales (81 MB).
DECIMALES_HASTA = 10
RESPUESTAS_SI = ("s", "si", "sí")
RESPUESTAS_NO = ("n", "no")
PREGUNTA_PLAN = "¿Aprobar el plan? [s/n, o escribe qué cambiar] "
LINEAS_VERIFICACION = 15
# Los mismos que usa console.status de Rich.
SPINNER_INDICADOR = "dots"
REFRESCOS_POR_SEGUNDO_INDICADOR = 12.5
SEGUNDOS_POR_MINUTO = 60
PREFIJO_AVISO = "Aviso: "
TEXTOS_MOTIVO_CAMBIO = {
    MOTIVO_CAIDO: "no responde después de los reintentos",
    MOTIVO_LIMITE: "sigue con el límite de pedidos (429) después de los reintentos",
    MOTIVO_CUOTA_DIARIA: "agotó su cuota diaria",
}
TEXTOS_CORTE = {
    MOTIVO_LIMITE_PASOS: "se cortó por el límite de pasos",
    MOTIVO_VALIDACION: "los tests no pasaron",
    MOTIVO_CTRL_C: "se canceló con Ctrl+C",
    MOTIVO_PROVEEDOR: "el proveedor del modelo no respondió",
    MOTIVO_ERROR: "terminó con un error",
    MOTIVO_BUCLE: "se cortó porque el modelo repetía lo mismo",
}
# Sin caché porque lo pidió el usuario: no hace falta avisar en amarillo.
MOTIVOS_SIN_CACHE_ELEGIDOS = (MOTIVO_SIN_CACHE, MOTIVO_DESACTIVADA)


class ConsolaSinUltimaColumna(Console):
    """Consola que nunca escribe en la última columna de la terminal.

    En algunas terminales (la consola clásica de Windows, entre otras), una línea que llega a
    la última columna salta sola al renglón siguiente y el salto de línea agrega otro: el texto
    queda corrido o pisa una línea. Como width y options salen de size, alcanza con achicarlo
    una vez para que los paneles, el ajuste de líneas y cualquier salida a todo el ancho
    respeten el margen. (En modo legacy de Windows Rich a veces ya resta una columna: perder
    otra no molesta.)
    """

    @property
    def size(self) -> ConsoleDimensions:
        real = super().size
        return ConsoleDimensions(max(real.width - 1, 1), real.height)

    @size.setter
    def size(self, nuevo: tuple[int, int]) -> None:
        Console.size.fset(self, nuevo)


consola = ConsolaSinUltimaColumna()


class SalidaConCopia:
    """Escribe en la terminal y copia lo mismo, byte a byte, en un archivo.

    Para comparar la salida cruda (con los códigos ANSI de Rich) con lo que muestra la
    pantalla cuando falta una línea. La terminal sigue decidiendo si es una TTY y su fileno:
    Rich los usa para elegir cómo dibujar.
    """

    def __init__(self, terminal: TextIO, copia: TextIO) -> None:
        self._terminal = terminal
        self._copia = copia

    @property
    def encoding(self) -> str:
        return getattr(self._terminal, "encoding", None) or "utf-8"

    def write(self, texto: str) -> int:
        self._copia.write(texto)
        # Sin búfer: si la ejecución se corta, la copia tiene todo lo que salió en pantalla.
        self._copia.flush()
        return self._terminal.write(texto)

    def flush(self) -> None:
        self._copia.flush()
        self._terminal.flush()

    def isatty(self) -> bool:
        return self._terminal.isatty()

    def fileno(self) -> int:
        return self._terminal.fileno()


def grabar_salida_cruda(ruta: Path) -> None:
    """Desde ahora, todo lo que se escribe en la consola se copia también en `ruta`."""
    # newline="": sin convertir los saltos de línea (en Windows, a \r\n), para que la copia
    # sea exactamente lo que se envió.
    copia = ruta.open("w", encoding="utf-8", newline="")
    atexit.register(copia.close)
    consola.file = SalidaConCopia(consola.file, copia)


def forzar_salida_utf8() -> None:
    """Pasa stdout y stderr a UTF-8.

    En Windows, si la salida va a un archivo o a una tubería, Python usa la página de códigos
    del sistema (cp1252), que no tiene caracteres como '→' o '✓': el primer print fallaría
    con UnicodeEncodeError y cortaría la ejecución.
    """
    for flujo in (sys.stdout, sys.stderr):
        reconfigurar = getattr(flujo, "reconfigure", None)
        if reconfigurar:
            reconfigurar(encoding="utf-8", errors="replace")


def mostrar_inicio(
    ticket: str, repo: str, proveedor: str, modelo: str, respaldo: list[str] | None = None
) -> None:
    """Muestra con qué datos arranca la ejecución."""
    texto_respaldo = f"  [dim](respaldo: {escape(', '.join(respaldo))})[/]" if respaldo else ""
    consola.print(
        f"[bold green]Ticket[/] {escape(ticket)}  [bold green]Repo[/] {escape(repo)}\n"
        f"[bold green]Modelo[/] {escape(proveedor)} / {escape(modelo)}{texto_respaldo}"
    )


def describir_corte(motivo: str | None) -> str:
    """Por qué quedó a medias una ejecución, en palabras."""
    return TEXTOS_CORTE.get(motivo or "", "quedó a medias")


def mostrar_retomando(clave: str, motivo: str, pasos: int) -> None:
    """Al empezar harness resume: qué se retoma y desde dónde."""
    consola.print(
        f"[bold green]Se retoma[/] {escape(clave)}: {escape(motivo)}, después de {pasos} "
        f"paso{'s' if pasos != 1 else ''}."
    )


def mostrar_rama_retomada(config: ConfigEntrega, es_recreada: bool = False) -> None:
    """La rama de la ejecución que se retoma, o que se creó de nuevo porque se había borrado."""
    if es_recreada:
        consola.print(
            f"[dim]La rama {escape(config.rama)} se había borrado al cortar (quedó sin cambios): "
            f"se creó de nuevo desde {escape(config.base)}.[/]"
        )
        return
    consola.print(f"[dim]Se sigue en la rama {escape(config.rama)}.[/]")


def mostrar_memoria(memoria: Memoria) -> None:
    """Si el repo tiene memoria del proyecto, cuántas notas trae."""
    if memoria.texto is None:
        return
    notas = memoria.notas
    plural = "s" if notas != 1 else ""
    consola.print(f"[dim]Memoria del proyecto cargada ({notas} nota{plural}).[/]")
    if memoria.es_recortada:
        mostrar_aviso(
            f"{RUTA_MEMORIA} tiene {memoria.caracteres_originales} caracteres: el modelo "
            f"recibe los primeros {MAX_CARACTERES_MEMORIA}. Conviene depurarla."
        )


def mostrar_nota_guardada(nota: str) -> None:
    """La nota que el modelo agregó a la memoria del proyecto."""
    consola.print(f"[dim]Memoria: nota guardada en {RUTA_MEMORIA}: {escape(nota)}[/]")


def mostrar_recomendacion_arquitectura(stack: str, herramienta: str) -> None:
    """Cómo hacer cumplir las reglas de arquitectura de AGENTS.md en la validación."""
    consola.print(
        f"[dim]Reglas de arquitectura ({escape(stack)}): para que la validación las haga "
        f"cumplir, y no solo el modelo, se recomienda {escape(herramienta)}. Ver "
        "docs/harness-yaml.md, \"Reglas de arquitectura\".[/]"
    )


def mostrar_sugerencia_test_logging(archivo: str, bloque: str) -> None:
    """El build de Gradle no muestra el detalle de las fallas: el bloque para agregarle."""
    mostrar_aviso(
        f"{archivo} no configura testLogging con exceptionFormat FULL: cuando un test falla, "
        "Gradle no muestra el valor esperado ni el obtenido y el modelo corrige a ciegas. "
        f"Se sugiere agregar a {archivo} (el harness no lo modifica):"
    )
    consola.print(Panel(escape(bloque), title=archivo, expand=False))


def mostrar_compactacion(compactacion: Compactacion) -> None:
    """La conversación creció demasiado y se acortaron los resultados viejos."""
    omitidos = compactacion.resultados_omitidos
    que = "se omitió 1 resultado viejo" if omitidos == 1 else (
        f"se omitieron {omitidos} resultados viejos"
    )
    ahorro = f"{compactacion.caracteres_ahorrados:,}".replace(",", ".")
    if compactacion.modo == MODO_RESUMEN:
        cuantos = "1 resultado" if omitidos == 1 else f"{omitidos} resultados"
        consola.print(
            f"[dim]Conversación compactada con un resumen del modelo: {cuantos} "
            f"(−{ahorro} caracteres).[/]"
        )
        return
    consola.print(f"[dim]Conversación compactada: {que} (−{ahorro} caracteres).[/]")


def mostrar_como_retomar(clave: str) -> None:
    """Al final de una ejecución que no terminó: cómo seguir sin empezar de cero."""
    consola.print(f"Para retomarla: [bold]harness resume {escape(clave)}[/]")


def mostrar_cambio_proveedor(cambio: CambioProveedor, paso: int) -> None:
    """El proveedor se cayó y la misma conversación sigue con el siguiente de la lista."""
    que_paso = TEXTOS_MOTIVO_CAMBIO.get(cambio.motivo, "falló")
    mostrar_aviso(
        f"{cambio.anterior} {que_paso}: se sigue con {cambio.siguiente} desde el paso {paso}."
    )


def mostrar_ticket(ticket: Ticket) -> None:
    """De dónde vino el ticket: archivo local, o Jira con su título y su página."""
    if ticket.fuente == FUENTE_LOCAL:
        consola.print(f"[dim]Ticket {escape(ticket.clave)} desde un archivo local.[/]")
        return
    consola.print(
        f"[bold green]Jira[/] {escape(ticket.clave)}: {escape(ticket.titulo)} (leído por la "
        f"API REST) {escape(ticket.url or '')}",
        soft_wrap=True,
    )


def mostrar_texto_ticket(texto: str) -> None:
    """El ticket completo, sin markup ni ajuste de líneas: para copiarlo o compararlo."""
    consola.print(texto, markup=False, highlight=False, soft_wrap=True)


def mostrar_contexto(contexto: ContextoProyecto) -> None:
    """Dice si se cargó AGENTS.md; si falta o se recortó, avisa."""
    if not contexto.existe:
        mostrar_aviso(
            "El repo no tiene AGENTS.md: el modelo explorará más lento. "
            "Créalo con 'harness init'."
        )
        return
    if contexto.es_recortado:
        mostrar_aviso(
            f"AGENTS.md tiene {contexto.caracteres_originales} caracteres: se le pasan al "
            f"modelo los primeros {MAX_CARACTERES_AGENTS}."
        )
        return
    consola.print(f"[dim]AGENTS.md cargado ({contexto.caracteres_originales} caracteres).[/]")


def mostrar_paso(paso: Paso) -> None:
    """Muestra la herramienta llamada y un resumen de su resultado.

    Si el paso tiene varias llamadas, la primera imprime la cabecera "[N] K herramientas" y
    cada una va en su línea con "·", para no repetir el número de paso.
    """
    argumentos = ", ".join(
        f"{nombre}={_resumir_argumento(nombre, valor)}"
        for nombre, valor in paso.llamada.argumentos.items()
    )
    llamada = f"[bold]{escape(paso.llamada.nombre)}[/]({escape(argumentos)})"
    color = "red" if paso.es_error else "dim"
    resultado = f"[{color}]→ {escape(_resumir_resultado(paso.resultado))}[/]"
    if paso.total == 1:
        consola.print(f"[cyan][{paso.numero}][/] {llamada}")
        consola.print(f"    {resultado}")
        return
    if paso.indice == 1:
        consola.print(f"[cyan][{paso.numero}][/] {paso.total} herramientas")
    consola.print(f"    · {llamada} {resultado}")


def mostrar_respuesta(numero: int, texto: str) -> None:
    """Una respuesta del modelo sin herramientas (la final de la implementación, por ejemplo):
    cuenta como paso, así que se muestra con su número."""
    resumen = _resumir_resultado(texto) if texto.strip() else "(sin texto)"
    consola.print(f"[cyan][{numero}][/] [bold]Respuesta:[/] [dim]{escape(resumen)}[/]")


def mostrar_validacion(validacion: Validacion) -> None:
    """Muestra si pasaron los tests + lint al terminar una vuelta."""
    if validacion.es_valido:
        resumen = f" ({escape(validacion.resumen_tests)})" if validacion.resumen_tests else ""
        consola.print(f"[bold green]✓ Validación {validacion.intento}: tests OK{resumen}[/]")
        return
    consola.print(f"[bold red]✗ Validación {validacion.intento}: falló[/]")
    consola.print(f"[dim]{escape(_ultimas_lineas(validacion.informe))}[/]")


def mostrar_alcance(estado: EstadoAlcance) -> None:
    """Qué hizo el modelo con los archivos que cambiaron fuera del plan."""
    for ruta, motivo in estado.justificados.items():
        consola.print(f"[dim]Fuera del plan, justificado: {escape(ruta)}: {escape(motivo)}[/]")
    for ruta in estado.revertidos:
        consola.print(f"[dim]Fuera del plan, revertido: {escape(ruta)}[/]")
    if estado.pendientes:
        mostrar_aviso(
            f"Fuera del plan, sin justificar ni revertir: {', '.join(estado.pendientes)}."
        )


def mostrar_revision(estado: EstadoRevision) -> None:
    """Cómo terminó una ronda de revisión: aprobada, con problemas, o sin informe."""
    if estado.es_fallida:
        mostrar_aviso("El modelo no informó la revisión: se sigue sin revisión.")
        return
    if estado.es_aprobada:
        consola.print(f"[bold green]✓ Revisión {estado.vueltas}: sin problemas[/]")
        return
    cantidad = len(estado.problemas)
    consola.print(f"[bold yellow]✗ Revisión {estado.vueltas}: {_problemas(cantidad)}[/]")
    for problema in estado.problemas:
        archivo, regla = escape(problema["archivo"]), escape(problema["regla_o_criterio"])
        consola.print(
            f"  [yellow]{archivo}[/] [dim]({regla}): {escape(problema['descripcion'])}[/]"
        )


def _problemas(cantidad: int) -> str:
    return "1 problema" if cantidad == 1 else f"{cantidad} problemas"


def mostrar_fin(
    resultado: ResultadoEjecucion,
    cambios: CambiosRepo,
    ruta_log: str,
    contexto: ContextoProyecto | None = None,
) -> None:
    """Muestra cómo terminó la ejecución, qué archivos cambió, los tokens y el log.

    Si faltaba AGENTS.md lo repite: el aviso del inicio queda arriba, detrás de la instalación
    y de los pasos.
    """
    if resultado.es_plan_rechazado:
        mostrar_aviso("Plan rechazado: no se implementó nada.")
    elif resultado.es_limite_alcanzado:
        consola.print(
            f"[bold red]Se alcanzó el límite de {resultado.pasos} pasos sin terminar.[/]"
        )
    else:
        consola.print(Panel(escape(resultado.texto_final or "(sin texto)"), title="Resultado"))
    if resultado.es_validado is False:
        consola.print(
            f"[bold red]Los tests no pasan después de {resultado.validaciones} validaciones.[/]"
        )
    revision = resultado.revision
    if revision.problemas and not revision.es_aprobada and resultado.es_validado:
        mostrar_aviso(
            f"La revisión dejó {_problemas(len(revision.problemas))} sin resolver: van en la "
            "descripción del PR para quien lo revise."
        )
    if not resultado.es_plan_rechazado:
        mostrar_cambios(cambios)
        if resultado.plan:
            # Los justificados ya se mostraron con su motivo.
            justificados = resultado.alcance.justificados
            fuera = archivos_fuera_del_plan(resultado.plan, cambios)
            mostrar_fuera_del_plan([ruta for ruta in fuera if ruta not in justificados])
    if contexto is not None and not contexto.tiene_contenido:
        mostrar_aviso(AVISO_FIN_SIN_AGENTS)
    consola.print(
        f"[dim]Pasos: {resultado.pasos} · Tokens: {resultado.tokens_entrada} entrada / "
        f"{resultado.tokens_salida} salida · Log: {escape(ruta_log)}[/]"
    )


def mostrar_github_mcp(servidor: str, transporte: str, herramientas: int, es_pr: bool) -> None:
    """Para qué se usa el servidor MCP de GitHub: herramientas del modelo y/o el PR."""
    usos = []
    if herramientas:
        usos.append(f"{herramientas} herramientas de lectura para el modelo")
    if es_pr:
        usos.append("el PR se crea con create_pull_request")
    if not herramientas and es_pr:
        usos = ["solo para el PR (sin herramientas para el modelo)"]
    consola.print(
        f"[bold green]GitHub por MCP[/] (servidor {escape(servidor)}, {escape(transporte)}): "
        f"{'; '.join(usos)}."
    )


def mostrar_github_mcp_sin_uso() -> None:
    """Hay servidor MCP, pero ni el modelo lo usa (--sin-herramientas-github) ni hay PR."""
    consola.print("[dim]GitHub por MCP: sin herramientas para el modelo y sin PR.[/]")


def mostrar_comentario_jira(
    clave: str, url_ticket: str, url_pr: str, resultado: ResultadoComentario
) -> None:
    """Cómo quedó el comentario con el link del PR en el ticket de Jira."""
    if resultado.estado == COMENTADO:
        camino = "por MCP" if resultado.camino == POR_MCP else "por la API REST"
        consola.print(
            f"[bold green]✓ Comentario[/] en {escape(clave)} ({camino}): {escape(url_ticket)}",
            soft_wrap=True,
        )
    elif resultado.estado == YA_ESTABA:
        mostrar_aviso(f"El ticket {clave} ya tiene el link del PR: no se comenta otra vez.")
    elif resultado.estado == SIN_URL:
        mostrar_aviso(f"No se comenta en {clave}: no se supo la URL exacta del PR.")
    else:
        mostrar_aviso(
            f"No se pudo comentar en {clave}: {resultado.error} Agrégalo a mano: {url_pr}"
        )


def mostrar_jira_mcp(servidor: str, transporte: str) -> None:
    """El link del PR se comenta en Jira por MCP (el ticket se leyó por la API REST)."""
    consola.print(
        f"[bold green]Jira por MCP[/] (servidor {escape(servidor)}, {escape(transporte)}): el "
        "link del PR se comenta por MCP."
    )


def mostrar_jira_rest(motivo: str) -> None:
    """El link del PR se comenta con la API REST de Jira, y por qué no por MCP."""
    consola.print(
        f"[bold green]Jira por la API REST[/]: el link del PR se comenta por REST "
        f"({escape(motivo)})."
    )


def mostrar_github_rest(motivo: str) -> None:
    """El PR se crea con la API REST de GitHub, y por qué no por MCP."""
    consola.print(f"[bold green]GitHub por la API REST[/] ({escape(motivo)}).")


def mostrar_rama(config: ConfigEntrega) -> None:
    """Avisa en qué rama trabaja el modelo."""
    consola.print(f"[dim]Rama {escape(config.rama)} creada desde {escape(config.base)}.[/]")


def mostrar_entrega(entrega: ResultadoEntrega, config: ConfigEntrega) -> None:
    """Commit, push y PR hechos, o por qué no se hicieron."""
    if entrega.hash_commit:
        consola.print(
            f"[bold green]✓ Commit {entrega.hash_commit}[/] en {escape(entrega.rama)} "
            f"({len(entrega.archivos)} archivo(s))."
        )
    if entrega.es_pusheado:
        consola.print(
            f"[bold green]✓ Push[/] de {escape(entrega.rama)} a {escape(config.remoto)}."
        )
    if entrega.pull_request:
        _mostrar_pull_request(entrega.pull_request)
    if entrega.motivo:
        mostrar_aviso(entrega.motivo)
    if entrega.error_pr:
        mostrar_error(f"No se pudo crear el pull request (el push sí se hizo): {entrega.error_pr}")
    # soft_wrap: el link y la ruta no se parten en dos líneas (siguen siendo clicables).
    if entrega.url_pr_a_mano:
        consola.print(f"Créalo a mano: {escape(entrega.url_pr_a_mano)}", soft_wrap=True)
    if entrega.ruta_pr_pendiente:
        consola.print(
            f"Título y descripción para pegar: {escape(str(entrega.ruta_pr_pendiente))}",
            soft_wrap=True,
        )


def _mostrar_pull_request(pr: PullRequest) -> None:
    tipo = "en borrador" if pr.es_borrador else "listo para revisar"
    camino = "por MCP" if pr.por_mcp else "por la API REST"
    # soft_wrap: el link no se parte en dos líneas (sigue siendo clicable).
    if pr.es_url_aproximada:
        consola.print(
            f"[bold green]✓ Pull request[/] {tipo} ({camino}); GitHub no devolvió su URL, "
            f"está entre los PRs de la rama: {escape(pr.url)}",
            soft_wrap=True,
        )
        return
    if pr.es_existente:
        consola.print(
            f"[bold green]✓ Ya había un pull request[/] {tipo} ({camino}): {escape(pr.url)}",
            soft_wrap=True,
        )
        return
    consola.print(
        f"[bold green]✓ Pull request[/] {tipo} ({camino}): {escape(pr.url)}", soft_wrap=True
    )
    if pr.es_borrador_rechazado:
        mostrar_aviso(
            "GitHub no permite PR en borrador en este repo (privado en una cuenta gratuita): "
            "se creó como PR normal."
        )


def mostrar_cambios(cambios: CambiosRepo) -> None:
    """Lista los archivos creados, modificados y eliminados en el repo objetivo."""
    if not cambios.hay_cambios:
        mostrar_aviso("El modelo no modificó ningún archivo.")
        return
    grupos = [
        ("Creados", "green", cambios.creados),
        ("Modificados", "yellow", cambios.modificados),
        ("Eliminados", "red", cambios.eliminados),
        ("Memoria", "cyan", cambios.memoria),
    ]
    for titulo, color, rutas in grupos:
        if rutas:
            consola.print(f"[bold]{titulo}:[/]")
            for ruta in rutas:
                consola.print(f"  [{color}]{escape(ruta)}[/]")


def mostrar_fuera_del_plan(rutas: list[str]) -> None:
    """Avisa de los archivos que cambiaron sin estar en el plan."""
    if not rutas:
        return
    mostrar_aviso("Archivos que cambiaron y no estaban en el plan:")
    for ruta in rutas:
        consola.print(f"  [yellow]{escape(ruta)}[/]")


def mostrar_plan(plan: Plan) -> None:
    """Muestra el plan propuesto por el modelo, armado desde su estructura."""
    lineas = [escape(plan.resumen), "", "[bold]Archivos:[/]"]
    for archivo in plan.archivos:
        simbolo = SIMBOLOS_ACCION[archivo.accion]
        lineas.append(
            f"  {simbolo} {escape(archivo.ruta)} [dim]({archivo.accion}: "
            f"{escape(archivo.motivo)})[/]"
        )
    lineas += ["", "[bold]Pasos:[/]"]
    lineas += [f"  {numero}. {escape(paso)}" for numero, paso in enumerate(plan.pasos, 1)]
    if plan.tests:
        lineas += ["", "[bold]Tests:[/]"]
        lineas += [f"  - {escape(test)}" for test in plan.tests]
    consola.print(Panel("\n".join(lineas), title="Plan"))


def preguntar_plan(tokens_entrada: int, tokens_salida: int) -> bool | str:
    """Pregunta si se aprueba el plan: True (s), False (n, Enter o EOF) o el texto de una
    corrección para planificar de nuevo."""
    consola.print(
        f"[dim]Tokens hasta ahora: {tokens_entrada} entrada / {tokens_salida} salida.[/]"
    )
    try:
        respuesta = consola.input(escape(PREGUNTA_PLAN)).strip()
    except EOFError:
        return False
    if respuesta.lower() in RESPUESTAS_SI:
        return True
    if not respuesta or respuesta.lower() in RESPUESTAS_NO:
        return False
    return respuesta


def es_terminal_interactiva() -> bool:
    """True si hay alguien en la consola para responder una pregunta."""
    return sys.stdin.isatty()


def preguntar_continuar_sin_pr() -> bool:
    """Pregunta si se sigue sin PR; Enter o sin respuesta posible (EOF) es que no."""
    while True:
        try:
            respuesta = consola.input(
                escape("¿Continuar sin PR (solo commit y push)? [s/N] ")
            ).strip().lower()
        except EOFError:
            return False
        if respuesta in RESPUESTAS_SI:
            return True
        if not respuesta or respuesta in RESPUESTAS_NO:
            return False


def preguntar_borrar() -> bool:
    """Pregunta si se borra lo listado por harness limpiar; Enter o EOF es que no."""
    while True:
        try:
            respuesta = consola.input(escape("¿Borrar todo lo de la lista? [s/N] ")).strip().lower()
        except EOFError:
            return False
        if respuesta in RESPUESTAS_SI:
            return True
        if not respuesta or respuesta in RESPUESTAS_NO:
            return False


def formatear_tamano(bytes_: int | None) -> str:
    """'81 MB', '1.2 GB' o '-' si no se sabe (unidades decimales, como docker)."""
    if bytes_ is None:
        return "-"
    for unidad, factor in UNIDADES_TAMANO:
        if bytes_ >= factor:
            valor = bytes_ / factor
            return f"{valor:.1f} {unidad}" if valor < DECIMALES_HASTA else f"{valor:.0f} {unidad}"
    return f"{bytes_} B"


def mostrar_plan_limpieza(plan: PlanLimpieza, con_imagenes: bool) -> None:
    """Lo que harness limpiar va a borrar (con su tamaño y el total) y lo que deja."""
    if plan.a_borrar:
        consola.print("[bold]Se va a borrar:[/]")
        for elemento in plan.a_borrar:
            consola.print(f"  {_linea_elemento(elemento)}")
        total = formatear_tamano(plan.total_bytes)
        consola.print(f"[bold]Total: {len(plan.a_borrar)} elemento(s), {escape(total)}.[/]")
    else:
        consola.print("No hay nada que borrar.")
    if plan.omitidos:
        consola.print("[dim]No se toca:[/]")
        for elemento, motivo in plan.omitidos:
            consola.print(f"[dim]  {_linea_elemento(elemento)}: {escape(motivo)}[/]")
    if con_imagenes and any(e.tipo == TIPO_IMAGEN for e in plan.a_borrar):
        mostrar_aviso(
            "Las imágenes pueden estar compartidas con otros proyectos tuyos: si otro las usa, "
            "Docker las descargará de nuevo la próxima vez."
        )


def _linea_elemento(elemento: Elemento) -> str:
    detalle = f" ({elemento.detalle})" if elemento.detalle else ""
    tamano = formatear_tamano(elemento.bytes)
    return escape(f"{elemento.tipo:<10} {elemento.nombre}{detalle}  {tamano}")


def mostrar_resultado_limpieza(resultado: ResultadoLimpieza) -> None:
    """Cuánto se liberó y qué no se pudo borrar."""
    liberado = formatear_tamano(resultado.bytes_liberados)
    consola.print(
        f"[bold green]✓ Borrado(s) {len(resultado.borrados)} elemento(s)[/] "
        f"({escape(liberado)})."
    )
    for elemento, error in resultado.fallidos:
        mostrar_aviso(f"No se pudo borrar {elemento.tipo} {elemento.nombre}: {error}")


def mostrar_github_verificado(repo: str) -> None:
    """El token pasó la verificación (y lo que no se puede comprobar todavía)."""
    consola.print(
        f"[bold green]✓ Token de GitHub[/] con acceso a {escape(repo)}. El permiso de escritura "
        "en pull requests no se puede comprobar sin crear uno: si falta, el PR fallará al final."
    )


def mostrar_inicio_init(repo: str, proveedor: str, modelo: str, faltantes: list[str]) -> None:
    """Muestra qué va a generar harness init y con qué modelo."""
    consola.print(
        f"[bold green]Init[/] {escape(repo)}  [bold green]Genera[/] {escape(', '.join(faltantes))}"
        f"\n[bold green]Modelo[/] {escape(proveedor)} / {escape(modelo)}"
    )


def mostrar_gitignore(agregadas: list[str]) -> None:
    """Las líneas que init agregó al .gitignore del repo."""
    consola.print(f"[dim]Agregado al .gitignore: {escape(', '.join(agregadas))}.[/]")


def mostrar_fin_init(resultado: ResultadoInit, ruta_log: str) -> None:
    """Muestra los archivos creados y recuerda revisarlos antes de usarlos."""
    if resultado.texto_final:
        consola.print(Panel(escape(resultado.texto_final), title="Resultado"))
    consola.print("[bold]Creados:[/]")
    for nombre in resultado.creados:
        consola.print(f"  [green]{escape(nombre)}[/]")
    mostrar_aviso(
        "Revísalos antes de usarlos, sobre todo docker.instalar y comandos_permitidos de "
        "harness.yaml: definen qué se ejecuta. Puedes probarlos con 'harness init --verificar'."
    )
    consola.print(
        f"[dim]Pasos: {resultado.pasos} · Tokens: {resultado.tokens_entrada} entrada / "
        f"{resultado.tokens_salida} salida · Log: {escape(ruta_log)}[/]"
    )


def mostrar_verificacion(resultado: ResultadoComando, salida: str) -> None:
    """Informa si los tests pasan con el harness.yaml (instalación incluida)."""
    if resultado.codigo == 0 and not resultado.es_tiempo_agotado:
        consola.print("[bold green]✓ Verificación: la instalación y los tests funcionan.[/]")
        return
    consola.print("[bold red]✗ Verificación: los tests no pasan con este harness.yaml.[/]")
    lineas = salida.strip().splitlines()[-LINEAS_VERIFICACION:]
    consola.print(f"[dim]{escape(chr(10).join(lineas))}[/]")


def mostrar_cache(cache: EstadoCache) -> None:
    """Si la ejecución usa la caché de dependencias y con qué volumen, o por qué no."""
    _fijar_indicador()
    if cache.es_usada:
        estado = "nuevo: esta vez se descarga todo" if cache.es_creado else "reutilizado"
        texto = f"Caché de dependencias: volumen {cache.volumen} ({estado})."
        if cache.variables_respetadas:
            texto += f" Se respetan de harness.yaml: {', '.join(cache.variables_respetadas)}."
        consola.print(f"[dim]{escape(texto)}[/]")
    elif cache.motivo in MOTIVOS_SIN_CACHE_ELEGIDOS:
        consola.print(f"[dim]Sin caché de dependencias ({escape(cache.motivo)}).[/]")
    else:
        # No lo eligió el usuario (otra ejecución, un volumen que no se pudo crear): se avisa.
        mostrar_aviso(f"Esta ejecución corre sin caché de dependencias: {cache.motivo}.")


def mostrar_instalacion(instalacion: Instalacion) -> None:
    """Cuánto tardó la instalación de dependencias (si falló, el error ya lo dice)."""
    if not instalacion.es_ok:
        return
    _fijar_indicador()
    duracion = formatear_duracion(instalacion.segundos)
    if instalacion.es_reinstalacion:
        texto = f"Dependencias reinstaladas en {duracion} (cambió {instalacion.archivo_cambiado})."
    else:
        texto = f"Dependencias instaladas en {duracion}."
    consola.print(f"[dim]{escape(texto)}[/]")


def mostrar_conservados(conservados: Conservados) -> None:
    """Con --conservar: cómo entrar a cada contenedor y cómo borrarlos después."""
    if not conservados.hay_algo:
        return
    _fijar_indicador()
    consola.print("[bold]Contenedores conservados (--conservar):[/]")
    if conservados.proyecto:
        consola.print(f"  Proyecto:      docker exec -it {escape(conservados.proyecto)} sh")
    for contenedor, servicio in conservados.servicios:
        consola.print(f"  Servicio {escape(servicio)}: docker exec -it {escape(contenedor)} sh")
    borrar = []
    if conservados.contenedores:
        borrar.append(f"docker rm -f -v {' '.join(conservados.contenedores)}")
    if conservados.red:
        borrar.append(f"docker network rm {conservados.red}")
    consola.print(
        f"Para borrarlos: [bold]harness limpiar[/] (o {escape('; '.join(borrar))})."
    )


def formatear_duracion(segundos: float) -> str:
    """'8 s', '1 min 52 s' o 'menos de 1 s'."""
    total = round(segundos)
    if total < 1:
        return "menos de 1 s"
    minutos, resto = divmod(total, SEGUNDOS_POR_MINUTO)
    return f"{minutos} min {resto} s" if minutos else f"{resto} s"


def mostrar_aviso(mensaje: str) -> None:
    """Muestra un aviso que no corta la ejecución (por ejemplo, un reintento).

    El prefijo lo distingue también sin colores (salida copiada o redirigida a un archivo).
    """
    _fijar_indicador()
    consola.print(f"[yellow]{PREFIJO_AVISO}{escape(mensaje)}[/]")


class Indicador:
    """Indicador animado mientras dura una tarea larga (instalar dependencias, conectar...).

    No es transitorio a propósito: al cerrarse, uno transitorio sube el cursor tantas líneas
    como medía y las borra, y si la terminal no tiene el cursor donde Rich supone, borra una
    línea que no es suya (pasó en Windows Terminal). Este deja su último mensaje escrito, sin
    el spinner, como una línea más, y nunca sube el cursor al cerrar. Fuera de una terminal no
    dibuja nada.
    """

    # El que está en pantalla: un mensaje que se imprime mientras tanto lo fija antes.
    activo: ClassVar["Indicador | None"] = None

    def __init__(self, mensaje: str) -> None:
        self._mensaje = mensaje
        self._es_fijado = False
        self._spinner = Spinner(SPINNER_INDICADOR, text=Text(mensaje))
        self._live: Live | None = None
        if consola.is_terminal:
            self._live = Live(
                self._spinner,
                console=consola,
                transient=False,
                refresh_per_second=REFRESCOS_POR_SEGUNDO_INDICADOR,
            )

    def update(self, mensaje: str) -> None:
        """Cambia el mensaje (por ejemplo, cada etapa de la preparación de Docker)."""
        self._mensaje = mensaje
        self._spinner.update(text=Text(mensaje))
        if self._es_fijado and self._live is not None:
            self._es_fijado = False
            self._live.update(self._spinner, refresh=True)

    def fijar(self) -> None:
        """Deja el mensaje actual como una línea, antes de otra que se imprime durante la tarea.

        Sin esto, lo que se imprime mientras el indicador gira sale arriba de él, y al cerrarse
        su mensaje queda después ("Dependencias instaladas" antes que "Instalando...").
        """
        if self._live is None or self._es_fijado:
            return
        self._es_fijado = True
        consola.print(Text(self._mensaje, style="dim"))
        self._live.update(Group(), refresh=True)

    def __enter__(self) -> "Indicador":
        Indicador.activo = self
        if self._live is not None:
            self._live.start()
        return self

    def __exit__(self, *_: object) -> None:
        Indicador.activo = None
        if self._live is not None:
            # La última imagen, sin el spinner, queda como una línea propia (stop la dibuja);
            # si ya se fijó, no queda nada que dibujar.
            final = Group() if self._es_fijado else Text(self._mensaje, style="dim")
            self._live.update(final)
            self._live.stop()


def _fijar_indicador() -> None:
    if Indicador.activo is not None:
        Indicador.activo.fijar()


def estado(mensaje: str) -> Indicador:
    """Indicador animado mientras dura una tarea larga (instalar dependencias...)."""
    return Indicador(mensaje)


def actualizar_estado(estado: Indicador, mensaje: str) -> None:
    """Cambia el texto del indicador animado (cada etapa de la preparación de Docker)."""
    estado.update(mensaje)


def mostrar_error(mensaje: str) -> None:
    """Muestra un error que corta la ejecución."""
    consola.print(f"[bold red]Error:[/] {escape(mensaje)}")


def _resumir_argumento(nombre: str, valor: Any) -> str:
    # La ruta siempre se ve: es lo que dice qué archivo se leyó o escribió.
    if nombre == "ruta" and isinstance(valor, str):
        return repr(_abreviar_ruta(valor))
    # El contenido de un archivo no cabe en una línea: basta con saber cuánto mide.
    if isinstance(valor, str) and ("\n" in valor or len(valor) > MAX_CARACTERES_ARGUMENTO):
        return f"<{len(valor)} caracteres>"
    # Listas como las de proponer_plan: el detalle ya se muestra en el panel del plan.
    if isinstance(valor, list | dict):
        return f"<{len(valor)} elementos>"
    return repr(valor)


def _abreviar_ruta(ruta: str) -> str:
    if len(ruta) <= MAX_CARACTERES_RUTA:
        return ruta
    # Se corta el medio y se deja más del final, donde está el nombre del archivo.
    visibles = MAX_CARACTERES_RUTA - len(ELIPSIS)
    inicio = visibles // 3
    return f"{ruta[:inicio]}{ELIPSIS}{ruta[inicio - visibles:]}"


def _ultimas_lineas(texto: str) -> str:
    lineas = texto.strip().splitlines()
    return "\n".join(lineas[-LINEAS_INFORME_VALIDACION:])


def _resumir_resultado(resultado: str) -> str:
    lineas = resultado.strip().splitlines() or [""]
    resumen = lineas[0][:MAX_CARACTERES_RESUMEN]
    if len(lineas) > 1:
        resumen += f" (+{len(lineas) - 1} líneas)"
    return resumen
