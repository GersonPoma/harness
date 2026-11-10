import io
from collections.abc import Callable

import pytest
from rich.console import Console

from harness.menu import (
    ABAJO,
    ARRIBA,
    ENTER,
    ESCAPE,
    dibujar,
    elegir,
    traducir_ansi,
    traducir_windows,
)

OPCIONES = ["rapido", "fuerte", "claude"]


def _teclas(*teclas: str) -> Callable[[], str]:
    pendientes = list(teclas)
    return lambda: pendientes.pop(0)


def _consola() -> Console:
    return Console(file=io.StringIO(), width=80)


@pytest.mark.parametrize(
    ("teclas", "esperado"),
    [
        ((ENTER,), 1),
        ((ABAJO, ENTER), 2),
        ((ABAJO, ABAJO, ENTER), 0),
        ((ARRIBA, ARRIBA, ENTER), 2),
        (("3",), 2),
        (("9", "x", ENTER), 1),
        ((ESCAPE,), None),
    ],
)
def test_elegir_con_flechas_numero_o_escape(teclas: tuple[str, ...], esperado: int | None) -> None:
    assert elegir(OPCIONES, _consola(), inicial=1, leer_tecla=_teclas(*teclas)) == esperado


def test_sin_opciones_no_lee_teclas() -> None:
    assert elegir([], _consola(), leer_tecla=lambda: pytest.fail("no debía leer")) is None


def test_dibujar_marca_la_elegida_y_numera() -> None:
    texto = dibujar(OPCIONES, 1).plain
    assert texto.splitlines()[:3] == ["  1. rapido", "› 2. fuerte", "  3. claude"]
    assert "Esc para cancelar" in texto


@pytest.mark.parametrize(
    ("teclas", "esperado"),
    [(("\xe0", "H"), ARRIBA), (("\x00", "P"), ABAJO), (("\r",), ENTER), (("\x1b",), ESCAPE),
     (("2",), "2"), (("\xe0", "K"), "")],
)
def test_traducir_windows(teclas: tuple[str, ...], esperado: str) -> None:
    primera, *resto = teclas
    assert traducir_windows(primera, _teclas(*resto)) == esperado


@pytest.mark.parametrize(
    ("teclas", "esperado"),
    [(("\x1b", "[", "A"), ARRIBA), (("\x1b", "[", "B"), ABAJO), (("\n",), ENTER),
     (("\x1b", "x"), ESCAPE)],
)
def test_traducir_ansi(teclas: tuple[str, ...], esperado: str) -> None:
    primera, *resto = teclas
    assert traducir_ansi(primera, _teclas(*resto)) == esperado


def test_ctrl_c_corta() -> None:
    with pytest.raises(KeyboardInterrupt):
        traducir_windows("\x03", _teclas())
