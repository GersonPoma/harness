from pathlib import Path

import pytest

from harness.herramientas.rutas import ErrorRuta, resolver_ruta, validar_escritura


def test_ruta_relativa_dentro_del_repo(repo: Path) -> None:
    assert resolver_ruta(repo, "src/app.py") == (repo / "src" / "app.py").resolve()


def test_punto_es_la_raiz(repo: Path) -> None:
    assert resolver_ruta(repo, ".") == repo.resolve()


@pytest.mark.parametrize("ruta", ["../secreto.txt", "src/../../secreto.txt", ".."])
def test_rechaza_salir_del_repo(repo: Path, ruta: str) -> None:
    with pytest.raises(ErrorRuta, match="fuera del repo"):
        resolver_ruta(repo, ruta)


def test_rechaza_ruta_absoluta(repo: Path) -> None:
    with pytest.raises(ErrorRuta, match="absoluta"):
        resolver_ruta(repo, str(repo / "src" / "app.py"))


def test_rechaza_ruta_absoluta_estilo_unix(repo: Path) -> None:
    with pytest.raises(ErrorRuta, match="absoluta"):
        resolver_ruta(repo, "/etc/passwd")


@pytest.mark.parametrize("ruta", [".git", ".git/config", ".GIT/config", "src/../.git/config"])
def test_rechaza_carpeta_git(repo: Path, ruta: str) -> None:
    with pytest.raises(ErrorRuta, match="prohibida"):
        resolver_ruta(repo, ruta)


def test_rechaza_symlink_que_sale_del_repo(repo: Path) -> None:
    enlace = repo / "enlace"
    try:
        enlace.symlink_to(repo.parent)
    except OSError:
        pytest.skip("Este sistema no permite crear symlinks sin permisos")
    with pytest.raises(ErrorRuta, match="fuera del repo"):
        resolver_ruta(repo, "enlace/secreto.txt")


@pytest.mark.parametrize(
    "ruta", ["harness.yaml", "HARNESS.YAML", ".harness/logs/x.jsonl", ".harness/sessions/T.json"]
)
def test_escritura_protegida(repo: Path, ruta: str) -> None:
    with pytest.raises(ErrorRuta, match="protegido"):
        validar_escritura(repo, resolver_ruta(repo, ruta))


@pytest.mark.parametrize("ruta", ["src/nuevo.py", "docs/harness.yaml", ".harness/memory.md"])
def test_escritura_permitida(repo: Path, ruta: str) -> None:
    validar_escritura(repo, resolver_ruta(repo, ruta))
