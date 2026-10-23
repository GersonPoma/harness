from pathlib import Path

import pytest

from harness.cambios import CambiosRepo
from harness.ejecucion import Planificacion, ejecutar_ticket
from harness.herramientas import crear_herramientas, crear_herramientas_lectura
from harness.herramientas.archivos import ErrorHerramienta
from harness.plan import Plan, archivos_fuera_del_plan, crear_herramienta_plan
from harness.planificacion import ErrorPlan
from harness.prompts import (
    MENSAJE_ENTREGAR_PLAN,
    MENSAJE_IMPLEMENTAR,
    MENSAJE_PLANIFICAR,
    crear_mensajes_iniciales,
)
from harness.tipos import LlamadaHerramienta, Mensaje, Rol
from tests.falsos import PLAN_SIMPLE, ProveedorFalso, pedir, proponer, terminar

LECTURA = ["leer_archivo", "listar_directorio"]
LEER = LlamadaHerramienta("l1", "leer_archivo", {"ruta": "src/app.py"})
ESCRIBIR = LlamadaHerramienta("e1", "escribir_archivo", {"ruta": "nuevo.py", "contenido": "x"})


def _plan(**cambios: object) -> dict:
    return {**PLAN_SIMPLE, **cambios}


def _ejecutar(repo: Path, proveedor: ProveedorFalso, max_pasos: int = 20, **kwargs):
    mensajes = crear_mensajes_iniciales("T")
    planificacion = Planificacion(crear_herramientas_lectura(repo), **kwargs)
    resultado = ejecutar_ticket(
        proveedor, mensajes, crear_herramientas(repo), max_pasos, planificacion=planificacion
    )
    return resultado, mensajes


# --- Validación del plan -------------------------------------------------------------------


def test_plan_valido_normaliza_rutas() -> None:
    registrados: list[Plan] = []
    herramienta = crear_herramienta_plan(registrados.append)
    archivos = [{"ruta": ".\\src\\app.py", "accion": "modificar", "motivo": "endpoint"}]
    assert herramienta.funcion(**_plan(archivos=archivos)) == "Plan registrado."
    assert registrados[0].archivos[0].ruta == "src/app.py"


@pytest.mark.parametrize(
    ("cambios", "error"),
    [
        (
            {"archivos": [{"ruta": "a.py", "accion": "renombrar", "motivo": "x"}]},
            "archivos.0.accion",
        ),
        ({"archivos": []}, "archivos"),
        ({"pasos": []}, "pasos"),
        ({"resumen": "  "}, "resumen"),
        ({"prioridad": "alta"}, "prioridad"),
    ],
)
def test_plan_invalido_vuelve_al_modelo(cambios: dict, error: str) -> None:
    herramienta = crear_herramienta_plan(lambda _: None)
    with pytest.raises(ErrorHerramienta, match=f"Plan inválido: .*{error}"):
        herramienta.funcion(**_plan(**cambios))


def test_archivos_fuera_del_plan() -> None:
    plan = Plan.model_validate(_plan(archivos=[
        {"ruta": "app/rutas.py", "accion": "modificar", "motivo": "x"},
        {"ruta": "tests/test_x.py", "accion": "crear", "motivo": "y"},
    ]))
    cambios = CambiosRepo(
        creados=["tests/test_x.py", "notas.txt"],
        modificados=["App/Rutas.py", "app/main.py"],
        eliminados=["viejo.py"],
    )
    assert archivos_fuera_del_plan(plan, cambios) == ["app/main.py", "notas.txt", "viejo.py"]


# --- Ronda de plan -------------------------------------------------------------------------


def test_la_ronda_de_plan_solo_tiene_herramientas_de_lectura(repo: Path) -> None:
    proveedor = ProveedorFalso([pedir(LEER), proponer(), pedir(ESCRIBIR), terminar("hecho")])
    resultado, mensajes = _ejecutar(repo, proveedor)

    assert proveedor.herramientas[0] == [*LECTURA, "proponer_plan"]
    assert proveedor.herramientas[1] == [*LECTURA, "proponer_plan"]
    # La implementación ya tiene todas las herramientas y no tiene proponer_plan.
    assert "escribir_archivo" in proveedor.herramientas[2]
    assert "proponer_plan" not in proveedor.herramientas[2]
    assert resultado.plan == Plan.model_validate(PLAN_SIMPLE)
    assert resultado.texto_final == "hecho"
    assert resultado.pasos == 4


def test_la_conversacion_lleva_el_plan_y_el_pedido_de_implementar(repo: Path) -> None:
    proveedor = ProveedorFalso([proponer(), terminar()])
    _, mensajes = _ejecutar(repo, proveedor)
    assert mensajes[2] == Mensaje(Rol.USUARIO, MENSAJE_PLANIFICAR)
    assert mensajes[3].llamadas[0].nombre == "proponer_plan"
    assert mensajes[4].contenido == "Plan registrado."
    assert mensajes[5] == Mensaje(Rol.USUARIO, MENSAJE_IMPLEMENTAR)


def test_si_responde_texto_se_le_pide_el_plan_solo_con_proponer_plan(repo: Path) -> None:
    proveedor = ProveedorFalso([terminar("Voy a crear nuevo.py"), proponer(), terminar()])
    avisos: list[str] = []
    resultado = ejecutar_ticket(
        proveedor, crear_mensajes_iniciales("T"), crear_herramientas(repo), 20,
        al_aviso=avisos.append,
        planificacion=Planificacion(crear_herramientas_lectura(repo)),
    )
    assert proveedor.conversaciones[1][-1] == Mensaje(Rol.USUARIO, MENSAJE_ENTREGAR_PLAN)
    assert proveedor.herramientas[1] == ["proponer_plan"]
    assert resultado.plan is not None
    assert resultado.pasos == 3
    assert "El modelo no propuso un plan: se le pide que llame a proponer_plan." in avisos


def test_plan_agotado_se_pide_al_final(repo: Path) -> None:
    # 10 lecturas agotan la ronda de plan; el pedido final ya no ofrece leer_archivo.
    proveedor = ProveedorFalso([pedir(LEER)] * 10 + [proponer(), terminar()])
    resultado, _ = _ejecutar(repo, proveedor, max_pasos=30)
    assert proveedor.herramientas[10] == ["proponer_plan"]
    assert resultado.pasos == 12


def test_plan_invalido_y_luego_valido(repo: Path) -> None:
    invalido = proponer(_plan(archivos=[]), id_llamada="p1")
    proveedor = ProveedorFalso([invalido, proponer(), terminar()])
    resultado, mensajes = _ejecutar(repo, proveedor)
    assert mensajes[4].contenido.startswith("Error: Plan inválido: archivos")
    assert resultado.plan == Plan.model_validate(PLAN_SIMPLE)


def test_sin_plan_valido_se_corta(repo: Path) -> None:
    proveedor = ProveedorFalso([terminar("no sé"), terminar("sigo sin saber"), terminar("x")])
    with pytest.raises(ErrorPlan, match="no propuso un plan válido"):
        _ejecutar(repo, proveedor)


def test_plan_rechazado_no_implementa(repo: Path) -> None:
    proveedor = ProveedorFalso([proponer(), pedir(ESCRIBIR), terminar()])
    resultado, _ = _ejecutar(repo, proveedor, al_planear=lambda _: False)
    assert resultado.es_plan_rechazado
    assert len(proveedor.conversaciones) == 1
    assert not (repo / "nuevo.py").exists()


def test_max_pasos_plan_configurable(repo: Path) -> None:
    # Con 3 pasos de plan, la cuarta respuesta ya es el pedido final con solo proponer_plan.
    proveedor = ProveedorFalso([pedir(LEER)] * 3 + [proponer(), terminar()])
    resultado, _ = _ejecutar(repo, proveedor, max_pasos=30, max_pasos_plan=3)
    assert proveedor.herramientas[2] == [*LECTURA, "proponer_plan"]
    assert proveedor.herramientas[3] == ["proponer_plan"]
    assert resultado.pasos == 5


def test_los_pasos_del_plan_cuentan_para_el_limite(repo: Path) -> None:
    proveedor = ProveedorFalso([pedir(LEER), proponer(), pedir(ESCRIBIR)])
    resultado, _ = _ejecutar(repo, proveedor, max_pasos=3)
    assert resultado.es_limite_alcanzado
    assert resultado.pasos == 3


def test_en_la_ronda_de_plan_no_se_puede_escribir(repo: Path) -> None:
    proveedor = ProveedorFalso([pedir(ESCRIBIR), proponer(), terminar()])
    _, mensajes = _ejecutar(repo, proveedor)
    assert "la herramienta 'escribir_archivo' no existe" in mensajes[4].contenido
    assert not (repo / "nuevo.py").exists()


def test_quita_la_numeracion_que_pone_el_modelo() -> None:
    pasos = [
        "1. Crear el modelo", "2) Agregar el endpoint", " 10 - Correr tests", "Paso 3 sin número",
    ]
    plan = Plan.model_validate(_plan(pasos=pasos))
    assert plan.pasos == [
        "Crear el modelo", "Agregar el endpoint", "Correr tests", "Paso 3 sin número",
    ]
