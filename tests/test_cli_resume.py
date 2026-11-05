"""harness resume <TICKET>: retoma una ejecución cortada desde su sesión."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness import cli
from harness.cambios import CambiosRepo
from harness.config import ConfigModelo
from harness.ejecucion import ResultadoEjecucion
from harness.sesion import (
    ESTADO_COMPLETADA,
    ESTADO_IMPLEMENTACION,
    ESTADO_PLAN,
    MOTIVO_LIMITE_PASOS,
    Sesion,
    cargar_sesion,
    guardar_sesion,
)
from harness.tipos import LlamadaHerramienta, Rol
from tests.falsos import ProveedorFalso, pedir, proponer, terminar
from tests.git_prueba import git, iniciar_repo, requiere_git

LEER = LlamadaHerramienta("c1", "leer_archivo", {"ruta": "app.py"})
CONFIG = ConfigModelo("gemini", "modelo-x", None, "clave-falsa-0123456789")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Repo sin harness.yaml (sin Docker ni entrega) y un ticket local fuera de él."""
    raiz = tmp_path / "repo"
    raiz.mkdir()
    (raiz / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "T-1.md").write_text("# T-1: Crear tareas\n\nHay que crear tareas.\n",
                                     encoding="utf-8")
    return raiz


def _invocar(
    monkeypatch: pytest.MonkeyPatch, argumentos: list[str], proveedor: ProveedorFalso
) -> tuple[int, str, list]:
    configs = []

    def crear(config, *_):
        configs.append(config)
        return proveedor

    monkeypatch.setattr(cli, "cargar_config", lambda: CONFIG)
    monkeypatch.setattr(cli, "crear_proveedor", crear)
    resultado = CliRunner().invoke(cli.app, argumentos)
    return resultado.exit_code, " ".join(resultado.output.split()), configs


def _run(monkeypatch: pytest.MonkeyPatch, repo: Path, respuestas: list, *opciones: str) -> int:
    argumentos = ["run", str(repo.parent / "T-1.md"), "--repo", str(repo), *opciones]
    return _invocar(monkeypatch, argumentos, ProveedorFalso(respuestas))[0]


def _resume(
    monkeypatch: pytest.MonkeyPatch, repo: Path, respuestas: list, *opciones: str
) -> tuple[int, str, ProveedorFalso, list]:
    proveedor = ProveedorFalso(respuestas)
    argumentos = ["resume", "T-1", "--repo", str(repo), *opciones]
    codigo, salida, configs = _invocar(monkeypatch, argumentos, proveedor)
    return codigo, salida, proveedor, configs


def _eventos(repo: Path) -> list[dict]:
    ultimo = sorted((repo / ".harness" / "logs").glob("T-1-*.jsonl"))[-1]
    return [json.loads(linea) for linea in ultimo.read_text(encoding="utf-8").splitlines()]


def test_retoma_un_corte_por_limite_y_termina(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    assert _run(monkeypatch, repo, [pedir(LEER)], "--sin-plan", "--max-pasos", "1") == 1

    codigo, salida, proveedor, _ = _resume(monkeypatch, repo, [terminar("Listo")])
    assert codigo == 0, salida
    assert "Se retoma T-1: se cortó por el límite de pasos, después de 1 paso." in salida
    (conversacion,) = proveedor.conversaciones
    # El system prompt de hoy, el ticket y lo hecho; la nota de retomar va en el último
    # resultado de herramienta (no como mensaje aparte entre resultados y respuesta).
    assert conversacion[0].rol == Rol.SISTEMA
    assert "Hay que crear tareas." in conversacion[1].contenido
    assert conversacion[-1].rol == Rol.HERRAMIENTA
    nota = "La ejecución se cortó (se cortó por el límite de pasos)"
    assert nota in conversacion[-1].contenido
    sesion = cargar_sesion(repo, "T-1")
    assert sesion.estado == ESTADO_COMPLETADA and sesion.pasos == 2
    assert "harness resume" not in salida.split("Se retoma")[1]
    resume = next(e for e in _eventos(repo) if e["evento"] == "resume")
    assert resume["motivo_corte"] == MOTIVO_LIMITE_PASOS


def test_otro_corte_al_retomar_se_puede_retomar_otra_vez(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    _run(monkeypatch, repo, [pedir(LEER)], "--sin-plan", "--max-pasos", "1")
    codigo, salida, _, _ = _resume(monkeypatch, repo, [pedir(LEER)], "--max-pasos", "1")
    assert codigo == 1 and "Para retomarla: harness resume T-1" in salida
    sesion = cargar_sesion(repo, "T-1")
    # Los pasos siguen contando: 1 de la primera parte y 1 de la segunda.
    assert (sesion.pasos, sesion.motivo_corte) == (2, MOTIVO_LIMITE_PASOS)


def test_avisa_lo_que_cambio_desde_el_corte(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    _run(monkeypatch, repo, [pedir(LEER)], "--sin-plan", "--max-pasos", "1")
    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")

    codigo, salida, proveedor, _ = _resume(monkeypatch, repo, [terminar()])
    assert codigo == 0
    assert "Desde el corte cambiaron app.py: el modelo lo sabrá al retomar." in salida
    assert "Desde el corte cambiaron estos archivos" in proveedor.conversaciones[0][-1].contenido
    resume = next(e for e in _eventos(repo) if e["evento"] == "resume")
    assert resume["cambios_desde_el_corte"] == ["app.py"]


def test_con_otro_proveedor_y_modelo(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "clave-openrouter-0123456789")
    _run(monkeypatch, repo, [pedir(LEER)], "--sin-plan", "--max-pasos", "1")
    codigo, _, _, configs = _resume(
        monkeypatch, repo, [terminar()], "--proveedor", "openrouter", "--modelo", "qwen:free"
    )
    assert codigo == 0
    assert (configs[0].proveedor, configs[0].modelo) == ("openrouter", "qwen:free")
    sesion = cargar_sesion(repo, "T-1")
    assert sesion.modelos_usados == ["gemini/modelo-x", "openrouter/qwen:free"]


def test_proveedor_sin_modelo_es_error(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    _run(monkeypatch, repo, [pedir(LEER)], "--sin-plan", "--max-pasos", "1")
    codigo, salida, _, _ = _resume(monkeypatch, repo, [], "--proveedor", "groq")
    assert codigo == 1 and "Con --proveedor hace falta también --modelo." in salida


def test_cortada_en_el_plan_se_planifica_de_nuevo(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    # Con --max-pasos 2 la ronda de plan tiene 1 paso: el modelo lee y se corta sin plan.
    _run(monkeypatch, repo, [pedir(LEER), RuntimeError("no debería llegar")], "--max-pasos", "2")
    sesion = cargar_sesion(repo, "T-1")
    assert sesion.estado == ESTADO_PLAN

    codigo, salida, proveedor, _ = _resume(monkeypatch, repo, [proponer(), terminar()])
    assert codigo == 0, salida
    # Empieza de nuevo: el ticket está, y la lectura de la primera vez no.
    primera = proveedor.conversaciones[0]
    assert "Hay que crear tareas." in primera[1].contenido
    assert not any(m.rol == Rol.HERRAMIENTA or m.llamadas for m in primera)


def test_sin_sesion_o_completada(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    codigo, salida, _, _ = _resume(monkeypatch, repo, [])
    assert codigo == 1 and "No hay una ejecución de T-1 para retomar" in salida
    _run(monkeypatch, repo, [terminar()], "--sin-plan")
    codigo, salida, proveedor, _ = _resume(monkeypatch, repo, [])
    assert codigo == 0 and "T-1 ya está completa" in salida
    assert proveedor.conversaciones == []


# --- Con Git y harness.yaml: la rama ------------------------------------------------------

YAML = "version: 1\nproyecto: demo\ndocker:\n  imagen: python:3.12-slim\ncomandos:\n  tests: x\n"


def _sesion_con_rama(repo: Path) -> None:
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


@pytest.fixture
def repo_git(repo: Path) -> Path:
    (repo / "harness.yaml").write_text(YAML, encoding="utf-8")
    iniciar_repo(repo)
    return repo


def _ejecutar_falso(monkeypatch: pytest.MonkeyPatch, recibidos: list) -> None:
    def ejecutar(prep, max_pasos, reporte):
        recibidos.append((max_pasos, prep.reanudacion.continuacion.pasos))
        (prep.repo / "tareas.py").write_text("TAREAS = []\n", encoding="utf-8")
        resultado = ResultadoEjecucion("Hecho", 5, 10, 5, False, True, 1)
        return resultado, CambiosRepo(creados=["tareas.py"])

    monkeypatch.setattr(cli, "_ejecutar", ejecutar)


@requiere_git
def test_en_otra_rama_no_cambia_sola(monkeypatch: pytest.MonkeyPatch, repo_git: Path) -> None:
    git(repo_git, "branch", "feature/T-1")
    _sesion_con_rama(repo_git)
    codigo, salida, _, _ = _resume(monkeypatch, repo_git, [])
    assert codigo == 1
    assert ("El repo está en 'main': cámbiate a la rama del ticket "
            "(git switch feature/T-1)") in salida
    assert git(repo_git, "branch", "--show-current") == "main"


@requiere_git
def test_retoma_con_cambios_sin_commitear_y_hace_el_commit(
    monkeypatch: pytest.MonkeyPatch, repo_git: Path
) -> None:
    git(repo_git, "switch", "--quiet", "-c", "feature/T-1")
    (repo_git / "a_medias.py").write_text("y = 1\n", encoding="utf-8")
    _sesion_con_rama(repo_git)
    recibidos: list = []
    _ejecutar_falso(monkeypatch, recibidos)

    codigo, salida, _, _ = _resume(monkeypatch, repo_git, [], "--max-pasos", "10")
    assert codigo == 0, salida
    # El presupuesto es para esta parte: los 3 pasos hechos más 10.
    assert recibidos == [(13, 3)]
    assert "Se sigue en la rama feature/T-1." in salida
    assert git(repo_git, "log", "-1", "--format=%s") == "T-1: Crear tareas"
    assert cargar_sesion(repo_git, "T-1").estado == ESTADO_COMPLETADA


@requiere_git
def test_si_la_rama_se_borro_porque_quedo_vacia_la_crea(
    monkeypatch: pytest.MonkeyPatch, repo_git: Path
) -> None:
    _sesion_con_rama(repo_git)
    _ejecutar_falso(monkeypatch, [])
    codigo, salida, _, _ = _resume(monkeypatch, repo_git, [])
    assert codigo == 0, salida
    assert git(repo_git, "branch", "--show-current") == "feature/T-1"
