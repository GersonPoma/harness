from pathlib import Path

import pytest

from harness.config_proyecto import ConfigGit
from harness.ejecucion import ResultadoEjecucion
from harness.entrega import (
    ConfigEntrega,
    cerrar_sin_entregar,
    crear_config_entrega,
    crear_mensaje_commit,
    entregar,
    preparar_rama,
)
from harness.git import ErrorGit
from harness.ticket import titulo_ticket
from tests.git_prueba import (
    RAMA_BASE,
    agregar_remoto,
    git,
    iniciar_repo,
    ramas_remotas,
    requiere_git,
)

pytestmark = requiere_git

TICKET = "T-1"
RAMA = f"feature/{TICKET}"
MENSAJE = "T-1: Crear tareas"


def _resultado(
    es_validado: bool | None = True, es_limite: bool = False, es_rechazado: bool = False
) -> ResultadoEjecucion:
    return ResultadoEjecucion(
        "Listo", 5, 100, 50, es_limite, es_validado, 1, es_plan_rechazado=es_rechazado
    )


def _config(es_con_push: bool = True) -> ConfigEntrega:
    return crear_config_entrega(ConfigGit(), TICKET, es_con_push)


@pytest.fixture
def proyecto(tmp_path: Path) -> Path:
    """Repo Git con un commit en main y un remoto local 'origin' con main subido."""
    repo = tmp_path / "proyecto"
    repo.mkdir()
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    iniciar_repo(repo)
    agregar_remoto(repo, tmp_path / "remoto.git")
    return repo


def _rama_actual(repo: Path) -> str:
    return git(repo, "branch", "--show-current")


def _avisos_al_preparar(repo: Path, config: ConfigEntrega | None = None) -> list[str]:
    avisos: list[str] = []
    preparar_rama(repo, config or _config(), avisos.append)
    return avisos


# --- Al empezar --------------------------------------------------------------------------


def test_crea_la_rama_del_ticket_desde_la_base(proyecto: Path) -> None:
    assert _avisos_al_preparar(proyecto) == []
    assert _rama_actual(proyecto) == RAMA
    assert git(proyecto, "rev-parse", RAMA) == git(proyecto, "rev-parse", RAMA_BASE)


def test_una_carpeta_sin_git_es_error(tmp_path: Path) -> None:
    with pytest.raises(ErrorGit, match="no es un repositorio Git"):
        _avisos_al_preparar(tmp_path)


def test_con_cambios_sin_commitear_es_error(proyecto: Path) -> None:
    (proyecto / "app.py").write_text("x = 2\n", encoding="utf-8")
    (proyecto / "nuevo.py").write_text("", encoding="utf-8")
    with pytest.raises(ErrorGit, match=r"Hay 2 archivo\(s\) sin commitear \(.*app\.py"):
        _avisos_al_preparar(proyecto)
    assert _rama_actual(proyecto) == RAMA_BASE


def test_los_logs_del_harness_no_cuentan_como_cambios(proyecto: Path) -> None:
    # El log de la ejecución se escribe antes de crear la rama (y puede no estar ignorado).
    (proyecto / ".harness" / "logs").mkdir(parents=True)
    (proyecto / ".harness" / "logs" / "T-1.jsonl").write_text("{}", encoding="utf-8")
    _avisos_al_preparar(proyecto)
    assert _rama_actual(proyecto) == RAMA


def test_si_la_rama_ya_existe_es_error(proyecto: Path) -> None:
    git(proyecto, "branch", RAMA)
    with pytest.raises(ErrorGit, match=f"La rama '{RAMA}' ya existe. Bórrala"):
        _avisos_al_preparar(proyecto)


def test_si_la_rama_ya_existe_en_el_remoto_es_error(proyecto: Path) -> None:
    git(proyecto, "push", "--quiet", "origin", f"{RAMA_BASE}:refs/heads/{RAMA}")
    with pytest.raises(ErrorGit, match=f"ya existe en 'origin'"):
        _avisos_al_preparar(proyecto)
    assert _rama_actual(proyecto) == RAMA_BASE


def test_sin_rama_base_es_error(proyecto: Path) -> None:
    config = crear_config_entrega(ConfigGit(rama_base="develop"), TICKET, True)
    with pytest.raises(ErrorGit, match="No existe la rama base 'develop'"):
        _avisos_al_preparar(proyecto, config)


def test_sin_remoto_es_error_salvo_con_sin_push(proyecto: Path) -> None:
    git(proyecto, "remote", "remove", "origin")
    with pytest.raises(ErrorGit, match="No existe el remoto 'origin'.*--sin-push"):
        _avisos_al_preparar(proyecto)
    assert _avisos_al_preparar(proyecto, _config(es_con_push=False)) == []
    assert _rama_actual(proyecto) == RAMA


def test_la_rama_de_trabajo_nunca_es_main(proyecto: Path) -> None:
    config = crear_config_entrega(ConfigGit(prefijo_rama=""), "main", True)
    with pytest.raises(ErrorGit, match="no puede ser 'main'"):
        _avisos_al_preparar(proyecto, config)


def test_avisa_si_la_base_tiene_commits_sin_subir(proyecto: Path) -> None:
    git(proyecto, "commit", "--quiet", "--allow-empty", "-m", "uno")
    git(proyecto, "commit", "--quiet", "--allow-empty", "-m", "dos")
    (aviso,) = _avisos_al_preparar(proyecto)
    assert aviso == (
        "La rama main local tiene 2 commits sin subir a origin/main: entrarían en la rama del "
        "ticket y aparecerían en el PR. Conviene hacer push de main antes."
    )


def test_avisa_si_la_base_esta_atrasada(proyecto: Path, tmp_path: Path) -> None:
    # Otro clon sube un commit: el main local queda 1 atrás después del fetch.
    otro = tmp_path / "otro"
    git(tmp_path, "clone", "--quiet", str(tmp_path / "remoto.git"), str(otro))
    git(otro, "-c", "user.name=Otro", "-c", "user.email=o@example.com",
        "commit", "--quiet", "--allow-empty", "-m", "de otro")
    git(otro, "push", "--quiet", "origin", RAMA_BASE)

    (aviso,) = _avisos_al_preparar(proyecto)
    assert "está 1 commit atrasada respecto de origin/main" in aviso
    assert "(git pull)" in aviso


# --- Al terminar -------------------------------------------------------------------------


def _preparar_y_cambiar(proyecto: Path, config: ConfigEntrega | None = None) -> ConfigEntrega:
    config = config or _config()
    preparar_rama(proyecto, config, lambda _: None)
    (proyecto / "app.py").write_text("x = 2\n", encoding="utf-8")
    (proyecto / "tareas.py").write_text("TAREAS = []\n", encoding="utf-8")
    return config


def test_con_la_validacion_ok_hace_commit_y_push(proyecto: Path, tmp_path: Path) -> None:
    config = _preparar_y_cambiar(proyecto)
    (proyecto / ".harness" / "logs").mkdir(parents=True)
    (proyecto / ".harness" / "logs" / "T-1.jsonl").write_text("{}", encoding="utf-8")

    entrega = entregar(proyecto, config, _resultado(), MENSAJE)

    assert entrega.hash_commit == git(proyecto, "rev-parse", "--short", "HEAD")
    assert entrega.archivos == ["app.py", "tareas.py"]
    assert entrega.es_pusheado and entrega.motivo is None
    # Una sola línea, sin cuerpo.
    assert git(proyecto, "log", "-1", "--format=%B") == MENSAJE
    assert RAMA in ramas_remotas(tmp_path / "remoto.git")
    # El log del harness no entra al commit.
    assert ".harness/logs/T-1.jsonl" not in git(proyecto, "show", "--name-only", "HEAD")


def test_sin_push_el_commit_queda_local(proyecto: Path, tmp_path: Path) -> None:
    config = _preparar_y_cambiar(proyecto, _config(es_con_push=False))
    entrega = entregar(proyecto, config, _resultado(), MENSAJE)
    assert entrega.hash_commit and not entrega.es_pusheado
    assert "--sin-push" in entrega.motivo
    assert RAMA not in ramas_remotas(tmp_path / "remoto.git")


@pytest.mark.parametrize(
    ("resultado", "motivo"),
    [
        (_resultado(es_validado=False), "La validación no pasó: sin commit ni push."),
        (_resultado(es_limite=True), "Se alcanzó el límite de pasos: sin commit ni push."),
        (_resultado(es_validado=None), "La validación no pasó: sin commit ni push."),
    ],
)
def test_sin_validacion_ok_no_hay_commit_y_los_cambios_quedan_en_la_rama(
    proyecto: Path, tmp_path: Path, resultado: ResultadoEjecucion, motivo: str
) -> None:
    config = _preparar_y_cambiar(proyecto)
    entrega = entregar(proyecto, config, resultado, MENSAJE)

    assert entrega.hash_commit is None and not entrega.es_pusheado
    assert entrega.motivo.startswith(motivo)
    assert f"quedaron sin commitear en la rama {RAMA}" in entrega.motivo
    assert _rama_actual(proyecto) == RAMA
    assert (proyecto / "tareas.py").exists()
    assert RAMA not in ramas_remotas(tmp_path / "remoto.git")


def test_sin_cambios_se_borra_la_rama(proyecto: Path) -> None:
    config = _config()
    preparar_rama(proyecto, config, lambda _: None)
    entrega = entregar(proyecto, config, _resultado(), MENSAJE)

    assert entrega.hash_commit is None and entrega.es_rama_borrada
    assert entrega.motivo == (
        f"El modelo no cambió ningún archivo. Se borró la rama {RAMA}, que quedó vacía."
    )
    assert _rama_actual(proyecto) == RAMA_BASE
    assert RAMA not in git(proyecto, "branch", "--list", RAMA)


def test_plan_rechazado_borra_la_rama(proyecto: Path) -> None:
    config = _config()
    preparar_rama(proyecto, config, lambda _: None)
    entrega = entregar(proyecto, config, _resultado(es_rechazado=True), MENSAJE)
    assert entrega.es_rama_borrada and _rama_actual(proyecto) == RAMA_BASE


def test_no_commitea_archivos_de_secretos(proyecto: Path) -> None:
    config = _preparar_y_cambiar(proyecto)
    (proyecto / ".env").write_text("API_KEY=real\n", encoding="utf-8")

    with pytest.raises(ErrorGit, match=r"pueden tener secretos \(\.env\)"):
        entregar(proyecto, config, _resultado(), MENSAJE)
    assert git(proyecto, "diff", "--cached", "--name-only") == ""
    assert git(proyecto, "rev-parse", RAMA) == git(proyecto, "rev-parse", RAMA_BASE)


def test_despues_de_un_error_se_borra_la_rama_si_quedo_vacia(proyecto: Path) -> None:
    config = _config()
    preparar_rama(proyecto, config, lambda _: None)
    entrega = cerrar_sin_entregar(proyecto, config)
    assert entrega.es_rama_borrada and _rama_actual(proyecto) == RAMA_BASE


def test_despues_de_un_error_con_cambios_la_rama_queda(proyecto: Path) -> None:
    config = _preparar_y_cambiar(proyecto)
    entrega = cerrar_sin_entregar(proyecto, config)
    assert not entrega.es_rama_borrada and _rama_actual(proyecto) == RAMA
    assert entrega.motivo.startswith("La ejecución no terminó. Los cambios quedaron")


# --- Mensaje del commit ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "titulo"),
    [
        ("# T-1: Crear tareas\n\nDescripción", "Crear tareas"),
        ("\n\n## t-1:   Crear tareas  \n", "Crear tareas"),
        ("# Crear tareas\n", "Crear tareas"),
        ("Crear tareas sin encabezado", "Crear tareas sin encabezado"),
        ("# T-1:\n", ""),
    ],
)
def test_titulo_del_ticket(texto: str, titulo: str) -> None:
    assert titulo_ticket(texto, TICKET) == titulo


def test_mensaje_del_commit() -> None:
    assert crear_mensaje_commit(TICKET, "Crear tareas") == MENSAJE
    assert crear_mensaje_commit(TICKET, "") == TICKET
