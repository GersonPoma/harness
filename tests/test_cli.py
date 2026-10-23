import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness import cli
from harness.config import ConfigModelo
from harness.tipos import LlamadaHerramienta, RespuestaModelo
from tests.falsos import ProveedorFalso, pedir, proponer, terminar, vacia


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
    respuestas = [vacia(), vacia(), vacia()]
    codigo, salida = _correr(monkeypatch, repo_sin_yaml, respuestas, "--sin-plan")
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
    respuestas = [pedir(escribir), terminar()]
    codigo, salida = _correr(monkeypatch, repo_sin_yaml, respuestas, "--sin-plan")
    assert codigo == 0
    assert "Creados:\n  nuevo.py" in salida
    fin = _eventos_del_log(repo_sin_yaml)[-1]
    assert (fin["creados"], fin["modificados"], fin["eliminados"]) == (["nuevo.py"], [], [])


def test_sin_cambios_avisa(monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path) -> None:
    respuestas = [terminar("No hice nada")]
    codigo, salida = _correr(monkeypatch, repo_sin_yaml, respuestas, "--sin-plan")
    assert codigo == 0
    assert "El modelo no modificó ningún archivo." in salida


def test_agents_md_va_al_contexto_y_al_log(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path
) -> None:
    texto = "# demo\nReglas del proyecto.\n"
    (repo_sin_yaml / "AGENTS.md").write_text(texto, encoding="utf-8")
    proveedor = ProveedorFalso([terminar()])
    codigo, salida = _correr(monkeypatch, repo_sin_yaml, proveedor, "--sin-plan")
    assert codigo == 0
    assert f"AGENTS.md cargado ({len(texto)} caracteres)." in salida
    assert "Reglas del proyecto." in proveedor.conversaciones[0][0].contenido
    inicio = _eventos_del_log(repo_sin_yaml)[0]
    assert (inicio["agents_md"], inicio["agents_md_caracteres"]) == (True, len(texto))


def test_sin_agents_md_avisa(monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path) -> None:
    _, salida = _correr(monkeypatch, repo_sin_yaml, [terminar()], "--sin-plan")
    assert "El repo no tiene AGENTS.md" in salida
    assert _eventos_del_log(repo_sin_yaml)[0]["agents_md"] is False


def _escribir(ruta: str) -> RespuestaModelo:
    argumentos = {"ruta": ruta, "contenido": "x"}
    return pedir(LlamadaHerramienta(f"e-{ruta}", "escribir_archivo", argumentos))


def test_muestra_el_plan_y_avisa_lo_que_quedo_fuera(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path
) -> None:
    respuestas = [proponer(), _escribir("nuevo.py"), _escribir("extra.py"), terminar()]
    codigo, salida = _correr(monkeypatch, repo_sin_yaml, respuestas)
    assert codigo == 0
    assert "Plan" in salida and "Agregar el módulo nuevo." in salida
    assert "+ nuevo.py (crear: Lo pide el ticket.)" in salida
    assert "1. Crear nuevo.py" in salida
    assert "Archivos que cambiaron y no estaban en el plan:\n  extra.py" in salida
    eventos = _eventos_del_log(repo_sin_yaml)
    plan = next(e for e in eventos if e["evento"] == "plan")
    assert plan["es_aprobado"] is True and plan["archivos"][0]["ruta"] == "nuevo.py"
    assert eventos[-1]["fuera_del_plan"] == ["extra.py"]


def test_aprobar_plan_con_n_termina_sin_implementar(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path
) -> None:
    proveedor = ProveedorFalso([proponer(), _escribir("nuevo.py"), terminar()])
    codigo, salida = _correr(
        monkeypatch, repo_sin_yaml, proveedor, "--aprobar-plan", entrada="quizas\nn\n"
    )
    assert codigo == 0
    assert salida.count("¿Implementar? [s/n]") == 2
    assert "Plan rechazado: no se implementó nada." in salida
    assert "El modelo no modificó ningún archivo" not in salida
    assert len(proveedor.conversaciones) == 1
    assert not (repo_sin_yaml / "nuevo.py").exists()
    eventos = _eventos_del_log(repo_sin_yaml)
    assert next(e for e in eventos if e["evento"] == "plan")["es_aprobado"] is False
    assert eventos[-1]["plan_rechazado"] is True


def test_aprobar_plan_con_s_implementa(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path
) -> None:
    respuestas = [proponer(), _escribir("nuevo.py"), terminar()]
    codigo, _ = _correr(monkeypatch, repo_sin_yaml, respuestas, "--aprobar-plan", entrada="s\n")
    assert codigo == 0
    assert (repo_sin_yaml / "nuevo.py").exists()


def test_sin_plan_y_aprobar_plan_juntos_es_error(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path
) -> None:
    codigo, salida = _correr(monkeypatch, repo_sin_yaml, [], "--sin-plan", "--aprobar-plan")
    assert codigo == 1
    assert "no se pueden usar juntos" in salida


def test_sin_plan_valido_termina_con_codigo_1(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path
) -> None:
    codigo, salida = _correr(monkeypatch, repo_sin_yaml, [terminar("a"), terminar("b")])
    assert codigo == 1
    assert "no propuso un plan válido" in salida
