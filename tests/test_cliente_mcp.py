import json
import sys
from collections.abc import Iterator
from concurrent.futures import Future
from pathlib import Path

import pytest

from harness import cliente_mcp
from harness.cliente_mcp import (
    ErrorMCP,
    ResultadoMCP,
    SesionMCP,
    agregar_nombre_docker,
    crear_entorno_hijo,
    nombre_contenedor,
)
from harness.config_mcp import ServidorHTTP, ServidorMCP, ServidorStdio
from tests.mcp_prueba import levantar_servidor_http, servidor_stdio

TICKET = "T-1"


@pytest.fixture(params=["stdio", "http"])
def servidor(request: pytest.FixtureRequest) -> Iterator[ServidorMCP]:
    """El servidor falso por cada transporte: los mismos casos tienen que andar en los dos."""
    if request.param == "stdio":
        yield servidor_stdio()
        return
    with levantar_servidor_http() as local:
        yield local.config()


def _sesion(servidor: ServidorMCP, tmp_path: Path) -> SesionMCP:
    return SesionMCP(servidor, tmp_path / "logs" / "T-1-mcp-github.log", TICKET)


def test_conecta_y_lista_las_herramientas(servidor: ServidorMCP, tmp_path: Path) -> None:
    with _sesion(servidor, tmp_path) as sesion:
        nombres = {h.nombre for h in sesion.herramientas}
        assert {"get_file_contents", "list_commits", "delete_file", "falla"} <= nombres
        (leer,) = [h for h in sesion.herramientas if h.nombre == "get_file_contents"]
        assert leer.descripcion == "Lee un archivo del repo en GitHub."
        assert leer.esquema["type"] == "object"
        assert set(leer.esquema["properties"]) == {"owner", "repo", "path"}


def test_llama_una_herramienta(servidor: ServidorMCP, tmp_path: Path) -> None:
    with _sesion(servidor, tmp_path) as sesion:
        resultado = sesion.llamar("get_file_contents", {"owner": "o", "repo": "r", "path": "a"})
    assert resultado.texto == "contenido de o/r/a" and not resultado.es_error


def test_una_herramienta_que_falla_vuelve_como_resultado(
    servidor: ServidorMCP, tmp_path: Path
) -> None:
    with _sesion(servidor, tmp_path) as sesion:
        resultado = sesion.llamar("falla", {"motivo": "prueba"})
    # El servidor del SDK no reenvía el mensaje de la excepción, solo qué herramienta falló.
    assert resultado.es_error and "falla" in resultado.texto


def test_un_error_con_is_error_llega_con_su_texto(servidor: ServidorMCP, tmp_path: Path) -> None:
    # Así falla el servidor real de GitHub: el texto es lo que el modelo necesita para corregirse.
    with _sesion(servidor, tmp_path) as sesion:
        resultado = sesion.llamar("get_repo", {"owner": "x", "repo": "y"})
    assert resultado == ResultadoMCP("repo no encontrado: x/y", es_error=True)


def test_una_herramienta_que_no_existe_vuelve_como_error(
    servidor: ServidorMCP, tmp_path: Path
) -> None:
    with _sesion(servidor, tmp_path) as sesion:
        resultado = sesion.llamar("no_existe", {})
    assert resultado.es_error and "no_existe" in resultado.texto


def test_una_llamada_que_tarda_demasiado(
    servidor: ServidorMCP, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cliente_mcp, "TIMEOUT_LLAMADA_SEGUNDOS", 0.5)
    with _sesion(servidor, tmp_path) as sesion:
        with pytest.raises(ErrorMCP, match=r"'lento' no respondió en 0\.5 s"):
            sesion.llamar("lento", {"segundos": 3})


def test_despues_de_cerrar_no_se_puede_llamar(servidor: ServidorMCP, tmp_path: Path) -> None:
    sesion = _sesion(servidor, tmp_path)
    with sesion:
        pass
    with pytest.raises(ErrorMCP, match="se cerró"):
        sesion.llamar("list_commits", {"owner": "o", "repo": "r"})


def test_se_cierra_aunque_haya_un_error_dentro(servidor: ServidorMCP, tmp_path: Path) -> None:
    sesion = _sesion(servidor, tmp_path)
    with pytest.raises(RuntimeError, match="algo falló"):
        with sesion:
            raise RuntimeError("algo falló")
    assert sesion._tarea.done()


# --- Solo stdio ---------------------------------------------------------------------------


def test_un_comando_que_no_existe_no_conecta(tmp_path: Path) -> None:
    servidor = ServidorStdio("github", "comando-que-no-existe-xyz", ())
    with pytest.raises(ErrorMCP, match="No se pudo conectar con el servidor MCP 'github'"):
        with _sesion(servidor, tmp_path):
            pass


def test_si_el_servidor_sale_al_arrancar_se_ve_la_causa_real(tmp_path: Path) -> None:
    # Así falla 'docker run' con Docker Desktop cerrado: escribe en stderr y sale.
    codigo = (
        "import sys; print('docker: error during connect: el pipe no existe', file=sys.stderr); "
        "print('token tok-secreto-de-prueba', file=sys.stderr); sys.exit(125)"
    )
    servidor = ServidorStdio(
        "github", sys.executable, ("-c", codigo), secretos=("tok-secreto-de-prueba",)
    )
    ruta_log = tmp_path / "logs" / "T-1-mcp-github.log"
    ruta_log.parent.mkdir(parents=True)
    ruta_log.write_text("línea de una sesión anterior\n", encoding="utf-8")
    with pytest.raises(ErrorMCP) as error:
        with SesionMCP(servidor, ruta_log, TICKET):
            pass
    mensaje = str(error.value)
    assert "docker: error during connect: el pipe no existe" in mensaje
    assert "ExceptionGroup" not in mensaje and "TaskGroup" not in mensaje
    assert "tok-secreto-de-prueba" not in mensaje and "token ****" in mensaje
    assert "sesión anterior" not in mensaje


def test_describir_fallo_muestra_las_excepciones_de_adentro_del_grupo() -> None:
    grupo = ExceptionGroup(
        "unhandled errors in a TaskGroup",
        [ExceptionGroup("interno", [ConnectionError("conexión rechazada")]), OSError("x")],
    )
    tarea: Future[None] = Future()
    tarea.set_exception(grupo)
    assert cliente_mcp._describir_fallo(tarea) == "ConnectionError: conexión rechazada; OSError: x"


def test_un_servidor_que_no_responde_a_tiempo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cliente_mcp, "TIMEOUT_CONEXION_SEGUNDOS", 1)
    # Un proceso que no habla MCP: nunca contesta al initialize.
    servidor = ServidorStdio("github", sys.executable, ("-c", "import time; time.sleep(30)"))
    with pytest.raises(ErrorMCP, match="'github' no respondió en 1 s"):
        with _sesion(servidor, tmp_path):
            pass


def test_los_logs_del_servidor_van_a_un_archivo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cliente_mcp, "TIMEOUT_CONEXION_SEGUNDOS", 2)
    servidor = ServidorStdio(
        "github",
        sys.executable,
        ("-c", "import sys; print('log del servidor', file=sys.stderr); import time; "
               "time.sleep(30)"),
    )
    ruta_log = tmp_path / "logs" / "T-1-mcp-github.log"
    with pytest.raises(ErrorMCP):
        with SesionMCP(servidor, ruta_log, TICKET) as _:
            pass
    assert "log del servidor" in ruta_log.read_text(encoding="utf-8")


def test_las_api_keys_del_env_no_llegan_al_servidor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "clave-gemini")
    monkeypatch.setenv("GITHUB_TOKEN", "github_pat_secreto")
    servidor = servidor_stdio(env={"GITHUB_PERSONAL_ACCESS_TOKEN": "github_pat_secreto"})
    with _sesion(servidor, tmp_path) as sesion:
        variables = set(json.loads(sesion.llamar("entorno", {}).texto))
    assert "GITHUB_PERSONAL_ACCESS_TOKEN" in variables
    assert not {"GEMINI_API_KEY", "GITHUB_TOKEN"} & variables


# --- Solo HTTP ----------------------------------------------------------------------------


def test_http_manda_los_headers_configurados(tmp_path: Path) -> None:
    with levantar_servidor_http() as local:
        servidor = local.config({"Authorization": "Bearer tok", "X-MCP-Toolsets": "repos"})
        with _sesion(servidor, tmp_path) as sesion:
            sesion.llamar("list_commits", {"owner": "o", "repo": "r"})
    assert local.headers_recibidos
    assert all(h.get("authorization") == "Bearer tok" for h in local.headers_recibidos)
    assert all(h.get("x-mcp-toolsets") == "repos" for h in local.headers_recibidos)


def test_http_sin_servidor_no_conecta(tmp_path: Path) -> None:
    # Puerto 9 (discard): nadie escucha ahí.
    servidor = ServidorHTTP("github", "http://127.0.0.1:9/mcp")
    with pytest.raises(ErrorMCP, match="No se pudo conectar con el servidor MCP 'github'"):
        with _sesion(servidor, tmp_path):
            pass


# --- Funciones puras ----------------------------------------------------------------------


def test_entorno_hijo_minimo_mas_docker_mas_env() -> None:
    entorno = {"DOCKER_HOST": "tcp://x:2375", "DOCKER_CONTEXT": "otro", "GEMINI_API_KEY": "k"}
    hijo = crear_entorno_hijo({"GITHUB_PERSONAL_ACCESS_TOKEN": "t"}, entorno)
    assert hijo["DOCKER_HOST"] == "tcp://x:2375" and hijo["DOCKER_CONTEXT"] == "otro"
    assert hijo["GITHUB_PERSONAL_ACCESS_TOKEN"] == "t"
    assert "GEMINI_API_KEY" not in hijo
    if sys.platform == "win32":
        # Sin estas, muchos programas no arrancan en Windows.
        assert {"SYSTEMROOT", "PATHEXT", "USERPROFILE", "APPDATA", "TEMP"} <= set(hijo)


ETIQUETAS_MCP = [
    "--label", "harness=1", "--label", "harness.pid=7", "--label", "harness.host=pc",
    "--label", "harness.tipo=mcp", "--label", "harness.ticket=T-1",
]


@pytest.mark.parametrize(
    ("comando", "args", "esperado", "es_agregado"),
    [
        ("docker", ["run", "-i", "img"], ["run", "--name", "n", *ETIQUETAS_MCP, "-i", "img"], True),
        ("C:/Docker/docker.EXE", ["run", "img"], ["run", "--name", "n", *ETIQUETAS_MCP, "img"],
         True),
        ("docker", ["run", "--name", "mio", "img"],
         ["run", *ETIQUETAS_MCP, "--name", "mio", "img"], False),
        ("docker", ["run", "--name=mio", "img"], ["run", *ETIQUETAS_MCP, "--name=mio", "img"],
         False),
        ("docker", ["compose", "run"], ["compose", "run"], False),
        ("npx", ["run"], ["run"], False),
    ],
)
def test_agregar_nombre_docker(
    monkeypatch: pytest.MonkeyPatch,
    comando: str,
    args: list[str],
    esperado: list[str],
    es_agregado: bool,
) -> None:
    # Las etiquetas permiten que harness limpiar encuentre el contenedor si queda huérfano.
    proceso = {"harness": "1", "harness.pid": "7", "harness.host": "pc"}
    monkeypatch.setattr(cliente_mcp, "etiquetas_del_proceso", lambda: dict(proceso))
    assert agregar_nombre_docker(comando, args, "n", "T-1") == (esperado, es_agregado)


def test_nombre_contenedor_valido_para_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cliente_mcp.os, "getpid", lambda: 42)
    assert nombre_contenedor("github remoto", "PROJ/12") == "harness-mcp-github-remoto-proj-12-42"
