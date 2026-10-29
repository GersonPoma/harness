from pathlib import Path

import pytest

from harness import entrega as modulo_entrega
from harness.cambios import CambiosRepo
from harness.config_proyecto import ConfigComandos, ConfigGit
from harness.ejecucion import ResultadoEjecucion
from harness.entrega import (
    ConfigEntrega,
    cerrar_sin_entregar,
    crear_config_entrega,
    crear_cuerpo_pr,
    crear_mensaje_commit,
    entregar,
    preparar_rama,
    verificar_github,
)
from harness.git import ErrorGit
from harness.github import (
    PISTA_PERMISOS,
    ErrorGitHub,
    PedidoPullRequest,
    PullRequest,
    RepoGitHub,
)
from harness.plan import Plan
from harness.ticket import titulo_ticket
from tests.falsos import PLAN_SIMPLE
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


def _subir_commit_desde_otro_clon(tmp_path: Path) -> None:
    """Otro clon sube un commit: el main local queda 1 atrás después del fetch."""
    otro = tmp_path / "otro"
    git(tmp_path, "clone", "--quiet", str(tmp_path / "remoto.git"), str(otro))
    git(otro, "-c", "user.name=Otro", "-c", "user.email=o@example.com",
        "commit", "--quiet", "--allow-empty", "-m", "de otro")
    git(otro, "push", "--quiet", "origin", RAMA_BASE)


@pytest.mark.parametrize("es_con_push", [True, False])
def test_avisa_si_la_base_tiene_commits_sin_subir(proyecto: Path, es_con_push: bool) -> None:
    git(proyecto, "commit", "--quiet", "--allow-empty", "-m", "uno")
    git(proyecto, "commit", "--quiet", "--allow-empty", "-m", "dos")
    (aviso,) = _avisos_al_preparar(proyecto, _config(es_con_push))
    assert aviso == (
        "La rama main local está 2 commits adelante y 0 commits atrás de origin/main. Los de "
        "adelante entrarían en la rama del ticket y aparecerían en el PR: conviene hacer push "
        "de main antes."
    )


@pytest.mark.parametrize("es_con_push", [True, False])
def test_avisa_si_la_base_esta_atrasada(
    proyecto: Path, tmp_path: Path, es_con_push: bool
) -> None:
    _subir_commit_desde_otro_clon(tmp_path)
    (aviso,) = _avisos_al_preparar(proyecto, _config(es_con_push))
    assert aviso == (
        "La rama main local está 0 commits adelante y 1 commit atrás de origin/main. La rama "
        "del ticket sale sin los de atrás: conviene actualizarla (git pull) antes."
    )


def test_avisa_adelante_y_atras_en_un_solo_aviso(proyecto: Path, tmp_path: Path) -> None:
    _subir_commit_desde_otro_clon(tmp_path)
    git(proyecto, "commit", "--quiet", "--allow-empty", "-m", "local")
    (aviso,) = _avisos_al_preparar(proyecto)
    assert aviso.startswith("La rama main local está 1 commit adelante y 1 commit atrás")
    assert "hacer push de main" in aviso and "(git pull)" in aviso


def test_sin_push_y_sin_poder_comparar_avisa_y_sigue(proyecto: Path, tmp_path: Path) -> None:
    git(proyecto, "remote", "set-url", "origin", str(tmp_path / "no-existe.git"))
    (aviso,) = _avisos_al_preparar(proyecto, _config(es_con_push=False))
    assert aviso.startswith("No se pudo comparar main con el remoto: 'git fetch")
    assert _rama_actual(proyecto) == RAMA


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


@pytest.mark.parametrize("es_ignorado", [True, False])
def test_los_logs_del_harness_no_entran_al_commit_esten_o_no_ignorados(
    tmp_path: Path, es_ignorado: bool
) -> None:
    # Lo normal es que el .gitignore del proyecto ya los ignore: git add no debe fallar.
    repo = tmp_path / "proyecto"
    repo.mkdir()
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    if es_ignorado:
        (repo / ".gitignore").write_text(
            ".harness/logs/\n.harness/sessions/\n", encoding="utf-8"
        )
    iniciar_repo(repo)
    config = _preparar_y_cambiar(repo, _config(es_con_push=False))
    for carpeta in ("logs", "sessions"):
        (repo / ".harness" / carpeta).mkdir(parents=True)
        (repo / ".harness" / carpeta / "T-1.json").write_text("{}", encoding="utf-8")

    entrega = entregar(repo, config, _resultado(), MENSAJE)

    assert entrega.hash_commit is not None
    assert entrega.archivos == ["app.py", "tareas.py"]
    assert git(repo, "show", "--name-only", "--format=", "HEAD").splitlines() == [
        "app.py",
        "tareas.py",
    ]


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


# --- Pull request ------------------------------------------------------------------------

URL_GITHUB = "https://github.com/gerson/tareas-api.git"
URL_PR = "https://github.com/gerson/tareas-api/pull/7"


def _con_remoto_de_github(proyecto: Path, tmp_path: Path, url: str = URL_GITHUB) -> None:
    # La URL dice GitHub, pero el push sigue yendo al remoto local.
    git(proyecto, "remote", "set-url", "origin", url)
    git(proyecto, "remote", "set-url", "--push", "origin", str(tmp_path / "remoto.git"))


def _armar_pedido(repo_github: RepoGitHub) -> PedidoPullRequest:
    return PedidoPullRequest(repo_github, RAMA, RAMA_BASE, MENSAJE, "cuerpo")


@pytest.fixture
def pedidos_pr(monkeypatch: pytest.MonkeyPatch) -> list[tuple[PedidoPullRequest, str]]:
    """Reemplaza la API de GitHub: anota cada pedido y devuelve un PR en borrador."""
    pedidos: list[tuple[PedidoPullRequest, str]] = []

    def crear_falso(pedido: PedidoPullRequest, token: str) -> PullRequest:
        pedidos.append((pedido, token))
        return PullRequest(URL_PR, es_borrador=True)

    monkeypatch.setattr(modulo_entrega.github, "crear_pull_request", crear_falso)
    return pedidos


def test_despues_del_push_abre_el_pr(
    proyecto: Path, tmp_path: Path, pedidos_pr: list
) -> None:
    config = _preparar_y_cambiar(proyecto)
    _con_remoto_de_github(proyecto, tmp_path)
    entrega = entregar(proyecto, config, _resultado(), MENSAJE, _armar_pedido, "token")

    assert entrega.es_pusheado and entrega.pull_request == PullRequest(URL_PR, True)
    ((pedido, token),) = pedidos_pr
    assert pedido.repo == RepoGitHub("gerson", "tareas-api") and token == "token"
    assert (pedido.rama, pedido.base, pedido.titulo) == (RAMA, RAMA_BASE, MENSAJE)


def test_sin_token_hace_push_pero_no_pr(
    proyecto: Path, tmp_path: Path, pedidos_pr: list
) -> None:
    config = _preparar_y_cambiar(proyecto)
    _con_remoto_de_github(proyecto, tmp_path)
    entrega = entregar(proyecto, config, _resultado(), MENSAJE, _armar_pedido, None)

    assert entrega.es_pusheado and entrega.pull_request is None
    assert entrega.motivo == "Falta GITHUB_TOKEN en el .env: se hizo push, pero no el pull request."
    assert pedidos_pr == []


def test_con_un_remoto_que_no_es_de_github_no_hay_pr(proyecto: Path, pedidos_pr: list) -> None:
    config = _preparar_y_cambiar(proyecto)
    entrega = entregar(proyecto, config, _resultado(), MENSAJE, _armar_pedido, "token")
    assert entrega.es_pusheado and "no es de GitHub" in entrega.motivo
    assert pedidos_pr == []


def test_sin_pr_no_llama_a_github(proyecto: Path, tmp_path: Path, pedidos_pr: list) -> None:
    config = crear_config_entrega(ConfigGit(), TICKET, es_con_push=True, es_con_pr=False)
    _preparar_y_cambiar(proyecto, config)
    _con_remoto_de_github(proyecto, tmp_path)
    entrega = entregar(proyecto, config, _resultado(), MENSAJE, _armar_pedido, "token")
    assert entrega.es_pusheado and entrega.pull_request is None and pedidos_pr == []
    assert entrega.motivo == "Sin PR (--sin-pr): se hizo el push, pero no el pull request."
    assert entrega.url_pr_a_mano == (
        "https://github.com/gerson/tareas-api/compare/main...feature/T-1?expand=1"
    )
    assert entrega.ruta_pr_pendiente == proyecto / ".harness" / "logs" / "T-1-pr.md"
    assert entrega.ruta_pr_pendiente.read_text(encoding="utf-8") == f"# {MENSAJE}\n\ncuerpo\n"


def test_sin_pr_y_sin_github_avisa_sin_link(proyecto: Path) -> None:
    config = crear_config_entrega(ConfigGit(), TICKET, es_con_push=True, es_con_pr=False)
    _preparar_y_cambiar(proyecto, config)
    entrega = entregar(proyecto, config, _resultado(), MENSAJE, _armar_pedido, "token")
    assert entrega.motivo.startswith("Sin PR (--sin-pr)")
    assert entrega.url_pr_a_mano is None and entrega.ruta_pr_pendiente is None


def test_sin_push_tampoco_hay_pr() -> None:
    config = crear_config_entrega(ConfigGit(), TICKET, es_con_push=False, es_con_pr=True)
    assert not config.es_con_pr


def _falla_el_pr(monkeypatch: pytest.MonkeyPatch, mensaje: str, codigo: int) -> None:
    def falla(pedido: PedidoPullRequest, token: str) -> PullRequest:
        raise ErrorGitHub(mensaje, codigo)

    monkeypatch.setattr(modulo_entrega.github, "crear_pull_request", falla)


@pytest.mark.parametrize(
    "url",
    [URL_GITHUB, "git@github.com:gerson/tareas-api.git", "ssh://git@github.com/gerson/tareas-api"],
)
def test_si_github_falla_deja_el_link_y_la_descripcion_para_crearlo_a_mano(
    proyecto: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    _falla_el_pr(monkeypatch, "GitHub no creó el pull request: 401 Bad credentials", 401)
    config = _preparar_y_cambiar(proyecto)
    _con_remoto_de_github(proyecto, tmp_path, url)
    entrega = entregar(proyecto, config, _resultado(), MENSAJE, _armar_pedido, "token")

    assert entrega.es_pusheado and entrega.pull_request is None
    assert RAMA in ramas_remotas(tmp_path / "remoto.git")
    # Sin la pista de permisos: un 401 es el token, no sus permisos.
    assert entrega.error_pr == "GitHub no creó el pull request: 401 Bad credentials"
    assert entrega.url_pr_a_mano == (
        "https://github.com/gerson/tareas-api/compare/main...feature/T-1?expand=1"
    )
    assert entrega.ruta_pr_pendiente == proyecto / ".harness" / "logs" / "T-1-pr.md"
    assert entrega.ruta_pr_pendiente.read_text(encoding="utf-8") == f"# {MENSAJE}\n\ncuerpo\n"


def test_si_el_pr_falla_con_422_agrega_la_pista_de_permisos(
    proyecto: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = "GitHub no creó el pull request: 422 Validation Failed; not all refs are readable"
    _falla_el_pr(monkeypatch, error, 422)
    config = _preparar_y_cambiar(proyecto)
    _con_remoto_de_github(proyecto, tmp_path)
    entrega = entregar(proyecto, config, _resultado(), MENSAJE, _armar_pedido, "token")

    assert entrega.error_pr == f"{error} {PISTA_PERMISOS}"
    assert entrega.url_pr_a_mano and entrega.ruta_pr_pendiente.exists()


def test_con_el_pr_creado_no_queda_descripcion_pendiente(
    proyecto: Path, tmp_path: Path, pedidos_pr: list
) -> None:
    config = _preparar_y_cambiar(proyecto)
    _con_remoto_de_github(proyecto, tmp_path)
    entrega = entregar(proyecto, config, _resultado(), MENSAJE, _armar_pedido, "token")
    assert entrega.url_pr_a_mano is None and entrega.ruta_pr_pendiente is None
    assert not (proyecto / ".harness" / "logs" / "T-1-pr.md").exists()


def test_cuerpo_del_pr() -> None:
    resultado = ResultadoEjecucion(
        "Agregué el endpoint.", 9, 100, 50, False, True, 2, plan=Plan.model_validate(PLAN_SIMPLE)
    )
    cambios = CambiosRepo(creados=["nuevo.py"], modificados=["app.py"])
    comandos = ConfigComandos(tests="pytest -q", lint="ruff check .")

    cuerpo = crear_cuerpo_pr(resultado, cambios, comandos, "gemini/modelo-x")

    assert cuerpo == (
        "## Resumen\n\nAgregué el endpoint.\n\n"
        "## Plan\n\nAgregar el módulo nuevo.\n\n**Archivos**\n"
        "- `nuevo.py` (crear): Lo pide el ticket.\n\n**Pasos**\n1. Crear nuevo.py\n\n"
        "## Validación\n\nPasaron los tests (`pytest -q`) y el lint (`ruff check .`) en la "
        "validación 2.\n\n"
        "## Cambios\n\n- Creados: `nuevo.py`\n- Modificados: `app.py`\n\n"
        "---\n_Generado por harness con `gemini/modelo-x`. Revísalo antes de aprobarlo._"
    )


def test_cuerpo_del_pr_sin_plan_ni_resumen() -> None:
    resultado = ResultadoEjecucion(None, 3, 10, 5, False, True, 1)
    cuerpo = crear_cuerpo_pr(resultado, CambiosRepo(), ConfigComandos(tests="mvn test"), "m")
    assert "_El modelo no dejó un resumen._" in cuerpo
    assert "_Se implementó sin plan (--sin-plan)._" in cuerpo
    assert "Pasaron los tests (`mvn test`) en la validación 1." in cuerpo


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


# --- Verificación de GitHub al empezar ---------------------------------------------------


def test_verificar_github_con_un_remoto_que_no_es_de_github_no_pide_nada(
    proyecto: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llamadas: list = []
    monkeypatch.setattr(
        modulo_entrega.github, "verificar_acceso", lambda *argumentos: llamadas.append(argumentos)
    )
    assert verificar_github(proyecto, _config(), "tok") is None
    assert llamadas == []


def test_verificar_github_usa_el_repo_del_remoto_y_la_base(
    proyecto: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llamadas: list = []
    git(proyecto, "remote", "set-url", "origin", "git@github.com:gerson/tareas-api.git")
    monkeypatch.setattr(
        modulo_entrega.github, "verificar_acceso", lambda *argumentos: llamadas.append(argumentos)
    )
    repo_github = verificar_github(proyecto, _config(), "tok")
    assert repo_github == RepoGitHub("gerson", "tareas-api")
    assert llamadas == [(repo_github, "main", "tok")]
