"""Alcance de más: justificar o revertir lo que cambió fuera del plan (caso TAREAS-3)."""

from pathlib import Path

import pytest

from harness.alcance import Alcance, EstadoAlcance, revertir
from harness.cambios import CambiosRepo, tomar_instantanea
from harness.config_proyecto import ConfigComandos
from harness.ejecucion import ResultadoEjecucion, ejecutar_ticket
from harness.entrega import crear_cuerpo_pr
from harness.git import leer_de_head
from harness.herramientas import crear_herramientas
from harness.herramientas.archivos import ErrorHerramienta
from harness.plan import Plan
from harness.prompts import PROMPT_SISTEMA
from harness.tipos import LlamadaHerramienta, Mensaje, Rol
from tests.falsos import ProveedorFalso, pedir, terminar
from tests.git_prueba import git, iniciar_repo, requiere_git

MAIN = "app = FastAPI()\n\n\n@app.get('/tareas')\ndef listar(): ...\n"
CON_POST = MAIN + "\n\n@app.post('/tareas')\ndef crear(): ...\n"
PLAN = Plan.model_validate({
    "resumen": "Completar tareas.",
    "archivos": [{"ruta": "src/main.py", "accion": "modificar", "motivo": "endpoint"}],
    "pasos": ["Agregar PATCH"],
    "tests": [],
})


@pytest.fixture
def repo_git(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text(MAIN, encoding="utf-8")
    (tmp_path / "src" / "modelos.py").write_text("class Tarea: ...\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Tareas\n", encoding="utf-8")
    iniciar_repo(tmp_path)
    return tmp_path


def _alcance(repo: Path, estados: list | None = None) -> Alcance:
    def al_resolver(estado: EstadoAlcance) -> None:
        if estados is not None:
            estados.append(estado)

    return Alcance(repo, tomar_instantanea(repo), [], None, al_resolver)


def _escribir(ruta: str, contenido: str, id_llamada: str) -> object:
    argumentos = {"ruta": ruta, "contenido": contenido}
    return pedir(LlamadaHerramienta(id_llamada, "escribir_archivo", argumentos))


def _llamar(nombre: str, id_llamada: str, **argumentos: str) -> object:
    return pedir(LlamadaHerramienta(id_llamada, nombre, argumentos))


def _ejecutar(repo: Path, respuestas: list, alcance: Alcance):
    proveedor = ProveedorFalso(respuestas)
    mensajes = [Mensaje(Rol.USUARIO, "T")]
    continuacion = ResultadoEjecucion(None, 0, 0, 0, False, None, 0, PLAN)
    resultado = ejecutar_ticket(
        proveedor, mensajes, crear_herramientas(repo), 20, validar=lambda: (True, "ok"),
        continuacion=continuacion, alcance=alcance,
    )
    return resultado, mensajes, proveedor


@requiere_git
def test_el_modelo_revierte_lo_que_nadie_pidio_y_justifica_lo_necesario(repo_git: Path) -> None:
    # Como TAREAS-3: TareaCrear en modelos.py para sostener un POST que nadie pidió.
    estados: list[EstadoAlcance] = []
    respuestas = [
        _escribir("src/modelos.py", "class Tarea: ...\nclass TareaCrear: ...\n", "e1"),
        _escribir("README.md", "# Tareas\n\nPATCH /tareas/{id}\n", "e2"),
        _escribir("src/main.py", CON_POST, "e3"),
        terminar("Listo"),
        _llamar("revertir_archivo", "r1", ruta="src/modelos.py"),
        _llamar("justificar_cambio", "j1", ruta="README.md",
                motivo="El criterio 3 pide documentar el endpoint."),
    ]
    resultado, mensajes, proveedor = _ejecutar(repo_git, respuestas, _alcance(repo_git, estados))

    pedido = next(m.contenido for m in mensajes if "no estaban en tu plan" in (m.contenido or ""))
    assert "- README.md\n- src/modelos.py" in pedido
    assert (repo_git / "src" / "modelos.py").read_text(encoding="utf-8") == "class Tarea: ...\n"
    assert resultado.alcance.revertidos == ["src/modelos.py"]
    assert resultado.alcance.justificados == {
        "README.md": "El criterio 3 pide documentar el endpoint."
    }
    assert resultado.alcance.pendientes == [] and estados[-1] is resultado.alcance
    # Las herramientas de la ronda: solo justificar y revertir (y las de lectura).
    assert proveedor.herramientas[4] == ["justificar_cambio", "revertir_archivo"]
    # Presupuesto propio: no descuenta de max_pasos.
    assert resultado.pasos_extra == 2


@requiere_git
def test_un_archivo_nuevo_fuera_del_plan_se_borra_al_revertir(repo_git: Path) -> None:
    respuestas = [
        _escribir("src/extra.py", "x = 1\n", "e1"), terminar(),
        _llamar("revertir_archivo", "r1", ruta="src/extra.py"),
    ]
    resultado, _, _ = _ejecutar(repo_git, respuestas, _alcance(repo_git))
    assert not (repo_git / "src" / "extra.py").exists()
    assert resultado.alcance.revertidos == ["src/extra.py"]


@requiere_git
def test_revertir_solo_toca_archivos_que_cambiaron_en_esta_ejecucion(repo_git: Path) -> None:
    # Cambió antes de la ejecución (sin commitear): está en la foto del principio así.
    (repo_git / "README.md").write_text("# Tareas\n\nnotas del usuario\n", encoding="utf-8")
    alcance = _alcance(repo_git)
    # Durante la ejecución cambia solo main.py.
    (repo_git / "src" / "main.py").write_text(CON_POST, encoding="utf-8")

    for intocable in ("README.md", "src/modelos.py"):
        antes = (repo_git / intocable).read_bytes()
        with pytest.raises(ErrorHerramienta, match="no cambió durante esta ejecución"):
            revertir(alcance, intocable)
        assert (repo_git / intocable).read_bytes() == antes
    revertir(alcance, "src/main.py")
    assert (repo_git / "src" / "main.py").read_text(encoding="utf-8") == MAIN


@requiere_git
def test_no_revierte_si_al_empezar_no_estaba_como_en_head(repo_git: Path) -> None:
    # Cambios del usuario sin commitear antes de la ejecución: volver a HEAD los perdería.
    (repo_git / "README.md").write_text("# Tareas\n\nnotas del usuario\n", encoding="utf-8")
    alcance = _alcance(repo_git)
    (repo_git / "README.md").write_text("# Otra cosa\n", encoding="utf-8")
    with pytest.raises(ErrorHerramienta, match="con seguridad"):
        revertir(alcance, "README.md")
    assert (repo_git / "README.md").read_text(encoding="utf-8") == "# Otra cosa\n"


@requiere_git
def test_las_herramientas_solo_aceptan_los_archivos_fuera_del_plan(repo_git: Path) -> None:
    respuestas = [
        _escribir("src/extra.py", "x = 1\n", "e1"), terminar(),
        _llamar("revertir_archivo", "r1", ruta="src/main.py"),
        _llamar("justificar_cambio", "j1", ruta="src/extra.py", motivo="Lo pide el criterio 1."),
    ]
    resultado, mensajes, _ = _ejecutar(repo_git, respuestas, _alcance(repo_git))
    error = next(m.contenido for m in mensajes if m.id_llamada == "r1")
    assert "no es uno de los archivos fuera del plan: src/extra.py" in error
    assert (repo_git / "src" / "main.py").read_text(encoding="utf-8") == MAIN
    assert resultado.alcance.justificados == {"src/extra.py": "Lo pide el criterio 1."}


@requiere_git
def test_sin_nada_fuera_del_plan_no_hay_ronda(repo_git: Path) -> None:
    respuestas = [_escribir("src/main.py", CON_POST, "e1"), terminar()]
    resultado, _, proveedor = _ejecutar(repo_git, respuestas, _alcance(repo_git))
    assert len(proveedor.conversaciones) == 2 and not resultado.alcance.hay_archivos


@requiere_git
def test_lo_que_queda_sin_resolver_va_como_pendiente(repo_git: Path) -> None:
    respuestas = [_escribir("src/extra.py", "x = 1\n", "e1"), terminar()] + [terminar("no")] * 3
    resultado, _, _ = _ejecutar(repo_git, respuestas, _alcance(repo_git))
    assert resultado.alcance.pendientes == ["src/extra.py"]


def test_sin_git_solo_se_puede_justificar(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    alcance = _alcance(tmp_path)
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
    with pytest.raises(ErrorHerramienta, match="sin Git: justifícalo"):
        revertir(alcance, "a.py")


def test_el_pr_tiene_la_seccion_de_cambios_fuera_del_plan() -> None:
    alcance = EstadoAlcance(
        justificados={"README.md": "Lo pide el criterio 3."}, revertidos=["src/modelos.py"],
        pendientes=["src/extra.py"],
    )
    resultado = ResultadoEjecucion("Listo.", 5, 1, 1, False, True, 1, alcance=alcance)
    cuerpo = crear_cuerpo_pr(resultado, CambiosRepo(), ConfigComandos(tests="pytest"), "m")
    assert (
        "## Cambios fuera del plan\n\n- `README.md`: Lo pide el criterio 3.\n"
        "- `src/modelos.py`: revertido (nadie lo pedía).\n- `src/extra.py`: **sin justificar**."
    ) in cuerpo


def test_el_prompt_pide_no_agregar_de_mas() -> None:
    assert "Implementa solo lo que piden los criterios del ticket" in PROMPT_SISTEMA
    assert "prefiere quitar lo que sobra" in " ".join(PROMPT_SISTEMA.split())


@requiere_git
def test_leer_de_head_devuelve_los_bytes_exactos(repo_git: Path) -> None:
    (repo_git / "crlf.txt").write_bytes(b"a\r\nb\r\n")
    git(repo_git, "add", "crlf.txt")
    git(repo_git, "commit", "--quiet", "-m", "crlf")
    assert leer_de_head(repo_git, "crlf.txt") == b"a\r\nb\r\n"
    assert leer_de_head(repo_git, "no-existe.txt") is None
