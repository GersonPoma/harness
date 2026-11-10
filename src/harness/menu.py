"""Menú para elegir una opción con las flechas (harness perfil usar, sin nombre).

Las teclas se leen de a una, sin esperar Enter: con msvcrt en Windows y con termios en Linux y
macOS. También se acepta el número de la opción. El lector de teclas se inyecta (los tests no
tienen terminal).
"""

import os
import sys
from collections.abc import Callable

from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.text import Text

ARRIBA = "arriba"
ABAJO = "abajo"
ENTER = "enter"
ESCAPE = "escape"
# Teclas especiales de Windows: un prefijo (\x00 o \xe0) y después el código de la flecha.
PREFIJOS_WINDOWS = ("\x00", "\xe0")
FLECHAS_WINDOWS = {"H": ARRIBA, "P": ABAJO}
# En una terminal ANSI las flechas llegan como ESC [ A / ESC [ B.
FLECHAS_ANSI = {"A": ARRIBA, "B": ABAJO}
TECLAS_ENTER = ("\r", "\n")
TECLA_ESCAPE = "\x1b"
TECLA_CTRL_C = "\x03"
MARCA = "›"
AYUDA = "↑/↓ para moverte, Enter para elegir (o el número), Esc para cancelar"

LectorTeclas = Callable[[], str]


def elegir(
    opciones: list[str],
    consola: Console,
    inicial: int = 0,
    leer_tecla: LectorTeclas | None = None,
) -> int | None:
    """El índice de la opción elegida, o None si se canceló con Esc."""
    if not opciones:
        return None
    leer = leer_tecla or leer_tecla_de_la_terminal
    indice = min(max(inicial, 0), len(opciones) - 1)
    with Live(dibujar(opciones, indice), console=consola, transient=True,
              auto_refresh=False) as vivo:
        while True:
            tecla = leer()
            if tecla == ENTER:
                return indice
            if tecla == ESCAPE:
                return None
            if tecla.isdigit() and 1 <= int(tecla) <= len(opciones):
                return int(tecla) - 1
            if tecla == ARRIBA:
                indice = (indice - 1) % len(opciones)
            elif tecla == ABAJO:
                indice = (indice + 1) % len(opciones)
            vivo.update(dibujar(opciones, indice), refresh=True)


def dibujar(opciones: list[str], indice: int) -> Text:
    """Las opciones numeradas, con la marca en la elegida."""
    lineas = []
    for numero, opcion in enumerate(opciones, start=1):
        if numero - 1 == indice:
            lineas.append(f"[bold cyan]{MARCA} {numero}. {escape(opcion)}[/]")
        else:
            lineas.append(f"  {numero}. {escape(opcion)}")
    lineas.append(f"[dim]{AYUDA}[/]")
    return Text.from_markup("\n".join(lineas))


def traducir_windows(primera: str, leer: LectorTeclas) -> str:
    """Una tecla de msvcrt.getwch en el nombre que usa elegir()."""
    if primera in PREFIJOS_WINDOWS:
        return FLECHAS_WINDOWS.get(leer(), "")
    return _traducir_simple(primera)


def traducir_ansi(primera: str, leer: LectorTeclas) -> str:
    """Una tecla de una terminal ANSI (Linux, macOS) en el nombre que usa elegir()."""
    if primera == TECLA_ESCAPE:
        if leer() != "[":
            return ESCAPE
        return FLECHAS_ANSI.get(leer(), "")
    return _traducir_simple(primera)


def leer_tecla_de_la_terminal() -> str:
    """Lee una tecla sin esperar Enter."""
    if os.name == "nt":
        import msvcrt

        return traducir_windows(msvcrt.getwch(), msvcrt.getwch)
    import termios
    import tty

    descriptor = sys.stdin.fileno()
    anterior = termios.tcgetattr(descriptor)
    try:
        tty.setraw(descriptor)
        return traducir_ansi(sys.stdin.read(1), lambda: sys.stdin.read(1))
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, anterior)


def _traducir_simple(tecla: str) -> str:
    if tecla == TECLA_CTRL_C:
        raise KeyboardInterrupt
    if tecla in TECLAS_ENTER:
        return ENTER
    if tecla == TECLA_ESCAPE:
        return ESCAPE
    return tecla
