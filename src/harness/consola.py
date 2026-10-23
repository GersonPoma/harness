"""Salida por consola de una ejecución, con rich."""

import sys
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.status import Status

from harness.bucle import Paso
from harness.cambios import CambiosRepo
from harness.contexto import MAX_CARACTERES_AGENTS, ContextoProyecto
from harness.ejecucion import ResultadoEjecucion, Validacion
from harness.entorno_docker import ResultadoComando
from harness.init import ResultadoInit
from harness.plan import Plan, archivos_fuera_del_plan

MAX_CARACTERES_RESUMEN = 120
MAX_CARACTERES_ARGUMENTO = 60
LINEAS_INFORME_VALIDACION = 8
SIMBOLOS_ACCION = {"crear": "[green]+[/]", "modificar": "[yellow]~[/]", "eliminar": "[red]-[/]"}
RESPUESTAS_SI = ("s", "si", "sí")
RESPUESTAS_NO = ("n", "no")
LINEAS_VERIFICACION = 15

consola = Console()


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


def mostrar_inicio(ticket: str, repo: str, proveedor: str, modelo: str) -> None:
    """Muestra con qué datos arranca la ejecución."""
    consola.print(
        f"[bold green]Ticket[/] {escape(ticket)}  [bold green]Repo[/] {escape(repo)}\n"
        f"[bold green]Modelo[/] {escape(proveedor)} / {escape(modelo)}"
    )


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
    """Muestra la herramienta llamada y un resumen de su resultado."""
    argumentos = ", ".join(
        f"{nombre}={_resumir_argumento(valor)}" for nombre, valor in paso.llamada.argumentos.items()
    )
    color = "red" if paso.es_error else "dim"
    consola.print(
        f"[cyan][{paso.numero}][/] [bold]{escape(paso.llamada.nombre)}[/]({escape(argumentos)})"
    )
    consola.print(f"    [{color}]→ {escape(_resumir_resultado(paso.resultado))}[/]")


def mostrar_validacion(validacion: Validacion) -> None:
    """Muestra si pasaron los tests + lint al terminar una vuelta."""
    if validacion.es_valido:
        consola.print(f"[bold green]✓ Validación {validacion.intento}: tests OK[/]")
        return
    consola.print(f"[bold red]✗ Validación {validacion.intento}: falló[/]")
    consola.print(f"[dim]{escape(_ultimas_lineas(validacion.informe))}[/]")


def mostrar_fin(resultado: ResultadoEjecucion, cambios: CambiosRepo, ruta_log: str) -> None:
    """Muestra cómo terminó la ejecución, qué archivos cambió, los tokens y el log."""
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
    if not resultado.es_plan_rechazado:
        mostrar_cambios(cambios)
        if resultado.plan:
            mostrar_fuera_del_plan(archivos_fuera_del_plan(resultado.plan, cambios))
    consola.print(
        f"[dim]Pasos: {resultado.pasos} · Tokens: {resultado.tokens_entrada} entrada / "
        f"{resultado.tokens_salida} salida · Log: {escape(ruta_log)}[/]"
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


def preguntar_implementar() -> bool:
    """Pregunta si se implementa el plan; sin respuesta posible (EOF), no se implementa."""
    while True:
        try:
            respuesta = consola.input(escape("¿Implementar? [s/n] ")).strip().lower()
        except EOFError:
            return False
        if respuesta in RESPUESTAS_SI:
            return True
        if respuesta in RESPUESTAS_NO:
            return False


def mostrar_inicio_init(repo: str, proveedor: str, modelo: str, faltantes: list[str]) -> None:
    """Muestra qué va a generar harness init y con qué modelo."""
    consola.print(
        f"[bold green]Init[/] {escape(repo)}  [bold green]Genera[/] {escape(', '.join(faltantes))}"
        f"\n[bold green]Modelo[/] {escape(proveedor)} / {escape(modelo)}"
    )


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


def mostrar_aviso(mensaje: str) -> None:
    """Muestra un aviso que no corta la ejecución (por ejemplo, un reintento)."""
    consola.print(f"[yellow]{escape(mensaje)}[/]")


def estado(mensaje: str) -> Status:
    """Indicador animado mientras dura una tarea larga (instalar dependencias...)."""
    return consola.status(escape(mensaje))


def actualizar_estado(estado: Status, mensaje: str) -> None:
    """Cambia el texto del indicador animado (cada etapa de la preparación de Docker)."""
    estado.update(escape(mensaje))


def mostrar_error(mensaje: str) -> None:
    """Muestra un error que corta la ejecución."""
    consola.print(f"[bold red]Error:[/] {escape(mensaje)}")


def _resumir_argumento(valor: Any) -> str:
    # El contenido de un archivo no cabe en una línea: basta con saber cuánto mide.
    if isinstance(valor, str) and ("\n" in valor or len(valor) > MAX_CARACTERES_ARGUMENTO):
        return f"<{len(valor)} caracteres>"
    # Listas como las de proponer_plan: el detalle ya se muestra en el panel del plan.
    if isinstance(valor, list | dict):
        return f"<{len(valor)} elementos>"
    return repr(valor)


def _ultimas_lineas(texto: str) -> str:
    lineas = texto.strip().splitlines()
    return "\n".join(lineas[-LINEAS_INFORME_VALIDACION:])


def _resumir_resultado(resultado: str) -> str:
    lineas = resultado.strip().splitlines() or [""]
    resumen = lineas[0][:MAX_CARACTERES_RESUMEN]
    if len(lineas) > 1:
        resumen += f" (+{len(lineas) - 1} líneas)"
    return resumen
