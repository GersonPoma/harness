"""Preflight de Docker: se comprueba al principio, antes de los MCP y de la rama."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness import cli
from harness.config import ConfigModelo
from harness.entorno_docker import MENSAJE_DOCKER_NO_CORRE, ErrorDocker
from harness.sesion import ESTADO_IMPLEMENTACION, MOTIVO_LIMITE_PASOS, Sesion, guardar_sesion
from tests.falsos import ProveedorFalso
from tests.git_prueba import git, iniciar_repo, requiere_git

CONFIG = ConfigModelo("gemini", "modelo-x", None, "clave-falsa-0123456789")
YAML = "version: 1\nproyecto: demo\ndocker:\n  imagen: python:3.12-slim\ncomandos:\n  tests: x\n"
MCP_GITHUB_DOCKER = {
    "mcpServers": {"github": {"command": "docker", "args": ["run", "-i", "--rm", "imagen"]}}
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    raiz = tmp_path / "repo"
    raiz.mkdir()
    (raiz / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "T-1.md").write_text("# T-1: Crear tareas\n", encoding="utf-8")
    return raiz


def _docker_cerrado(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """El preflight falla como con Docker Desktop cerrado; anota cada vez que se llama."""
    llamadas: list[str] = []

    def sin_docker() -> float:
        llamadas.append("docker")
        raise ErrorDocker(MENSAJE_DOCKER_NO_CORRE)

    monkeypatch.setattr(cli, "verificar_docker", sin_docker)
    return llamadas


def _sin_mcp_ni_ejecucion(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Anota si se intentó conectar un MCP o ejecutar: con Docker cerrado no debe pasar."""
    intentos: list[str] = []
    monkeypatch.setattr(cli, "_conectar_mcp", lambda *_: intentos.append("mcp"))
    monkeypatch.setattr(cli, "_ejecutar", lambda *_: intentos.append("ejecutar"))
    return intentos


def _invocar(monkeypatch: pytest.MonkeyPatch, argumentos: list[str]) -> tuple[int, str]:
    monkeypatch.setattr(cli, "cargar_config", lambda: CONFIG)
    monkeypatch.setattr(cli, "crear_proveedor", lambda *_: ProveedorFalso([]))
    resultado = CliRunner().invoke(cli.app, argumentos)
    return resultado.exit_code, " ".join(resultado.output.split())


def _run(monkeypatch: pytest.MonkeyPatch, repo: Path) -> tuple[int, str]:
    return _invocar(monkeypatch, ["run", str(repo.parent / "T-1.md"), "--repo", str(repo)])


@requiere_git
def test_run_con_docker_cerrado_no_crea_la_rama_ni_conecta_el_mcp(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    (repo / "harness.yaml").write_text(YAML, encoding="utf-8")
    iniciar_repo(repo)
    _docker_cerrado(monkeypatch)
    intentos = _sin_mcp_ni_ejecucion(monkeypatch)

    codigo, salida = _run(monkeypatch, repo)

    assert codigo == 1
    assert MENSAJE_DOCKER_NO_CORRE in salida
    assert "ExceptionGroup" not in salida
    assert intentos == []
    assert git(repo, "branch", "--list", "feature/T-1") == ""
    (log,) = (repo / ".harness" / "logs").glob("T-1-*.jsonl")
    errores = [json.loads(linea) for linea in log.read_text(encoding="utf-8").splitlines()]
    assert any(e.get("mensaje") == MENSAJE_DOCKER_NO_CORRE for e in errores)


def test_sin_harness_yaml_ni_mcp_en_docker_no_se_exige_docker(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    llamadas = _docker_cerrado(monkeypatch)
    _sin_mcp_ni_ejecucion(monkeypatch)
    _run(monkeypatch, repo)
    assert llamadas == []


def test_sin_harness_yaml_un_mcp_en_docker_exige_docker(
    monkeypatch: pytest.MonkeyPatch, repo: Path, tmp_path: Path
) -> None:
    (tmp_path / "sin-mcp.json").write_text(json.dumps(MCP_GITHUB_DOCKER), encoding="utf-8")
    llamadas = _docker_cerrado(monkeypatch)
    intentos = _sin_mcp_ni_ejecucion(monkeypatch)

    codigo, salida = _run(monkeypatch, repo)

    assert codigo == 1 and llamadas == ["docker"] and intentos == []
    assert MENSAJE_DOCKER_NO_CORRE in salida


def test_mcp_en_docker_que_no_se_va_a_usar_no_exige_docker(
    monkeypatch: pytest.MonkeyPatch, repo: Path, tmp_path: Path
) -> None:
    (tmp_path / "sin-mcp.json").write_text(json.dumps(MCP_GITHUB_DOCKER), encoding="utf-8")
    llamadas = _docker_cerrado(monkeypatch)
    _sin_mcp_ni_ejecucion(monkeypatch)
    argumentos = [
        "run", str(repo.parent / "T-1.md"), "--repo", str(repo),
        "--sin-pr", "--sin-herramientas-github",
    ]
    _invocar(monkeypatch, argumentos)
    assert llamadas == []


@requiere_git
def test_resume_con_docker_cerrado_no_vuelve_a_crear_la_rama(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    (repo / "harness.yaml").write_text(YAML, encoding="utf-8")
    iniciar_repo(repo)
    # La rama se borró porque quedó vacía: sin Docker, resume no debe crearla de nuevo.
    sesion = Sesion(
        ticket={"clave": "T-1", "titulo": "Crear tareas", "fuente": "local", "url": None,
                "texto": "# T-1: Crear tareas"},
        estado=ESTADO_IMPLEMENTACION, proveedor="gemini/modelo-x",
        modelos_usados=["gemini/modelo-x"], pasos=3, es_con_plan=False,
        rama={"nombre": "feature/T-1", "base": "main", "remoto": "origin",
              "es_con_push": False, "es_con_pr": False, "head_al_cortar": None},
        motivo_corte=MOTIVO_LIMITE_PASOS,
    )
    guardar_sesion(repo, sesion, [])
    _docker_cerrado(monkeypatch)
    intentos = _sin_mcp_ni_ejecucion(monkeypatch)

    codigo, salida = _invocar(monkeypatch, ["resume", "T-1", "--repo", str(repo)])

    assert codigo == 1
    assert MENSAJE_DOCKER_NO_CORRE in salida
    assert intentos == []
    assert git(repo, "branch", "--list", "feature/T-1") == ""


def test_init_verificar_con_docker_cerrado_no_llama_al_modelo(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    _docker_cerrado(monkeypatch)
    generados: list[str] = []
    monkeypatch.setattr(cli, "_generar_archivos_init", lambda *_: generados.append("init"))

    codigo, salida = _invocar(monkeypatch, ["init", "--repo", str(repo), "--verificar"])

    assert codigo == 1
    assert MENSAJE_DOCKER_NO_CORRE in salida
    assert generados == []


def test_init_sin_verificar_no_exige_docker(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    llamadas = _docker_cerrado(monkeypatch)
    monkeypatch.setattr(cli, "_generar_archivos_init", lambda *_: None)
    _invocar(monkeypatch, ["init", "--repo", str(repo)])
    assert llamadas == []
