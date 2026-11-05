"""harness run guarda la sesión en .harness/sessions/<TICKET>.json para harness resume."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness import cli
from harness.config import ConfigModelo
from harness.proveedores.base import MOTIVO_CAIDO, ErrorProveedorCaido
from harness.sesion import (
    ESTADO_COMPLETADA,
    ESTADO_IMPLEMENTACION,
    MOTIVO_CTRL_C,
    MOTIVO_LIMITE_PASOS,
    MOTIVO_PROVEEDOR,
    Sesion,
    cargar_sesion,
    guardar_sesion,
    ruta_sesion,
)
from harness.tipos import LlamadaHerramienta, RespuestaModelo
from tests.falsos import ProveedorFalso, pedir, terminar

CLAVE_API = "clave-falsa-0123456789"
LEER = LlamadaHerramienta("c1", "leer_archivo", {"ruta": "app.py"})


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Repo sin harness.yaml (sin Docker ni entrega) y un ticket local fuera de él."""
    raiz = tmp_path / "repo"
    raiz.mkdir()
    # Un secreto en un archivo que el modelo lee: nunca debe quedar en la sesión.
    (raiz / "app.py").write_text(f"CLAVE = '{CLAVE_API}'\n", encoding="utf-8")
    (tmp_path / "T-1.md").write_text("# T-1: algo\n", encoding="utf-8")
    return raiz


def _correr(
    monkeypatch: pytest.MonkeyPatch,
    repo: Path,
    respuestas: list[RespuestaModelo | BaseException],
    *opciones: str,
) -> tuple[int, str, ProveedorFalso]:
    config = ConfigModelo("gemini", "modelo-x", None, CLAVE_API)
    proveedor = ProveedorFalso(respuestas)
    monkeypatch.setattr(cli, "cargar_config", lambda: config)
    monkeypatch.setattr(cli, "crear_proveedor", lambda *_: proveedor)
    argumentos = ["run", str(repo.parent / "T-1.md"), "--repo", str(repo), "--sin-plan",
                  *opciones]
    resultado = CliRunner().invoke(cli.app, argumentos)
    return resultado.exit_code, " ".join(resultado.output.split()), proveedor


def test_una_ejecucion_terminada_deja_la_sesion_completada(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    codigo, salida, _ = _correr(monkeypatch, repo, [pedir(LEER), terminar()])
    assert codigo == 0
    sesion = cargar_sesion(repo, "T-1")
    assert sesion.estado == ESTADO_COMPLETADA and sesion.motivo_corte is None
    # Sistema, ticket, la llamada, su resultado y la respuesta final.
    assert sesion.pasos == 2 and len(sesion.mensajes) == 5
    assert "harness resume" not in salida


def test_corte_por_limite_de_pasos(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    codigo, salida, _ = _correr(monkeypatch, repo, [pedir(LEER)], "--max-pasos", "1")
    assert codigo == 1
    sesion = cargar_sesion(repo, "T-1")
    assert (sesion.estado, sesion.motivo_corte) == (ESTADO_IMPLEMENTACION, MOTIVO_LIMITE_PASOS)
    assert sesion.mensajes[-1].id_llamada == "c1"
    assert "Para retomarla: harness resume T-1" in salida


@pytest.mark.parametrize(
    ("error", "motivo"),
    [
        (ErrorProveedorCaido("Gemini no responde (503).", MOTIVO_CAIDO), MOTIVO_PROVEEDOR),
        (KeyboardInterrupt(), MOTIVO_CTRL_C),
    ],
)
def test_corte_por_proveedor_o_ctrl_c(
    monkeypatch: pytest.MonkeyPatch, repo: Path, error: BaseException, motivo: str
) -> None:
    codigo, salida, _ = _correr(monkeypatch, repo, [pedir(LEER), error])
    assert codigo != 0
    sesion = cargar_sesion(repo, "T-1")
    assert sesion.motivo_corte == motivo and sesion.pasos == 1
    assert "Para retomarla: harness resume T-1" in salida


def test_la_sesion_no_guarda_secretos(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    _correr(monkeypatch, repo, [pedir(LEER), terminar()])
    texto = ruta_sesion(repo, "T-1").read_text(encoding="utf-8")
    assert CLAVE_API not in texto and "CLAVE = '****'" in texto


def _sesion_pendiente(repo: Path) -> None:
    sesion = Sesion(
        ticket={"clave": "T-1", "titulo": "algo", "fuente": "local", "url": None},
        estado=ESTADO_IMPLEMENTACION, proveedor="gemini/modelo-x",
        motivo_corte=MOTIVO_LIMITE_PASOS,
    )
    guardar_sesion(repo, sesion, [])


def test_con_una_sesion_pendiente_run_sugiere_resume(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    _sesion_pendiente(repo)
    codigo, salida, proveedor = _correr(monkeypatch, repo, [terminar()])
    assert codigo == 1 and proveedor.conversaciones == []
    assert ("Hay una ejecución sin terminar de T-1 (se cortó por el límite de pasos): "
            "retómala con 'harness resume T-1', o empieza de nuevo con --desde-cero.") in salida


def test_desde_cero_descarta_la_sesion_sin_borrarla(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    _sesion_pendiente(repo)
    codigo, salida, _ = _correr(monkeypatch, repo, [terminar()], "--desde-cero")
    assert codigo == 0
    descartadas = list(ruta_sesion(repo, "T-1").parent.glob("T-1.descartada-*.json"))
    assert len(descartadas) == 1
    assert "Sesión anterior de T-1 descartada" in salida
    assert "bórrala a mano para empezar limpio" in salida
    assert cargar_sesion(repo, "T-1").estado == ESTADO_COMPLETADA
