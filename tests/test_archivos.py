from pathlib import Path

import pytest

from harness.herramientas import archivos
from harness.herramientas.archivos import (
    ErrorHerramienta,
    escribir_archivo,
    leer_archivo,
    listar_directorio,
)
from harness.herramientas.rutas import ErrorRuta


def test_leer_archivo(repo: Path) -> None:
    assert leer_archivo(repo, "src/app.py") == "print('hola')\n"


def test_leer_harness_yaml_esta_permitido(repo: Path) -> None:
    assert leer_archivo(repo, "harness.yaml") == "version: 1\n"


def test_leer_archivo_inexistente(repo: Path) -> None:
    with pytest.raises(ErrorHerramienta, match="No existe"):
        leer_archivo(repo, "no_existe.py")


def test_leer_archivo_binario(repo: Path) -> None:
    (repo / "imagen.png").write_bytes(b"\x89PNG\xff\xfe")
    with pytest.raises(ErrorHerramienta, match="UTF-8"):
        leer_archivo(repo, "imagen.png")


def test_leer_archivo_largo_se_recorta(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(archivos, "MAX_CARACTERES_LECTURA", 10)
    (repo / "largo.txt").write_text("x" * 50, encoding="utf-8")
    resultado = leer_archivo(repo, "largo.txt")
    assert resultado.startswith("x" * 10 + "\n")
    assert "10 de 50" in resultado


def test_escribir_archivo_crea_carpetas(repo: Path) -> None:
    escribir_archivo(repo, "src/nuevo/modulo.py", "a = 1\nb = 2\n")
    # Se lee en bytes para comprobar que en Windows no se agregan \r.
    assert (repo / "src" / "nuevo" / "modulo.py").read_bytes() == b"a = 1\nb = 2\n"


def test_escribir_archivo_reemplaza(repo: Path) -> None:
    escribir_archivo(repo, "src/app.py", "nuevo")
    assert leer_archivo(repo, "src/app.py") == "nuevo"


def test_escribir_harness_yaml_esta_prohibido(repo: Path) -> None:
    with pytest.raises(ErrorRuta, match="protegido"):
        escribir_archivo(repo, "harness.yaml", "limites: {}")
    assert (repo / "harness.yaml").read_text(encoding="utf-8") == "version: 1\n"


def test_escribir_fuera_del_repo_esta_prohibido(repo: Path) -> None:
    with pytest.raises(ErrorRuta):
        escribir_archivo(repo, "../hackeado.txt", "x")
    assert not (repo.parent / "hackeado.txt").exists()


def test_escribir_en_git_esta_prohibido(repo: Path) -> None:
    with pytest.raises(ErrorRuta):
        escribir_archivo(repo, ".git/hooks/pre-commit", "x")


def test_listar_directorio_raiz(repo: Path) -> None:
    # .git se oculta; las carpetas van primero y terminan en '/'.
    assert listar_directorio(repo) == "src/\nharness.yaml"


def test_listar_subcarpeta(repo: Path) -> None:
    assert listar_directorio(repo, "src") == "app.py"


def test_listar_carpeta_vacia(repo: Path) -> None:
    (repo / "vacia").mkdir()
    assert "vacía" in listar_directorio(repo, "vacia")


def test_listar_carpeta_inexistente(repo: Path) -> None:
    with pytest.raises(ErrorHerramienta, match="No existe"):
        listar_directorio(repo, "nada")


def test_listar_fuera_del_repo(repo: Path) -> None:
    with pytest.raises(ErrorRuta):
        listar_directorio(repo, "..")
