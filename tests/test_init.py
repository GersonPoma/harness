from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness import cli
from harness.config import ConfigModelo
from harness.entorno_docker import ResultadoComando
from harness.config_proyecto import validar_texto_config
from harness.init import LINEA_REQUIERE_AGENTS, ErrorInit, archivos_faltantes, ejecutar_init
from harness.prompts import (
    EJEMPLO_HARNESS_MAVEN,
    PLANTILLA_HARNESS_YAML,
    PROMPT_INIT,
    RECOMENDACIONES_POR_STACK,
    crear_mensajes_init,
)
from harness.tipos import LlamadaHerramienta, RespuestaModelo
from tests.falsos import ProveedorFalso, pedir, terminar

YAML_VALIDO = """\
version: 1
proyecto: demo
docker:
  imagen: python:3.12-slim
comandos:
  tests: pytest -q
"""
YAML_INVALIDO = "version: 1\nproyecto: demo\ncomandos:\n  tests: pytest\n"
LECTURA = ["leer_archivo", "listar_directorio"]


def _guardar(herramienta: str, contenido: str, id_llamada: str = "g") -> RespuestaModelo:
    return pedir(LlamadaHerramienta(id_llamada, herramienta, {"contenido": contenido}))


@pytest.fixture
def repo_vacio(tmp_path: Path) -> Path:
    """Repo sin AGENTS.md ni harness.yaml."""
    raiz = tmp_path / "repo"
    raiz.mkdir()
    (raiz / "app.py").write_text("x = 1\n", encoding="utf-8")
    return raiz


def test_archivos_faltantes(repo_vacio: Path) -> None:
    assert archivos_faltantes(repo_vacio) == ["AGENTS.md", "harness.yaml"]
    (repo_vacio / "AGENTS.md").write_text("# x", encoding="utf-8")
    assert archivos_faltantes(repo_vacio) == ["harness.yaml"]


def test_genera_los_dos_y_corrige_un_yaml_invalido(repo_vacio: Path) -> None:
    leer = LlamadaHerramienta("l1", "leer_archivo", {"ruta": "app.py"})
    proveedor = ProveedorFalso([
        pedir(leer),
        _guardar("guardar_agents_md", "# demo\n", "g1"),
        _guardar("guardar_harness_yaml", YAML_INVALIDO, "g2"),
        _guardar("guardar_harness_yaml", YAML_VALIDO, "g3"),
    ])
    resultado = ejecutar_init(proveedor, repo_vacio)

    assert proveedor.herramientas[0] == [*LECTURA, "guardar_agents_md", "guardar_harness_yaml"]
    # El YAML inválido no se escribe y el error vuelve al modelo con la ruta exacta.
    error = proveedor.conversaciones[3][-1].contenido
    assert "docker: falta este campo" in error and "Corrige el YAML" in error
    assert resultado.creados == ["AGENTS.md", "harness.yaml"]
    assert resultado.pasos == 4
    assert (repo_vacio / "AGENTS.md").read_text(encoding="utf-8") == "# demo\n"
    guardado = (repo_vacio / "harness.yaml").read_text(encoding="utf-8")
    linea_proyecto = "proyecto: demo\n"
    assert guardado == YAML_VALIDO.replace(linea_proyecto, linea_proyecto + LINEA_REQUIERE_AGENTS)


def test_el_yaml_generado_lleva_requiere_agents_md_explicita(repo_vacio: Path) -> None:
    (repo_vacio / "AGENTS.md").write_text("# x", encoding="utf-8")
    ejecutar_init(ProveedorFalso([_guardar("guardar_harness_yaml", YAML_VALIDO)]), repo_vacio)

    guardado = (repo_vacio / "harness.yaml").read_text(encoding="utf-8")
    lineas = guardado.splitlines()
    assert lineas[2] == (
        "requiere_agents_md: false  # true = harness run se detiene si falta AGENTS.md"
    )
    assert validar_texto_config(guardado).requiere_agents_md is False


def test_si_el_modelo_ya_puso_requiere_agents_md_no_se_repite(repo_vacio: Path) -> None:
    (repo_vacio / "AGENTS.md").write_text("# x", encoding="utf-8")
    yaml = YAML_VALIDO + LINEA_REQUIERE_AGENTS
    ejecutar_init(ProveedorFalso([_guardar("guardar_harness_yaml", yaml)]), repo_vacio)
    assert (repo_vacio / "harness.yaml").read_text(encoding="utf-8") == yaml


@pytest.mark.parametrize("agents_md", [None, "", "  \n"])
def test_init_genera_agents_md_aunque_se_requiera(repo_vacio: Path, agents_md: str | None) -> None:
    # requiere_agents_md solo afecta a harness run: init tiene que poder crear el que falta.
    yaml = YAML_VALIDO + "requiere_agents_md: true\n"
    (repo_vacio / "harness.yaml").write_text(yaml, encoding="utf-8")
    if agents_md is not None:
        (repo_vacio / "AGENTS.md").write_text(agents_md, encoding="utf-8")
    resultado = ejecutar_init(
        ProveedorFalso([_guardar("guardar_agents_md", "# demo\n")]), repo_vacio
    )
    assert resultado.creados == ["AGENTS.md"]
    assert (repo_vacio / "AGENTS.md").read_text(encoding="utf-8") == "# demo\n"
    assert (repo_vacio / "harness.yaml").read_text(encoding="utf-8") == yaml


def test_solo_genera_lo_que_falta_y_usa_lo_existente(repo_vacio: Path) -> None:
    (repo_vacio / "AGENTS.md").write_text("# Mi proyecto\nUsa pytest.\n", encoding="utf-8")
    proveedor = ProveedorFalso([_guardar("guardar_harness_yaml", YAML_VALIDO)])
    resultado = ejecutar_init(proveedor, repo_vacio)

    assert proveedor.herramientas[0] == [*LECTURA, "guardar_harness_yaml"]
    pedido = proveedor.conversaciones[0][1].contenido
    assert "faltan en el repo: harness.yaml" in pedido
    assert "AGENTS.md ya existe" in pedido and "Usa pytest." in pedido
    assert resultado.creados == ["harness.yaml"]
    assert (repo_vacio / "AGENTS.md").read_text(encoding="utf-8") == "# Mi proyecto\nUsa pytest.\n"


def test_con_los_dos_archivos_no_llama_al_modelo(repo_vacio: Path) -> None:
    (repo_vacio / "AGENTS.md").write_text("# x", encoding="utf-8")
    (repo_vacio / "harness.yaml").write_text(YAML_VALIDO, encoding="utf-8")
    proveedor = ProveedorFalso([])
    assert ejecutar_init(proveedor, repo_vacio).creados == []
    assert proveedor.conversaciones == []


def test_si_termina_sin_guardar_se_le_pide_lo_que_falta(repo_vacio: Path) -> None:
    proveedor = ProveedorFalso([
        _guardar("guardar_agents_md", "# demo\n"),
        terminar("Listo"),
        _guardar("guardar_harness_yaml", YAML_VALIDO),
    ])
    avisos: list[str] = []
    resultado = ejecutar_init(proveedor, repo_vacio, al_aviso=avisos.append)
    assert proveedor.herramientas[2] == ["guardar_harness_yaml"]
    assert "Todavía falta guardar: harness.yaml" in proveedor.conversaciones[2][-1].contenido
    assert avisos == ["Faltan por guardar: harness.yaml. Se le pide al modelo."]
    assert resultado.creados == ["AGENTS.md", "harness.yaml"]


def test_si_nunca_guarda_es_error_y_conserva_lo_guardado(repo_vacio: Path) -> None:
    proveedor = ProveedorFalso([
        _guardar("guardar_agents_md", "# demo\n"), terminar("Listo"), terminar("No puedo"),
    ])
    with pytest.raises(ErrorInit, match="no guardó: harness.yaml"):
        ejecutar_init(proveedor, repo_vacio)
    assert (repo_vacio / "AGENTS.md").exists()
    assert not (repo_vacio / "harness.yaml").exists()


# --- CLI -----------------------------------------------------------------------------------


def _init(
    monkeypatch: pytest.MonkeyPatch, repo: Path, respuestas: list, *opciones: str
) -> tuple[int, str, ProveedorFalso]:
    proveedor = ProveedorFalso(respuestas)
    monkeypatch.setattr(cli, "cargar_config", lambda: ConfigModelo("gemini", "m", None, "k"))
    monkeypatch.setattr(cli, "crear_proveedor", lambda *_: proveedor)
    resultado = CliRunner().invoke(cli.app, ["init", "--repo", str(repo), *opciones])
    return resultado.exit_code, resultado.output, proveedor


def test_cli_init_genera_y_recuerda_revisar(
    monkeypatch: pytest.MonkeyPatch, repo_vacio: Path
) -> None:
    respuestas = [
        _guardar("guardar_agents_md", "# demo\n", "g1"),
        _guardar("guardar_harness_yaml", YAML_VALIDO, "g2"),
    ]
    codigo, salida, _ = _init(monkeypatch, repo_vacio, respuestas)
    assert codigo == 0
    assert "Creados:\n  AGENTS.md\n  harness.yaml" in salida
    assert "Revísalos antes de usarlos" in salida
    assert list((repo_vacio / ".harness" / "logs").glob("init-*.jsonl"))


def test_cli_init_sin_nada_que_generar(monkeypatch: pytest.MonkeyPatch, repo_vacio: Path) -> None:
    (repo_vacio / "AGENTS.md").write_text("# x", encoding="utf-8")
    (repo_vacio / "harness.yaml").write_text(YAML_VALIDO, encoding="utf-8")
    codigo, salida, proveedor = _init(monkeypatch, repo_vacio, [])
    assert codigo == 0
    assert "no hay nada que generar" in salida
    assert proveedor.conversaciones == []


def test_cli_init_verificar_ok(monkeypatch: pytest.MonkeyPatch, repo_vacio: Path) -> None:
    (repo_vacio / "AGENTS.md").write_text("# x", encoding="utf-8")
    (repo_vacio / "harness.yaml").write_text(YAML_VALIDO, encoding="utf-8")
    llamadas: list[str] = []

    def verificar_falso(repo, config, variables, al_avanzar=None) -> ResultadoComando:
        llamadas.append(config.comandos.tests)
        return ResultadoComando(0, "2 passed")

    monkeypatch.setattr(cli, "verificar_config", verificar_falso)
    codigo, salida, _ = _init(monkeypatch, repo_vacio, [], "--verificar")
    assert codigo == 0
    assert llamadas == ["pytest -q"]
    assert "✓ Verificación: la instalación y los tests funcionan." in salida


def test_cli_init_verificar_falla_y_oculta_secretos(
    monkeypatch: pytest.MonkeyPatch, repo_vacio: Path
) -> None:
    (repo_vacio / "AGENTS.md").write_text("# x", encoding="utf-8")
    yaml = YAML_VALIDO + "variables_entorno:\n  CLAVE: ${CLAVE_PRUEBA_INIT}\n"
    (repo_vacio / "harness.yaml").write_text(yaml, encoding="utf-8")
    monkeypatch.setenv("CLAVE_PRUEBA_INIT", "s3creta")
    salida_tests = "FAILED test_x - conexión rechazada con clave s3creta\n1 failed"
    monkeypatch.setattr(
        cli, "verificar_config", lambda *_args, **_kw: ResultadoComando(1, salida_tests)
    )
    codigo, salida, _ = _init(monkeypatch, repo_vacio, [], "--verificar")
    assert codigo == 1
    assert "✗ Verificación: los tests no pasan" in salida
    assert "con clave ****" in salida and "s3creta" not in salida


# --- Prompt ---------------------------------------------------------------------------------


@pytest.mark.parametrize("ejemplo", [PLANTILLA_HARNESS_YAML, EJEMPLO_HARNESS_MAVEN])
def test_los_ejemplos_del_prompt_son_harness_yaml_validos(ejemplo: str) -> None:
    # El modelo los copia: si no validan, guardar_harness_yaml se los rechazaría.
    config = validar_texto_config(ejemplo)
    assert config.limites.max_pasos_plan <= config.limites.max_pasos // 2


def test_el_ejemplo_maven_es_el_de_tienda_api() -> None:
    config = validar_texto_config(EJEMPLO_HARNESS_MAVEN)
    assert config.comandos.tests == (
        "mvn -B -q clean test -Dmaven.test.redirectTestOutputToFile=true"
    )
    assert config.limites.model_dump(include={
        "max_pasos", "max_pasos_plan", "timeout_comando_segundos", "max_caracteres_salida",
    }) == {
        "max_pasos": 50, "max_pasos_plan": 20,
        "timeout_comando_segundos": 600, "max_caracteres_salida": 6000,
    }
    assert set(config.variables_entorno) == {
        "SPRING_DATASOURCE_URL", "SPRING_DATASOURCE_USERNAME", "SPRING_DATASOURCE_PASSWORD",
    }
    assert config.variables_entorno["SPRING_DATASOURCE_URL"].startswith("jdbc:postgresql://db:")
    assert any("ArquitecturaTest" in glob for glob in config.archivos_protegidos)


@pytest.mark.parametrize(
    "texto",
    [
        # Python chico: sin limites.
        "Python chico (pytest, pocas dependencias): los valores por defecto alcanzan; no "
        "escribas\n  limites.",
        # Maven y Gradle.
        "timeout_comando_segundos: 600, max_pasos: 50,\n  max_pasos_plan: 20 y "
        "max_caracteres_salida: 6000",
        "mvn -B -q clean test -Dmaven.test.redirectTestOutputToFile=true",
        "gradle clean test",
        # Spring Boot.
        "SPRING_DATASOURCE_URL (jdbc:postgresql://db:5432/<base>), SPRING_DATASOURCE_USERNAME",
        # Tests de arquitectura.
        "ArchUnit en Java, import-linter en Python",
        "archivos_protegidos sus archivos de test o de configuración",
    ],
)
def test_el_prompt_de_init_recomienda_segun_el_stack(texto: str) -> None:
    assert texto in PROMPT_INIT


def test_el_prompt_de_init_incluye_la_plantilla_y_el_ejemplo() -> None:
    assert PLANTILLA_HARNESS_YAML in PROMPT_INIT
    assert EJEMPLO_HARNESS_MAVEN in PROMPT_INIT
    assert RECOMENDACIONES_POR_STACK in PROMPT_INIT
    assert crear_mensajes_init(["harness.yaml"], {})[0].contenido == PROMPT_INIT
