"""Salida por consola de una ejecución, con rich."""

from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.status import Status

from harness.bucle import Paso
from harness.ejecucion import ResultadoEjecucion, Validacion

MAX_CARACTERES_RESUMEN = 120
MAX_CARACTERES_ARGUMENTO = 60
LINEAS_INFORME_VALIDACION = 8

consola = Console()


def mostrar_inicio(ticket: str, repo: str, proveedor: str, modelo: str) -> None:
    """Muestra con qué datos arranca la ejecución."""
    consola.print(
        f"[bold green]Ticket[/] {escape(ticket)}  [bold green]Repo[/] {escape(repo)}\n"
        f"[bold green]Modelo[/] {escape(proveedor)} / {escape(modelo)}"
    )


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


def mostrar_fin(resultado: ResultadoEjecucion, ruta_log: str) -> None:
    """Muestra cómo terminó la ejecución, los tokens usados y dónde quedó el log."""
    if resultado.es_limite_alcanzado:
        consola.print(
            f"[bold red]Se alcanzó el límite de {resultado.pasos} pasos sin terminar.[/]"
        )
    else:
        consola.print(Panel(escape(resultado.texto_final or "(sin texto)"), title="Resultado"))
    if resultado.es_validado is False:
        consola.print(
            f"[bold red]Los tests no pasan después de {resultado.validaciones} validaciones.[/]"
        )
    consola.print(
        f"[dim]Pasos: {resultado.pasos} · Tokens: {resultado.tokens_entrada} entrada / "
        f"{resultado.tokens_salida} salida · Log: {escape(ruta_log)}[/]"
    )


def mostrar_aviso(mensaje: str) -> None:
    """Muestra un aviso que no corta la ejecución (por ejemplo, un reintento)."""
    consola.print(f"[yellow]{escape(mensaje)}[/]")


def estado(mensaje: str) -> Status:
    """Indicador animado mientras dura una tarea larga (instalar dependencias...)."""
    return consola.status(escape(mensaje))


def mostrar_error(mensaje: str) -> None:
    """Muestra un error que corta la ejecución."""
    consola.print(f"[bold red]Error:[/] {escape(mensaje)}")


def _resumir_argumento(valor: Any) -> str:
    # El contenido de un archivo no cabe en una línea: basta con saber cuánto mide.
    if isinstance(valor, str) and ("\n" in valor or len(valor) > MAX_CARACTERES_ARGUMENTO):
        return f"<{len(valor)} caracteres>"
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
