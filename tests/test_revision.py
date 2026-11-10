"""Paso de revisión: con los tests en verde, el modelo revisa su diff y lo informa."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from harness import cli
from harness.cambios import CambiosRepo, comparar, tomar_instantanea
from harness.config_proyecto import ConfigComandos, ErrorConfigProyecto, validar_texto_config
from harness.ejecucion import ResultadoEjecucion, ejecutar_ticket
from harness.entrega import crear_cuerpo_pr
from harness.revision import (
    MAX_CARACTERES_DIFF,
    EstadoRevision,
    Revision,
    armar_diff,
    revisar,
)
from harness.sesion import ESTADO_IMPLEMENTACION, Sesion, armar_reanudacion
from harness.tipos import LlamadaHerramienta, Mensaje, Rol
from tests.falsos import ProveedorFalso, pedir, terminar
from tests.git_prueba import iniciar_repo, requiere_git

LEER = LlamadaHerramienta("l1", "leer_archivo", {"ruta": "app.py"})
PROBLEMA = {
    "archivo": "src/main.py",
    "regla_o_criterio": "fuera del ticket",
    "descripcion": "POST /tareas es de TAREAS-2: nadie lo pidió.",
}


def _informar(es_aprobado: bool, problemas: list | None = None, id_llamada: str = "r") -> object:
    argumentos = {"es_aprobado": es_aprobado, "problemas": problemas or []}
    return pedir(LlamadaHerramienta(id_llamada, "informar_revision", argumentos))


def _revision(max_vueltas: int = 2, max_pasos: int = 3, estados: list | None = None) -> Revision:
    def al_revisar(estado: EstadoRevision) -> None:
        if estados is not None:
            estados.append(EstadoRevision(**vars(estado)))

    return Revision([], lambda: "+ código nuevo", max_vueltas, max_pasos, al_revisar)


def _ejecutar(proveedor: ProveedorFalso, revision: Revision, max_pasos: int = 10):
    mensajes = [Mensaje(Rol.USUARIO, "T")]
    resultado = ejecutar_ticket(
        proveedor, mensajes, [], max_pasos, validar=lambda: (True, "## Tests (x)\n3 passed"),
        max_intentos_validacion=2, revision=revision,
    )
    return resultado, mensajes


# --- La ronda ------------------------------------------------------------------------------


def test_una_revision_aprobada_termina_la_ejecucion() -> None:
    estados: list[EstadoRevision] = []
    proveedor = ProveedorFalso([terminar("Listo"), _informar(True)])
    resultado, mensajes = _ejecutar(proveedor, _revision(estados=estados))
    assert resultado.revision.es_aprobada and resultado.revision.vueltas == 1
    assert resultado.texto_final == "Listo"
    # El pedido de revisión lleva el diff y pide marcar lo que ningún criterio pide.
    pedido = next(m.contenido for m in mensajes if "Antes de entregar" in (m.contenido or ""))
    assert "+ código nuevo" in pedido and "fuera del ticket" in pedido
    assert [e.vueltas for e in estados] == [1]


def test_con_problemas_vuelve_a_implementar_y_se_revisa_otra_vez() -> None:
    proveedor = ProveedorFalso([
        terminar("Listo"), _informar(False, [PROBLEMA]),
        terminar("Quité el endpoint"), _informar(True, id_llamada="r2"),
    ])
    resultado, mensajes = _ejecutar(proveedor, _revision())
    assert resultado.validaciones == 2
    assert resultado.revision.vueltas == 2 and resultado.revision.correcciones == 1
    assert resultado.revision.es_aprobada and resultado.revision.problemas == []
    assert resultado.texto_final == "Quité el endpoint"
    pedido = next(m.contenido for m in mensajes if "La revisión encontró" in (m.contenido or ""))
    assert "src/main.py (fuera del ticket): POST /tareas es de TAREAS-2" in pedido
    assert "quítalo en vez de completarlo" in pedido


def test_si_se_acaban_las_vueltas_los_problemas_quedan_sin_resolver() -> None:
    proveedor = ProveedorFalso([terminar(), _informar(False, [PROBLEMA])])
    resultado, _ = _ejecutar(proveedor, _revision(max_vueltas=1))
    # Los tests pasan: igual se entrega, con los problemas en el PR.
    assert resultado.es_validado is True
    assert resultado.revision.problemas == [PROBLEMA] and not resultado.revision.es_aprobada
    assert resultado.revision.correcciones == 0


def test_la_revision_no_descuenta_de_max_pasos() -> None:
    # 3 pasos de implementación con max_pasos 3: la revisión (2 pasos) tiene su presupuesto.
    proveedor = ProveedorFalso([
        pedir(LEER), pedir(LEER), terminar(), pedir(LEER), _informar(False, [PROBLEMA]),
        terminar("Corregido"), _informar(True, id_llamada="r2"),
    ])
    resultado, _ = _ejecutar(proveedor, _revision(max_pasos=3), max_pasos=4)
    assert not resultado.es_limite_alcanzado
    assert resultado.pasos == 7 and resultado.pasos_extra == 3
    assert resultado.pasos_con_presupuesto == 4


def test_sin_informe_se_le_pide_con_tool_choice_y_si_no_se_sigue_sin_revision() -> None:
    proveedor = ProveedorFalso([terminar(), pedir(LEER), pedir(LEER), terminar("no sé")])
    resultado, mensajes = _ejecutar(proveedor, _revision(max_pasos=3))
    assert proveedor.obligatorias[-1] == "informar_revision"
    assert resultado.revision.es_fallida and resultado.revision.vueltas == 1
    assert resultado.es_validado is True


@pytest.mark.parametrize(
    ("argumentos", "mensaje"),
    [
        ({"es_aprobado": True, "problemas": [PROBLEMA]}, "es_aprobado es true pero hay"),
        ({"es_aprobado": False, "problemas": []}, "describe cada uno"),
        ({"es_aprobado": False, "problemas": [{"archivo": "x"}]}, "regla_o_criterio"),
    ],
)
def test_un_informe_invalido_vuelve_al_modelo(argumentos: dict, mensaje: str) -> None:
    invalido = pedir(LlamadaHerramienta("r1", "informar_revision", argumentos))
    proveedor = ProveedorFalso([invalido, _informar(True, id_llamada="r2")])
    mensajes = [Mensaje(Rol.USUARIO, "T")]
    ronda = revisar(proveedor, mensajes, _revision(max_pasos=3), 1)
    assert ronda.informe is not None and ronda.informe.es_aprobado
    error = next(m.contenido for m in mensajes if m.rol == Rol.HERRAMIENTA)
    assert error.startswith("Error: Informe inválido") and mensaje in error


# --- El diff -------------------------------------------------------------------------------


@requiere_git
def test_el_diff_tiene_los_modificados_y_los_creados_sin_la_memoria(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    iniciar_repo(tmp_path)
    antes = tomar_instantanea(tmp_path)
    (tmp_path / "app.py").write_text("x = 2\n", encoding="utf-8")
    (tmp_path / "nuevo.py").write_text("CLAVE = 'sk-secreta-123456'\n", encoding="utf-8")
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "memory.md").write_text("- nota\n", encoding="utf-8")

    cambios = comparar(antes, tomar_instantanea(tmp_path))
    diff = armar_diff(tmp_path, cambios, ["sk-secreta-123456"])
    assert "-x = 1\n+x = 2" in diff
    assert "+++ b/nuevo.py" in diff and "+CLAVE = '****'" in diff
    assert "memory.md" not in diff and "sk-secreta" not in diff


def test_un_diff_largo_se_recorta(tmp_path: Path) -> None:
    (tmp_path / "grande.py").write_text("x = 1\n" * 10_000, encoding="utf-8")
    diff = armar_diff(tmp_path, CambiosRepo(creados=["grande.py"]), [])
    assert len(diff) < MAX_CARACTERES_DIFF + 200 and "diff recortado" in diff


def test_sin_cambios_de_codigo() -> None:
    assert armar_diff(Path("."), CambiosRepo(memoria=[".harness/memory.md"]), []) == (
        "(sin cambios en el código)"
    )


# --- harness.yaml, PR, sesión y CLI ----------------------------------------------------------

YAML = (
    "version: 1\nproyecto: demo\ndocker:\n  imagen: python:3.12-slim\n"
    "comandos:\n  tests: pytest\n"
)


def test_revision_en_harness_yaml() -> None:
    por_defecto = validar_texto_config(YAML).revision
    assert (por_defecto.activa, por_defecto.max_vueltas, por_defecto.max_pasos) == (True, 2, 6)
    escrita = validar_texto_config(YAML + "revision:\n  activa: false\n  max_vueltas: 1\n")
    assert not escrita.revision.activa and escrita.revision.max_vueltas == 1
    for invalido in ("revision:\n  max_vueltas: 0\n", "revision:\n  otra: 1\n"):
        with pytest.raises(ErrorConfigProyecto):
            validar_texto_config(YAML + invalido)


def _cuerpo(estado: EstadoRevision) -> str:
    resultado = ResultadoEjecucion("Listo.", 5, 1, 1, False, True, 1, revision=estado)
    return crear_cuerpo_pr(resultado, CambiosRepo(), ConfigComandos(tests="pytest"), "m")


def test_la_revision_en_el_pr() -> None:
    assert "## Revisión" not in _cuerpo(EstadoRevision())
    assert "sin problemas (1 vuelta)" in _cuerpo(EstadoRevision(vueltas=1, es_aprobada=True))
    pendientes = _cuerpo(EstadoRevision(vueltas=2, problemas=[PROBLEMA]))
    assert "**Problemas sin resolver**" in pendientes
    assert "- `src/main.py` (fuera del ticket): POST /tareas es de TAREAS-2" in pendientes
    assert "_Sin revisión: el modelo no la informó._" in _cuerpo(
        EstadoRevision(vueltas=1, es_fallida=True)
    )


def test_resume_sigue_contando_las_vueltas_y_los_pasos_extra() -> None:
    sesion = Sesion(
        ticket={"clave": "T-1", "titulo": "t", "fuente": "local", "url": None},
        estado=ESTADO_IMPLEMENTACION, proveedor="gemini/x", pasos=12, pasos_extra=4,
        revision={"vueltas": 1, "correcciones": 1, "problemas": [PROBLEMA],
                  "es_aprobada": False, "es_fallida": False},
        mensajes=[Mensaje(Rol.SISTEMA, "s"), Mensaje(Rol.USUARIO, "T")],
    )
    continuacion = armar_reanudacion(sesion, "corte", []).continuacion
    assert continuacion.revision.vueltas == 1 and continuacion.revision.correcciones == 1
    assert continuacion.pasos_con_presupuesto == 8


def test_sin_revision_en_harness_yaml_o_con_la_opcion(tmp_path: Path) -> None:
    config = validar_texto_config(YAML)
    prep = SimpleNamespace(repo=tmp_path, secretos=[], es_con_revision=True, revisor=None)
    reporte = SimpleNamespace(revision=lambda _: None)
    assert cli._revision(prep, config, {}, reporte) is not None
    prep.es_con_revision = False
    assert cli._revision(prep, config, {}, reporte) is None
    apagada = validar_texto_config(YAML + "revision:\n  activa: false\n")
    prep.es_con_revision = True
    assert cli._revision(prep, apagada, {}, reporte) is None


# --- Revisar con un modelo distinto -------------------------------------------------------

from harness.proveedores.base import ErrorProveedor  # noqa: E402


def _con_revisor(revisor: ProveedorFalso, estados: list | None = None) -> Revision:
    revision = _revision(estados=estados)
    revision.proveedor = revisor
    revision.modelo = "openrouter/revisor"
    return revision


def test_el_revisor_recibe_la_conversacion_y_el_principal_no_revisa() -> None:
    principal = ProveedorFalso([terminar("Listo")])
    revisor = ProveedorFalso([_informar(False, [PROBLEMA]), _informar(True, id_llamada="r2")])
    principal_2 = ProveedorFalso([terminar("Listo"), terminar("Corregido")])
    resultado, mensajes = _ejecutar(principal_2, _con_revisor(revisor))
    # El principal implementa (y corrige); el revisor hace las dos revisiones.
    assert len(principal_2.conversaciones) == 2 and len(revisor.conversaciones) == 2
    assert "+ código nuevo" in revisor.conversaciones[0][-1].contenido
    assert resultado.revision.es_aprobada and resultado.revision.modelo == "openrouter/revisor"
    # Sus tokens se suman: 2 del principal y 2 del revisor, de 10 cada uno.
    assert resultado.tokens_entrada == 40
    assert principal.conversaciones == []


def test_si_el_revisor_se_cae_se_sigue_sin_revision() -> None:
    avisos: list[str] = []
    estados: list[EstadoRevision] = []
    revisor = ProveedorFalso([ErrorProveedor("'openrouter' respondió con error 404: no existe")])
    proveedor = ProveedorFalso([terminar("Listo")])
    mensajes = [Mensaje(Rol.USUARIO, "T")]
    resultado = ejecutar_ticket(
        proveedor, mensajes, [], 10, validar=lambda: (True, "## Tests (x)\n3 passed"),
        max_intentos_validacion=2, revision=_con_revisor(revisor, estados),
        al_aviso=avisos.append,
    )
    assert resultado.es_validado and resultado.revision.es_fallida
    assert avisos == [
        "El revisor openrouter/revisor falló: 'openrouter' respondió con error 404: no existe "
        "Se sigue sin revisión."
    ]
    assert [e.es_fallida for e in estados] == [True]
    assert "el revisor `openrouter/revisor` falló" in _cuerpo(resultado.revision)


def test_si_se_cae_el_principal_en_la_revision_se_corta() -> None:
    proveedor = ProveedorFalso([terminar("Listo"), ErrorProveedor("caído")])
    with pytest.raises(ErrorProveedor):
        _ejecutar(proveedor, _revision())


def test_el_pr_dice_quien_reviso() -> None:
    estado = EstadoRevision(vueltas=1, es_aprobada=True, modelo="openrouter/revisor")
    assert "Revisó `openrouter/revisor`, un modelo distinto del que implementó." in (
        _cuerpo(estado)
    )


def test_el_revisor_desde_el_cli_usa_su_propio_proveedor(tmp_path: Path) -> None:
    from harness.config import ConfigModelo

    config = validar_texto_config(YAML)
    revisor = ConfigModelo("groq", "llama", None, "clave-groq-0123456789", "GROQ_API_KEY")
    prep = SimpleNamespace(repo=tmp_path, secretos=[], es_con_revision=True, revisor=revisor)
    reporte = SimpleNamespace(revision=lambda _: None, reintento=lambda _: None)
    revision = cli._revision(prep, config, {}, reporte)
    assert revision.modelo == "groq/llama" and revision.proveedor is not None
