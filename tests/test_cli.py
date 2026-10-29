import json
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness import cli
from harness.config import ConfigModelo
from harness.entorno_docker import ErrorDocker
from harness.planificacion import calcular_pasos_plan
from harness.tipos import LlamadaHerramienta, RespuestaModelo
from harness.cambios import CambiosRepo
from harness.ejecucion import ResultadoEjecucion
from harness.github import ErrorGitHub, PedidoPullRequest, PullRequest
from tests.falsos import ProveedorFalso, pedir, proponer, terminar, vacia
from tests.git_prueba import agregar_remoto, git, iniciar_repo, ramas_remotas, requiere_git


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


YAML_CON_DOCKER = """\
version: 1
proyecto: demo
docker:
  imagen: python:3.12-slim
comandos:
  tests: pytest -q
"""


def _con_requiere_agents(
    monkeypatch: pytest.MonkeyPatch, repo: Path, valor: str, agents_md: str | None
) -> list[str]:
    """Escribe harness.yaml con la clave y cambia Docker por uno que solo anota si se usó."""
    yaml = YAML_CON_DOCKER + f"requiere_agents_md: {valor}\n"
    (repo / "harness.yaml").write_text(yaml, encoding="utf-8")
    if agents_md is not None:
        (repo / "AGENTS.md").write_text(agents_md, encoding="utf-8")
    # Con harness.yaml hay entrega con Git: el repo tiene que serlo (sin remoto: --sin-push).
    iniciar_repo(repo)
    usos: list[str] = []

    def docker_falso(*_args: object) -> None:
        usos.append("docker")
        raise ErrorDocker("Docker falso")

    monkeypatch.setattr(cli, "EntornoDocker", docker_falso)
    return usos


@pytest.mark.parametrize("agents_md", [None, "", " \n\n"])
def test_requiere_agents_md_sin_agents_md_se_detiene(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path, agents_md: str | None
) -> None:
    usos = _con_requiere_agents(monkeypatch, repo_sin_yaml, "true", agents_md)
    proveedor = ProveedorFalso([terminar()])
    codigo, salida = _correr(monkeypatch, repo_sin_yaml, proveedor)
    assert codigo == 1
    assert cli.MENSAJE_REQUIERE_AGENTS in " ".join(salida.split())
    assert usos == []
    assert proveedor.conversaciones == []
    assert not (repo_sin_yaml / ".harness").exists()


def test_requiere_agents_md_con_agents_md_sigue(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path
) -> None:
    usos = _con_requiere_agents(monkeypatch, repo_sin_yaml, "true", "# demo\n")
    codigo, salida = _correr(monkeypatch, repo_sin_yaml, [terminar()], "--sin-push")
    assert usos == ["docker"]
    assert "Docker falso" in salida and codigo == 1


def test_sin_requiere_agents_md_avisa_y_sigue(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path
) -> None:
    usos = _con_requiere_agents(monkeypatch, repo_sin_yaml, "false", None)
    _, salida = _correr(monkeypatch, repo_sin_yaml, [terminar()], "--sin-push")
    assert "El repo no tiene AGENTS.md" in salida
    assert usos == ["docker"]


def test_max_pasos_plan_de_harness_yaml_llega_a_la_planificacion(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path
) -> None:
    yaml = YAML_CON_DOCKER + "limites:\n  max_pasos: 20\n  max_pasos_plan: 4\n"
    (repo_sin_yaml / "harness.yaml").write_text(yaml, encoding="utf-8")
    iniciar_repo(repo_sin_yaml)
    recibidas: list[int] = []

    def ejecutar_falso(prep, max_pasos, reporte):
        recibidas.append(cli._planificacion(prep, reporte).max_pasos_plan)
        raise ErrorDocker("fin de la prueba")

    monkeypatch.setattr(cli, "_ejecutar", ejecutar_falso)
    _correr(monkeypatch, repo_sin_yaml, [], "--sin-push")
    assert recibidas == [4]


def _correr_con_limites(
    monkeypatch: pytest.MonkeyPatch, repo: Path, limites: str, *opciones: str
) -> tuple[list[int], str]:
    """Corre con harness.yaml y anota el presupuesto de plan que llega a la ejecución."""
    (repo / "harness.yaml").write_text(YAML_CON_DOCKER + limites, encoding="utf-8")
    iniciar_repo(repo)
    presupuestos: list[int] = []

    def ejecutar_falso(prep, max_pasos, reporte):
        planificacion = cli._planificacion(prep, reporte)
        if planificacion:
            presupuestos.append(calcular_pasos_plan(max_pasos, planificacion.max_pasos_plan))
        raise ErrorDocker("fin de la prueba")

    monkeypatch.setattr(cli, "_ejecutar", ejecutar_falso)
    _, salida = _correr(monkeypatch, repo, [], "--sin-push", *opciones)
    return presupuestos, " ".join(salida.split())


def test_max_pasos_menor_recorta_el_plan_y_avisa(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path
) -> None:
    limites = "limites:\n  max_pasos: 40\n  max_pasos_plan: 20\n"
    presupuestos, salida = _correr_con_limites(
        monkeypatch, repo_sin_yaml, limites, "--max-pasos", "15"
    )
    assert presupuestos == [7]
    assert "Pasos de planificación recortados de 20 a 7 por --max-pasos 15." in salida
    avisos = [e["mensaje"] for e in _eventos_del_log(repo_sin_yaml) if e["evento"] == "aviso"]
    assert avisos == ["Pasos de planificación recortados de 20 a 7 por --max-pasos 15."]


@pytest.mark.parametrize("opciones", [(), ("--max-pasos", "40"), ("--sin-plan",)])
def test_sin_recorte_no_hay_aviso(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path, opciones: tuple[str, ...]
) -> None:
    limites = "limites:\n  max_pasos: 40\n  max_pasos_plan: 20\n"
    presupuestos, salida = _correr_con_limites(monkeypatch, repo_sin_yaml, limites, *opciones)
    assert "recortados" not in salida
    if "--sin-plan" not in opciones:
        assert presupuestos == [20]


def test_sin_max_pasos_plan_escrito_no_hay_aviso(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path
) -> None:
    # Sin la clave, el 10 por defecto se ajusta a la mitad sin avisar: no se recorta nada escrito.
    presupuestos, salida = _correr_con_limites(
        monkeypatch, repo_sin_yaml, "", "--max-pasos", "6"
    )
    assert presupuestos == [3]
    assert "recortados" not in salida


def test_varias_llamadas_en_un_paso_se_agrupan_y_el_log_no_cambia(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path
) -> None:
    leer = LlamadaHerramienta("a", "leer_archivo", {"ruta": "app.py"})
    listar = LlamadaHerramienta("b", "listar_directorio", {"ruta": "."})
    codigo, salida = _correr(
        monkeypatch, repo_sin_yaml, [pedir(leer, listar), terminar()], "--sin-plan"
    )
    assert codigo == 0
    assert "[1] 2 herramientas\n    · leer_archivo(ruta='app.py')" in salida
    assert "    · listar_directorio(ruta='.')" in salida
    pasos = [e for e in _eventos_del_log(repo_sin_yaml) if e["evento"] == "herramienta"]
    # Un evento por llamada, con las mismas claves de siempre.
    assert [(e["paso"], e["herramienta"]) for e in pasos] == [
        (1, "leer_archivo"), (1, "listar_directorio"),
    ]
    assert not {"indice", "total"} & set(pasos[0])


def _correr_con_entrega(
    monkeypatch: pytest.MonkeyPatch,
    repo: Path,
    es_validado: bool,
    *opciones: str,
    texto_final: str = "Hecho",
    url_github: str | None = None,
    antes_de_correr: Callable[[Path, Path], None] | None = None,
    entrada: str | None = None,
) -> tuple[int, str, Path]:
    """Repo Git con remoto local; la ejecución (falsa) escribe un archivo y valida o no."""
    (repo / "harness.yaml").write_text(YAML_CON_DOCKER, encoding="utf-8")
    (repo.parent / "T-1.md").write_text("# T-1: Crear tareas\n", encoding="utf-8")
    iniciar_repo(repo)
    remoto = agregar_remoto(repo, repo.parent / "remoto.git")
    if antes_de_correr:
        antes_de_correr(repo, remoto)

    def ejecutar_falso(prep, max_pasos, reporte):
        if url_github:
            # Después del fetch inicial: la URL dice GitHub y el push sigue yendo al local.
            git(prep.repo, "remote", "set-url", "origin", url_github)
            git(prep.repo, "remote", "set-url", "--push", "origin", str(remoto))
        (prep.repo / "tareas.py").write_text("TAREAS = []\n", encoding="utf-8")
        resultado = ResultadoEjecucion(texto_final, 3, 10, 5, False, es_validado, 1)
        return resultado, CambiosRepo(creados=["tareas.py"])

    monkeypatch.setattr(cli, "_ejecutar", ejecutar_falso)
    codigo, salida = _correr(monkeypatch, repo, [], *opciones, entrada=entrada)
    return codigo, salida, remoto


@requiere_git
def test_con_la_validacion_ok_hace_commit_y_push_en_la_rama(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path
) -> None:
    codigo, salida, remoto = _correr_con_entrega(monkeypatch, repo_sin_yaml, True)

    assert codigo == 0
    assert "Rama feature/T-1 creada desde main." in salida
    assert "✓ Commit" in salida and "✓ Push de feature/T-1 a origin." in salida
    assert "feature/T-1" in ramas_remotas(remoto)
    assert git(repo_sin_yaml, "log", "-1", "--format=%B", "feature/T-1") == "T-1: Crear tareas"
    eventos = {e["evento"]: e for e in _eventos_del_log(repo_sin_yaml)}
    assert eventos["rama"] == {**eventos["rama"], "rama": "feature/T-1", "base": "main"}
    assert eventos["entrega"]["archivos"] == ["tareas.py"]
    assert eventos["entrega"]["es_pusheado"] is True


def _commit_local(repo: Path, remoto: Path) -> None:
    git(repo, "commit", "--quiet", "--allow-empty", "-m", "sin subir")


def _commit_en_el_remoto(repo: Path, remoto: Path) -> None:
    otro = repo.parent / "otro"
    git(repo.parent, "clone", "--quiet", str(remoto), str(otro))
    git(otro, "-c", "user.name=Otro", "-c", "user.email=o@example.com",
        "commit", "--quiet", "--allow-empty", "-m", "de otro")
    git(otro, "push", "--quiet", "origin", "main")


@requiere_git
@pytest.mark.parametrize("opciones", [(), ("--sin-push",)])
@pytest.mark.parametrize(
    ("cambiar_base", "conteo"),
    [
        (_commit_local, "1 commit adelante y 0 commits atrás"),
        (_commit_en_el_remoto, "0 commits adelante y 1 commit atrás"),
    ],
)
def test_avisa_en_consola_si_la_base_esta_desactualizada_antes_de_crear_la_rama(
    monkeypatch: pytest.MonkeyPatch,
    repo_sin_yaml: Path,
    opciones: tuple[str, ...],
    cambiar_base: Callable[[Path, Path], None],
    conteo: str,
) -> None:
    _, salida, _ = _correr_con_entrega(
        monkeypatch, repo_sin_yaml, True, *opciones, antes_de_correr=cambiar_base
    )
    aviso = f"La rama main local está {conteo} de origin/main."
    # La consola parte las líneas largas: se compara sin los saltos.
    salida_plana = " ".join(salida.split())
    assert aviso in salida_plana
    assert salida_plana.index(aviso) < salida_plana.index("Rama feature/T-1 creada desde main.")


@requiere_git
def test_abre_el_pr_y_oculta_el_token_en_la_descripcion(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "tok-secreto-123")
    pedidos: list[PedidoPullRequest] = []

    def crear_falso(pedido: PedidoPullRequest, token: str) -> PullRequest:
        pedidos.append(pedido)
        return PullRequest("https://github.com/gerson/demo/pull/3", es_borrador=True)

    monkeypatch.setattr("harness.entrega.github.crear_pull_request", crear_falso)
    codigo, salida, _ = _correr_con_entrega(
        monkeypatch, repo_sin_yaml, True,
        texto_final="Usé tok-secreto-123 para probar.",
        url_github="https://github.com/gerson/demo.git",
    )

    assert codigo == 0
    assert "✓ Pull request en borrador: https://github.com/gerson/demo/pull/3" in salida
    (pedido,) = pedidos
    assert pedido.titulo == "T-1: Crear tareas" and pedido.base == "main"
    assert "Usé **** para probar." in pedido.cuerpo
    assert "Pasaron los tests (`pytest -q`) en la validación 1." in pedido.cuerpo
    (entrega,) = [e for e in _eventos_del_log(repo_sin_yaml) if e["evento"] == "entrega"]
    assert entrega["pull_request"] == "https://github.com/gerson/demo/pull/3"
    (log,) = (repo_sin_yaml / ".harness" / "logs").glob("*.jsonl")
    assert "tok-secreto-123" not in log.read_text(encoding="utf-8")


PROBLEMA_TOKEN = (
    "El token no puede leer la rama main de gerson/demo: le falta el permiso Contents."
)


def _con_github_al_empezar(
    monkeypatch: pytest.MonkeyPatch, problema: str | None = None, es_interactiva: bool = False
) -> list[str]:
    """El remoto parece de GitHub (el push sigue yendo al bare local) y el token falla o no.

    Devuelve las ramas base con las que se verificó el token.
    """
    verificaciones: list[str] = []

    def verificar_falso(repo_github, base: str, token: str | None) -> None:
        verificaciones.append(base)
        if problema:
            raise ErrorGitHub(problema, 403)

    monkeypatch.setattr("harness.git.url_remoto", lambda *_: "https://github.com/gerson/demo.git")
    monkeypatch.setattr("harness.entrega.github.verificar_acceso", verificar_falso)
    monkeypatch.setattr(cli.consola, "es_terminal_interactiva", lambda: es_interactiva)
    return verificaciones


def _prs_pedidos(monkeypatch: pytest.MonkeyPatch) -> list[PedidoPullRequest]:
    pedidos: list[PedidoPullRequest] = []

    def crear_falso(pedido: PedidoPullRequest, token: str) -> PullRequest:
        pedidos.append(pedido)
        return PullRequest("https://github.com/gerson/demo/pull/3", es_borrador=True)

    monkeypatch.setattr("harness.entrega.github.crear_pull_request", crear_falso)
    return pedidos


@requiere_git
def test_con_el_token_ok_lo_avisa_antes_de_crear_la_rama(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    verificaciones = _con_github_al_empezar(monkeypatch)
    pedidos = _prs_pedidos(monkeypatch)
    codigo, salida, _ = _correr_con_entrega(monkeypatch, repo_sin_yaml, True)

    assert codigo == 0 and verificaciones == ["main"]
    salida_plana = " ".join(salida.split())
    verificado = "✓ Token de GitHub con acceso a gerson/demo."
    assert verificado in salida_plana
    assert "no se puede comprobar sin crear uno" in salida_plana
    assert salida_plana.index(verificado) < salida_plana.index("Rama feature/T-1 creada")
    assert len(pedidos) == 1


@requiere_git
def test_si_el_token_falla_sin_terminal_se_detiene_sin_crear_la_rama(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path
) -> None:
    _con_github_al_empezar(monkeypatch, PROBLEMA_TOKEN)
    codigo, salida, remoto = _correr_con_entrega(monkeypatch, repo_sin_yaml, True)

    assert codigo == 1
    salida_plana = " ".join(salida.split())
    assert PROBLEMA_TOKEN in salida_plana
    assert "usa --sin-pr para seguir sin PR" in salida_plana
    assert "¿Continuar sin PR" not in salida_plana
    assert git(repo_sin_yaml, "branch", "--list", "feature/T-1") == ""
    # No se llegó a ejecutar el modelo.
    assert not (repo_sin_yaml / "tareas.py").exists()


@requiere_git
@pytest.mark.parametrize("respuesta", ["n\n", "\n"])
def test_si_el_token_falla_y_responde_que_no_se_detiene(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path, respuesta: str
) -> None:
    _con_github_al_empezar(monkeypatch, PROBLEMA_TOKEN, es_interactiva=True)
    codigo, salida, _ = _correr_con_entrega(
        monkeypatch, repo_sin_yaml, True, entrada=respuesta
    )

    assert codigo == 1
    salida_plana = " ".join(salida.split())
    assert "¿Continuar sin PR (solo commit y push)? [s/N]" in salida_plana
    assert "Detenido antes de crear la rama" in salida_plana
    assert git(repo_sin_yaml, "branch", "--list", "feature/T-1") == ""
    assert not (repo_sin_yaml / "tareas.py").exists()


@requiere_git
def test_si_el_token_falla_y_responde_que_si_sigue_sin_pr(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path
) -> None:
    _con_github_al_empezar(monkeypatch, PROBLEMA_TOKEN, es_interactiva=True)
    pedidos = _prs_pedidos(monkeypatch)
    codigo, salida, remoto = _correr_con_entrega(monkeypatch, repo_sin_yaml, True, entrada="s\n")

    assert codigo == 0
    assert "Se sigue sin PR, como con --sin-pr" in " ".join(salida.split())
    assert "feature/T-1" in ramas_remotas(remoto)
    assert pedidos == []
    _assert_aviso_sin_pr(salida)
    eventos = [e["evento"] for e in _eventos_del_log(repo_sin_yaml)]
    assert eventos.index("aviso") < eventos.index("rama")


@requiere_git
@pytest.mark.parametrize("opcion", ["--sin-pr", "--sin-push"])
def test_sin_pr_no_verifica_el_token(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path, opcion: str
) -> None:
    verificaciones = _con_github_al_empezar(monkeypatch, PROBLEMA_TOKEN)
    codigo, _, _ = _correr_con_entrega(monkeypatch, repo_sin_yaml, True, opcion)
    assert codigo == 0 and verificaciones == []


def _assert_aviso_sin_pr(salida: str) -> None:
    """Al final, después del push: que no hubo PR y el link para crearlo a mano."""
    url = "https://github.com/gerson/demo/compare/main...feature/T-1?expand=1"
    salida_plana = " ".join(salida.split())
    aviso = "Sin PR (--sin-pr): se hizo el push, pero no el pull request."
    assert aviso in salida_plana
    assert f"Créalo a mano: {url}\n" in salida
    assert salida_plana.index("✓ Push") < salida_plana.index(aviso)


@requiere_git
def test_con_sin_pr_avisa_al_final_con_el_link(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path
) -> None:
    _con_github_al_empezar(monkeypatch)
    pedidos = _prs_pedidos(monkeypatch)
    codigo, salida, _ = _correr_con_entrega(monkeypatch, repo_sin_yaml, True, "--sin-pr")
    assert codigo == 0 and pedidos == []
    _assert_aviso_sin_pr(salida)


@requiere_git
def test_si_el_pr_falla_termina_con_codigo_1(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "tok")

    def falla(pedido: PedidoPullRequest, token: str) -> PullRequest:
        raise ErrorGitHub(
            "GitHub no creó el pull request: 422 Validation Failed; not all refs are readable",
            422,
        )

    monkeypatch.setattr("harness.entrega.github.crear_pull_request", falla)
    codigo, salida, remoto = _correr_con_entrega(
        monkeypatch, repo_sin_yaml, True, url_github="https://github.com/gerson/demo.git"
    )
    assert codigo == 1
    salida_plana = " ".join(salida.split())
    assert "No se pudo crear el pull request (el push sí se hizo)" in salida_plana
    assert "El token necesita Contents: Read-only y Pull requests: Read and write" in salida_plana
    # El link entero en una línea, para poder abrirlo con un clic.
    url = "https://github.com/gerson/demo/compare/main...feature/T-1?expand=1"
    assert f"Créalo a mano: {url}\n" in salida
    ruta = repo_sin_yaml / ".harness" / "logs" / "T-1-pr.md"
    assert f"Título y descripción para pegar: {ruta}" in salida
    assert ruta.read_text(encoding="utf-8").startswith("# T-1: Crear tareas\n\n## Resumen")
    assert "feature/T-1" in ramas_remotas(remoto)
    (entrega,) = [e for e in _eventos_del_log(repo_sin_yaml) if e["evento"] == "entrega"]
    assert entrega["url_pr_a_mano"] == url and entrega["pr_pendiente"] == str(ruta)


@requiere_git
def test_con_la_validacion_fallida_no_hay_commit_ni_push(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path
) -> None:
    codigo, salida, remoto = _correr_con_entrega(monkeypatch, repo_sin_yaml, False)

    assert codigo == 1
    assert "La validación no pasó: sin commit ni push." in salida
    assert "feature/T-1" not in ramas_remotas(remoto)
    assert git(repo_sin_yaml, "branch", "--show-current") == "feature/T-1"
    assert (repo_sin_yaml / "tareas.py").exists()


@requiere_git
def test_con_cambios_sin_commitear_no_llama_al_modelo(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path
) -> None:
    (repo_sin_yaml / "harness.yaml").write_text(YAML_CON_DOCKER, encoding="utf-8")
    iniciar_repo(repo_sin_yaml)
    (repo_sin_yaml / "app.py").write_text("x = 2\n", encoding="utf-8")
    proveedor = ProveedorFalso([terminar()])

    codigo, salida = _correr(monkeypatch, repo_sin_yaml, proveedor, "--sin-push")
    assert codigo == 1
    assert "sin commitear" in salida
    assert proveedor.conversaciones == []


def test_sin_harness_yaml_no_hay_git(monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path) -> None:
    # repo_sin_yaml ni siquiera es un repo Git: sin validación no hay entrega.
    codigo, salida = _correr(monkeypatch, repo_sin_yaml, [terminar()], "--sin-plan")
    assert codigo == 0
    assert "sin commit ni push" in " ".join(salida.split())
    eventos = [e["evento"] for e in _eventos_del_log(repo_sin_yaml)]
    assert "rama" not in eventos and "entrega" not in eventos


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


def test_el_paso_de_proponer_plan_no_repite_el_plan(
    monkeypatch: pytest.MonkeyPatch, repo_sin_yaml: Path
) -> None:
    _, salida = _correr(monkeypatch, repo_sin_yaml, [proponer(), _escribir("nuevo.py"), terminar()])
    assert "proponer_plan(resumen=<24 caracteres>" not in salida
    assert "archivos=<1 elementos>" in salida and "pasos=<1 elementos>" in salida
