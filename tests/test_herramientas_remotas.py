import json
from collections.abc import Iterator

import pytest

from harness.bucle import ejecutar_llamada
from harness.cliente_mcp import SesionMCP
from harness.herramientas.remotas import (
    HERRAMIENTAS_LECTURA_GITHUB,
    PISTA_RECORTE,
    PREFIJO_GITHUB,
    crear_herramientas_mcp,
    es_herramienta_github,
    preparar_salida,
)
from harness.tipos import LlamadaHerramienta
from tests.datos.servidor_mcp_falso import VAR_SECRETO
from tests.mcp_prueba import servidor_stdio

SECRETO = "github_pat_muy_secreto"
MAX_CARACTERES = 4000


@pytest.fixture(scope="module")
def sesion(tmp_path_factory: pytest.TempPathFactory) -> Iterator[SesionMCP]:
    """Una sola sesión para todo el módulo: lanzar el servidor tarda unos segundos."""
    ruta_log = tmp_path_factory.mktemp("logs") / "T-1-mcp-github.log"
    with SesionMCP(servidor_stdio(env={VAR_SECRETO: SECRETO}), ruta_log, "T-1") as abierta:
        yield abierta


def _crear(sesion: SesionMCP, permitidas=HERRAMIENTAS_LECTURA_GITHUB, maximo=MAX_CARACTERES):
    return crear_herramientas_mcp(sesion, PREFIJO_GITHUB, permitidas, [SECRETO], maximo)


def test_solo_las_permitidas_con_el_prefijo_del_servidor(sesion: SesionMCP) -> None:
    herramientas = _crear(sesion)
    assert [h.nombre for h in herramientas] == [
        "github__get_file_contents",
        "github__list_commits",
        "github__search_pull_requests",
        "github__pull_request_read",
    ]
    # Las de escritura que tiene el servidor no llegan nunca al modelo.
    assert "github__delete_file" not in {h.nombre for h in herramientas}
    assert all(es_herramienta_github(h) for h in herramientas)


def test_una_permitida_que_el_servidor_no_tiene_se_ignora(sesion: SesionMCP) -> None:
    herramientas = _crear(sesion, ("list_commits", "no_existe"))
    assert [h.nombre for h in herramientas] == ["github__list_commits"]


def test_el_esquema_queda_tal_cual(sesion: SesionMCP) -> None:
    (leer,) = _crear(sesion, ("get_file_contents",))
    (original,) = [h for h in sesion.herramientas if h.nombre == "get_file_contents"]
    assert leer.parametros == original.esquema
    assert leer.descripcion == "Lee un archivo del repo en GitHub."


def test_la_llamada_pasa_por_el_servidor(sesion: SesionMCP) -> None:
    (leer,) = _crear(sesion, ("get_file_contents",))
    assert leer.funcion(owner="o", repo="r", path="a.py") == "contenido de o/r/a.py"


def test_oculta_los_secretos_de_la_salida(sesion: SesionMCP) -> None:
    (leer,) = _crear(sesion, ("get_file_contents",))
    assert leer.funcion(owner="o", repo="r", path="secreto.txt") == "token=****"


def test_recorta_las_salidas_largas_desde_el_principio(sesion: SesionMCP) -> None:
    (leer,) = _crear(sesion, ("get_file_contents",), maximo=300)
    salida = leer.funcion(owner="o", repo="r", path="largo.txt")
    assert len(salida) <= 300
    lineas = salida.splitlines()
    # Líneas completas desde la primera; la marca, con la pista, al final.
    assert lineas[0] == "línea 0" and all(l.startswith("línea ") for l in lineas[:-1])
    assert "línea 399" not in salida
    assert lineas[-1].endswith(f"se omitió el resto. {PISTA_RECORTE}]")


# --- Salidas en JSON de una línea, como las de GitHub ---------------------------------------

COMMITS = [
    {"sha": f"{numero:040x}", "commit": {"message": f"commit número {numero}"}}
    for numero in range(60)
]


def test_un_json_corto_llega_con_sangria() -> None:
    texto = json.dumps({"sha": "abc", "mensaje": "acción"}, ensure_ascii=True)
    assert preparar_salida(texto, [], 4000) == '{\n "sha": "abc",\n "mensaje": "acción"\n}'


def test_un_json_largo_de_una_linea_llega_formateado_y_recortado() -> None:
    texto = json.dumps(COMMITS)
    assert "\n" not in texto and len(texto) > 4000
    salida = preparar_salida(texto, [], 1500)

    assert len(salida) <= 1500
    # El principio está intacto, en líneas completas: el modelo ve dónde empieza el JSON y los
    # primeros objetos.
    formateado = json.dumps(COMMITS, indent=1, ensure_ascii=False)
    principio, marca = salida.rsplit("\n", 1)
    assert formateado.startswith(principio + "\n")
    assert principio.startswith('[\n {\n  "sha": "' + "0" * 40)
    total = len(formateado)
    assert marca == f"[... {total} caracteres en total, se omitió el resto. {PISTA_RECORTE}]"


def test_un_texto_largo_de_una_linea_conserva_el_principio() -> None:
    texto = "inicio " + "x" * 5000 + " final"
    salida = preparar_salida(texto, [], 300)
    assert len(salida) <= 300
    assert salida.startswith("inicio xxx") and "final" not in salida
    assert f"5013 caracteres en total, se omitió el resto. {PISTA_RECORTE}]" in salida


def test_un_json_que_es_un_solo_string_largo_conserva_el_principio() -> None:
    salida = preparar_salida(json.dumps("inicio " + "y" * 5000), [], 300)
    assert len(salida) <= 300 and salida.startswith('"inicio yyy')


def test_los_secretos_se_ocultan_tambien_dentro_del_json() -> None:
    texto = json.dumps({"url": "https://x?token=github_pat_abc", "token": "github_pat_abc"})
    salida = preparar_salida(texto, ["github_pat_abc"], 4000)
    assert "github_pat_abc" not in salida and '"token": "****"' in salida


def test_un_error_del_servidor_le_llega_al_modelo_con_su_motivo(sesion: SesionMCP) -> None:
    herramientas = {h.nombre: h for h in _crear(sesion, ("get_repo",))}
    llamada = LlamadaHerramienta("c1", "github__get_repo", {"owner": "x", "repo": "y"})
    assert ejecutar_llamada(llamada, herramientas) == ("Error: repo no encontrado: x/y", True)
