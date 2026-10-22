from pathlib import Path

import pytest
from rich.console import Console

from harness import consola
from harness.cambios import CambiosRepo, comparar, tomar_instantanea


@pytest.fixture
def salida(monkeypatch: pytest.MonkeyPatch) -> Console:
    """Reemplaza la consola del harness por una que guarda lo impreso."""
    grabadora = Console(record=True, width=100, force_terminal=True, color_system="standard")
    monkeypatch.setattr(consola, "consola", grabadora)
    return grabadora


def test_detecta_creados_modificados_y_eliminados(repo: Path) -> None:
    (repo / "borrar.py").write_text("x", encoding="utf-8")
    antes = tomar_instantanea(repo)

    (repo / "src" / "app.py").write_text("print('chau')\n", encoding="utf-8")
    (repo / "src" / "nuevo.py").write_text("", encoding="utf-8")
    (repo / "borrar.py").unlink()

    cambios = comparar(antes, tomar_instantanea(repo))
    assert cambios == CambiosRepo(
        creados=["src/nuevo.py"], modificados=["src/app.py"], eliminados=["borrar.py"]
    )
    assert cambios.hay_cambios


def test_ignora_logs_del_harness_y_caches(repo: Path) -> None:
    antes = tomar_instantanea(repo)
    for ruta in [".harness/logs/T.jsonl", ".pytest_cache/v", "src/__pycache__/app.pyc"]:
        (repo / ruta).parent.mkdir(parents=True, exist_ok=True)
        (repo / ruta).write_text("x", encoding="utf-8")
    (repo / ".git" / "config").write_text("[otro]\n", encoding="utf-8")

    assert not comparar(antes, tomar_instantanea(repo)).hay_cambios


def test_muestra_la_lista_de_archivos(salida: Console) -> None:
    consola.mostrar_cambios(CambiosRepo(creados=["b.py"], modificados=["a.py"]))
    assert salida.export_text() == "Creados:\n  b.py\nModificados:\n  a.py\n"


def test_sin_cambios_avisa_en_amarillo(salida: Console) -> None:
    consola.mostrar_cambios(CambiosRepo())
    assert salida.export_text(clear=False) == "El modelo no modificó ningún archivo.\n"
    # Código ANSI 33: amarillo.
    assert "\x1b[33mEl modelo no modificó ningún archivo." in salida.export_text(styles=True)
