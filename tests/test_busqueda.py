from pathlib import Path

import pytest

from harness.herramientas import busqueda
from harness.herramientas.archivos import ErrorHerramienta
from harness.herramientas.busqueda import buscar_en_codigo
from harness.herramientas.rutas import ErrorRuta
from tests.git_prueba import iniciar_repo, requiere_git


@pytest.fixture
def codigo(tmp_path: Path) -> Path:
    raiz = tmp_path / "repo"
    (raiz / "src").mkdir(parents=True)
    (raiz / "tests").mkdir()
    (raiz / "src" / "servicio.py").write_text(
        "class VentaService:\n    def total(self):\n        return 0\n", encoding="utf-8"
    )
    (raiz / "tests" / "test_servicio.py").write_text(
        "from src.servicio import VentaService\n\n\ndef test_total():\n"
        "    assert VentaService().total() == 0\n",
        encoding="utf-8",
    )
    return raiz


def test_busca_en_todo_el_repo(codigo: Path) -> None:
    assert buscar_en_codigo(codigo, "VentaService") == (
        "src/servicio.py:1: class VentaService:\n"
        "tests/test_servicio.py:1: from src.servicio import VentaService\n"
        "tests/test_servicio.py:5: assert VentaService().total() == 0"
    )


def test_busca_solo_en_una_carpeta(codigo: Path) -> None:
    resultado = buscar_en_codigo(codigo, "VentaService", "src")
    assert resultado == "src/servicio.py:1: class VentaService:"


def test_busca_con_una_expresion_regular(codigo: Path) -> None:
    resultado = buscar_en_codigo(codigo, r"def \w+\(", es_regex=True)
    assert resultado.splitlines() == [
        "src/servicio.py:2: def total(self):",
        "tests/test_servicio.py:4: def test_total():",
    ]


def test_el_patron_es_literal_por_defecto(codigo: Path) -> None:
    assert "Sin coincidencias" in buscar_en_codigo(codigo, "total(.)")
    assert "servicio.py:2" in buscar_en_codigo(codigo, "total(self)")


def test_sin_coincidencias(codigo: Path) -> None:
    assert buscar_en_codigo(codigo, "Inventario") == "Sin coincidencias de 'Inventario' en '.'."


@pytest.mark.parametrize(
    ("patron", "es_regex", "mensaje"),
    [("", False, "está vacío"), ("def (", True, "no es válida")],
)
def test_patrones_invalidos(codigo: Path, patron: str, es_regex: bool, mensaje: str) -> None:
    with pytest.raises(ErrorHerramienta, match=mensaje):
        buscar_en_codigo(codigo, patron, es_regex=es_regex)


def test_no_lee_secretos_ni_git_ni_binarios(codigo: Path) -> None:
    (codigo / ".env").write_text("CLAVE_VentaService=sk-secreta\n", encoding="utf-8")
    (codigo / ".env.local").write_text("VentaService=1\n", encoding="utf-8")
    (codigo / ".envrc").write_text("export VentaService=sk-direnv\n", encoding="utf-8")
    (codigo / ".git").mkdir()
    (codigo / ".git" / "config").write_text("VentaService\n", encoding="utf-8")
    (codigo / "logo.bin").write_bytes(b"\xff\xfeVentaService\x00")
    resultado = buscar_en_codigo(codigo, "VentaService")
    assert ".env" not in resultado and ".git" not in resultado and "logo" not in resultado
    assert "sk-secreta" not in resultado and "sk-direnv" not in resultado


def test_no_sale_del_repo(codigo: Path) -> None:
    with pytest.raises(ErrorRuta, match="fuera del repo"):
        buscar_en_codigo(codigo, "x", "..")


@requiere_git
def test_con_git_respeta_el_gitignore(codigo: Path) -> None:
    (codigo / ".gitignore").write_text("build/\n", encoding="utf-8")
    (codigo / "build").mkdir()
    (codigo / "build" / "generado.py").write_text("VentaService = 1\n", encoding="utf-8")
    iniciar_repo(codigo)
    assert "build/" not in buscar_en_codigo(codigo, "VentaService")


def test_limita_las_coincidencias_y_el_largo(
    codigo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(busqueda, "MAX_COINCIDENCIAS", 2)
    (codigo / "src" / "largo.py").write_text(
        "x = 'VentaService" + "a" * 300 + "'\n", encoding="utf-8"
    )
    lineas = buscar_en_codigo(codigo, "VentaService").splitlines()
    assert lineas[-1].startswith("[... y 2 coincidencias más")
    assert len(lineas) == 3
    assert all(len(linea) < 230 for linea in lineas)


def test_no_lee_archivos_enormes(codigo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(busqueda, "MAX_BYTES_ARCHIVO", 10)
    assert "Sin coincidencias" in buscar_en_codigo(codigo, "VentaService")
