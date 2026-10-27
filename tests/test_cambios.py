import shutil
import subprocess
from pathlib import Path

import pytest
from rich.console import Console

from harness import consola
from harness.cambios import CARPETAS_DE_COMPILACION, CambiosRepo, comparar, tomar_instantanea

requiere_git = pytest.mark.skipif(shutil.which("git") is None, reason="Git no está instalado")


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


def test_sin_git_ignora_las_carpetas_de_compilacion(repo: Path) -> None:
    # El repo de prueba tiene un .git falso: Git no lo reconoce y se usa la lista fija.
    antes = tomar_instantanea(repo)
    for carpeta in CARPETAS_DE_COMPILACION:
        _escribir(repo / carpeta / "salida.class")
    _escribir(repo / "src" / "nuevo.py")

    assert comparar(antes, tomar_instantanea(repo)) == CambiosRepo(creados=["src/nuevo.py"])


@pytest.fixture
def repo_git(tmp_path: Path) -> Path:
    """Repo Git real con dos archivos en el índice y un .gitignore de Maven."""
    raiz = tmp_path / "repo_git"
    raiz.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=raiz, check=True)
    _escribir(raiz / ".gitignore", "target/\n*.log\n")
    _escribir(raiz / "src" / "App.java")
    _escribir(raiz / "borrar.java")
    subprocess.run(["git", "add", "."], cwd=raiz, check=True)
    return raiz


def test_sin_git_ignora_bin_de_los_ides(repo: Path) -> None:
    # Eclipse y VS Code (Java) compilan en bin/main y bin/test.
    antes = tomar_instantanea(repo)
    _escribir(repo / "bin" / "main" / "com" / "App.class")
    _escribir(repo / "bin" / "test" / "com" / "AppTest.class")
    assert not comparar(antes, tomar_instantanea(repo)).hay_cambios


@requiere_git
def test_con_git_respeta_el_gitignore(repo_git: Path) -> None:
    antes = tomar_instantanea(repo_git)
    _escribir(repo_git / "target" / "classes" / "App.class")
    _escribir(repo_git / "target" / "surefire-reports" / "TEST-App.xml")
    _escribir(repo_git / "app.log")
    _escribir(repo_git / "src" / "App.java", "class App { int x; }")
    _escribir(repo_git / "src" / "Nuevo.java")
    (repo_git / "borrar.java").unlink()

    assert comparar(antes, tomar_instantanea(repo_git)) == CambiosRepo(
        creados=["src/Nuevo.java"], modificados=["src/App.java"], eliminados=["borrar.java"]
    )


@requiere_git
def test_con_git_manda_el_gitignore_y_no_la_lista_fija(repo_git: Path) -> None:
    # 'build' no está en el .gitignore de este proyecto: es código del repo y se sigue.
    antes = tomar_instantanea(repo_git)
    _escribir(repo_git / "build" / "script.py")
    assert comparar(antes, tomar_instantanea(repo_git)).creados == ["build/script.py"]


@requiere_git
def test_con_git_ignora_logs_del_harness_y_caches(repo_git: Path) -> None:
    antes = tomar_instantanea(repo_git)
    for ruta in [".harness/logs/T.jsonl", ".pytest_cache/v", "src/__pycache__/app.pyc"]:
        _escribir(repo_git / ruta)
    assert not comparar(antes, tomar_instantanea(repo_git)).hay_cambios


def test_muestra_la_lista_de_archivos(salida: Console) -> None:
    consola.mostrar_cambios(CambiosRepo(creados=["b.py"], modificados=["a.py"]))
    assert salida.export_text() == "Creados:\n  b.py\nModificados:\n  a.py\n"


def test_sin_cambios_avisa_en_amarillo(salida: Console) -> None:
    consola.mostrar_cambios(CambiosRepo())
    assert salida.export_text(clear=False) == "El modelo no modificó ningún archivo.\n"
    # Código ANSI 33: amarillo.
    assert "\x1b[33mEl modelo no modificó ningún archivo." in salida.export_text(styles=True)


def _escribir(ruta: Path, contenido: str = "x") -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(contenido, encoding="utf-8")
