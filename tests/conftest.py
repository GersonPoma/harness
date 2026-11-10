import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Repo objetivo de prueba con algunos archivos."""
    raiz = tmp_path / "repo"
    (raiz / "src").mkdir(parents=True)
    (raiz / "src" / "app.py").write_text("print('hola')\n", encoding="utf-8")
    (raiz / "harness.yaml").write_text("version: 1\n", encoding="utf-8")
    (raiz / ".git").mkdir()
    (raiz / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (tmp_path / "secreto.txt").write_text("fuera del repo", encoding="utf-8")
    return raiz


@pytest.fixture(autouse=True)
def sin_config_del_usuario(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Ningún test lee el ~/.harness/ de quien los corre, y el entorno queda como estaba
    (cargar_env carga el .env y las credenciales de config.toml en os.environ)."""
    monkeypatch.setattr("harness.config_usuario.CARPETA_USUARIO", tmp_path / "usuario-harness")
    monkeypatch.setattr("harness.config._origenes", {})
    antes = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(antes)


@pytest.fixture(autouse=True)
def sin_mcp_del_usuario(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Ningún test usa el mcp.json ni las variables HARNESS_MCP_*, HARNESS_RESPALDO o
    HARNESS_CONSOLA_CRUDA de quien los corre."""
    monkeypatch.setattr("harness.cli.ruta_mcp", lambda: tmp_path / "sin-mcp.json")
    monkeypatch.delenv("HARNESS_MCP_GITHUB", raising=False)
    monkeypatch.delenv("HARNESS_MCP_JIRA", raising=False)
    monkeypatch.delenv("HARNESS_MCP_HERRAMIENTAS_MODELO", raising=False)
    monkeypatch.delenv("HARNESS_RESPALDO", raising=False)
    monkeypatch.delenv("HARNESS_CONSOLA_CRUDA", raising=False)
    # harness init consulta las imágenes descargadas: los tests no dependen del Docker local.
    monkeypatch.setattr("harness.cli.listar_imagenes", lambda: [])
    # El preflight de Docker tampoco: los tests que lo prueban lo reemplazan por uno que falla.
    monkeypatch.setattr("harness.cli.verificar_docker", lambda: 0.1)
