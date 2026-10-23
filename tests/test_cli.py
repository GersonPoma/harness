import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness import cli
from harness.config import ConfigModelo
from harness.tipos import LlamadaHerramienta, RespuestaModelo
from tests.falsos import ProveedorFalso, pedir, terminar, vacia


@pytest.fixture
def repo_sin_yaml(tmp_path: Path) -> Path:
    """Repo sin harness.yaml (modo fase 1: sin Docker) y un ticket fuera de él."""
    raiz = tmp_path / "repo"
    raiz.mkdir()
    (raiz / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "T-1.md").write_text("# T-1: algo\n", encoding="utf-8")
    return raiz


def _correr(
    monkeypatch: pytest.MonkeyPatch,
    repo: Path,
    respuestas: list[RespuestaModelo] | ProveedorFalso,
    *opciones: str,
    entrada: str | None = None,
) -> tuple[int, str]:
    config = ConfigModelo("gemini", "modelo-x", None, "clave")
    proveedor = respuestas if isinstance(respuestas, ProveedorFalso) else ProveedorFalso(respuestas)
    monkeypatch.setattr(cli, "cargar_config", lambda: config)
    monkeypatch.setattr(cli, "crear_proveedor", lambda *_: proveedor)
    argumentos = ["run", str(repo.parent / "T-1.md"), "--repo", str(repo), *opciones]
    resultado = CliRunner().invoke(cli.app, argumentos, input=entrada)
    return resultado.exit_code, resultado.output


def _eventos_del_log(repo: Path) -> list[dict]:
    (log,) = (repo / ".harness" / "logs").glob("*.jsonl")
    return [json.loads(linea) for linea in log.read_text(encoding="utf-8").splitlines()]


def test_tres_respuestas_vacias_terminan_con_codigo_1(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path
) -> None:
    codigo, salida = _correr(monkeypatch, repo_sin_yaml, [vacia(), vacia(), vacia()])
    assert codigo == 1
    assert "respondió vacío (sin texto ni herramientas) 3 veces seguidas" in salida
    eventos = [e["evento"] for e in _eventos_del_log(repo_sin_yaml)]
    assert eventos == ["inicio", "aviso", "aviso", "error"]


def test_al_final_lista_los_archivos_cambiados(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path
) -> None:
    escribir = LlamadaHerramienta(
        "c1", "escribir_archivo", {"ruta": "nuevo.py", "contenido": "y = 2\n"}
    )
    codigo, salida = _correr(monkeypatch, repo_sin_yaml, [pedir(escribir), terminar()])
    assert codigo == 0
    assert "Creados:\n  nuevo.py" in salida
    fin = _eventos_del_log(repo_sin_yaml)[-1]
    assert (fin["creados"], fin["modificados"], fin["eliminados"]) == (["nuevo.py"], [], [])


def test_sin_cambios_avisa(monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path) -> None:
    codigo, salida = _correr(monkeypatch, repo_sin_yaml, [terminar("No hice nada")])
    assert codigo == 0
    assert "El modelo no modificó ningún archivo." in salida


def test_agents_md_va_al_contexto_y_al_log(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path
) -> None:
    texto = "# demo\nReglas del proyecto.\n"
    (repo_sin_yaml / "AGENTS.md").write_text(texto, encoding="utf-8")
    proveedor = ProveedorFalso([terminar()])
    codigo, salida = _correr(monkeypatch, repo_sin_yaml, proveedor)
    assert codigo == 0
    assert f"AGENTS.md cargado ({len(texto)} caracteres)." in salida
    assert "Reglas del proyecto." in proveedor.conversaciones[0][0].contenido
    inicio = _eventos_del_log(repo_sin_yaml)[0]
    assert (inicio["agents_md"], inicio["agents_md_caracteres"]) == (True, len(texto))


def test_sin_agents_md_avisa(monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path) -> None:
    _, salida = _correr(monkeypatch, repo_sin_yaml, [terminar()])
    assert "El repo no tiene AGENTS.md" in salida
    assert _eventos_del_log(repo_sin_yaml)[0]["agents_md"] is False
