from pathlib import Path

import pytest

from harness.bucle import ErrorRespuestaVacia, Paso, ejecutar_bucle
from harness.herramientas import crear_herramientas
from harness.prompts import MENSAJE_RESPUESTA_VACIA, crear_mensajes_iniciales
from harness.tipos import LlamadaHerramienta, Mensaje, Rol
from tests.falsos import ProveedorFalso, pedir, terminar, vacia


def _resultados_por_id(mensajes: list) -> dict[str, str]:
    return {m.id_llamada: m.contenido for m in mensajes if m.rol == Rol.HERRAMIENTA}


def test_termina_cuando_el_modelo_no_pide_herramientas(repo: Path) -> None:
    proveedor = ProveedorFalso([terminar("Nada que hacer")])
    resultado = ejecutar_bucle(proveedor, crear_mensajes_iniciales("T"), crear_herramientas(repo))
    assert resultado.texto_final == "Nada que hacer"
    assert resultado.pasos == 1
    assert not resultado.es_limite_alcanzado


def test_ejecuta_herramientas_y_devuelve_resultado_al_modelo(repo: Path) -> None:
    proveedor = ProveedorFalso([
        pedir(LlamadaHerramienta("c1", "leer_archivo", {"ruta": "src/app.py"})),
        pedir(LlamadaHerramienta("c2", "escribir_archivo", {"ruta": "b.py", "contenido": "x"})),
        terminar(),
    ])
    mensajes = crear_mensajes_iniciales("T")
    resultado = ejecutar_bucle(proveedor, mensajes, crear_herramientas(repo))

    assert resultado.pasos == 3
    assert resultado.tokens_entrada == 30
    assert (repo / "b.py").read_text(encoding="utf-8") == "x"
    # El segundo pedido al modelo ya incluye el contenido leído en el primero.
    assert _resultados_por_id(proveedor.conversaciones[1]) == {"c1": "print('hola')\n"}


def test_varias_herramientas_en_una_respuesta(repo: Path) -> None:
    proveedor = ProveedorFalso([
        pedir(
            LlamadaHerramienta("a", "leer_archivo", {"ruta": "src/app.py"}),
            LlamadaHerramienta("b", "listar_directorio", {"ruta": "src"}),
            LlamadaHerramienta("c", "leer_archivo", {"ruta": "no_existe.py"}),
        ),
        terminar(),
    ])
    pasos: list[Paso] = []
    mensajes = crear_mensajes_iniciales("T")
    ejecutar_bucle(proveedor, mensajes, crear_herramientas(repo), al_paso=pasos.append)

    resultados = _resultados_por_id(mensajes)
    assert list(resultados) == ["a", "b", "c"]
    assert resultados["a"] == "print('hola')\n"
    assert resultados["b"] == "app.py"
    assert resultados["c"].startswith("Error:")
    assert [p.numero for p in pasos] == [1, 1, 1]
    assert [p.es_error for p in pasos] == [False, False, True]


def test_herramienta_inexistente_se_devuelve_como_error(repo: Path) -> None:
    proveedor = ProveedorFalso([
        pedir(LlamadaHerramienta("c1", "borrar_todo", {})),
        terminar(),
    ])
    mensajes = crear_mensajes_iniciales("T")
    resultado = ejecutar_bucle(proveedor, mensajes, crear_herramientas(repo))
    error = _resultados_por_id(mensajes)["c1"]
    assert "no existe" in error and "leer_archivo" in error
    assert resultado.texto_final == "Listo"


def test_argumentos_con_json_invalido_se_devuelven_como_error(repo: Path) -> None:
    llamada = LlamadaHerramienta("c1", "leer_archivo", {}, "Argumentos con JSON inválido: x")
    proveedor = ProveedorFalso([pedir(llamada), terminar()])
    mensajes = crear_mensajes_iniciales("T")
    ejecutar_bucle(proveedor, mensajes, crear_herramientas(repo))
    assert "JSON inválido" in _resultados_por_id(mensajes)["c1"]


def test_argumentos_con_nombre_equivocado_se_devuelven_como_error(repo: Path) -> None:
    proveedor = ProveedorFalso([
        pedir(LlamadaHerramienta("c1", "leer_archivo", {"path": "src/app.py"})),
        terminar(),
    ])
    mensajes = crear_mensajes_iniciales("T")
    ejecutar_bucle(proveedor, mensajes, crear_herramientas(repo))
    assert "argumentos inválidos" in _resultados_por_id(mensajes)["c1"]


def test_ruta_prohibida_se_devuelve_como_error(repo: Path) -> None:
    proveedor = ProveedorFalso([
        pedir(LlamadaHerramienta("c1", "escribir_archivo", {"ruta": "../x", "contenido": ""})),
        terminar(),
    ])
    mensajes = crear_mensajes_iniciales("T")
    ejecutar_bucle(proveedor, mensajes, crear_herramientas(repo))
    assert "fuera del repo" in _resultados_por_id(mensajes)["c1"]


def test_se_detiene_al_llegar_al_limite_de_pasos(repo: Path) -> None:
    llamada = LlamadaHerramienta("c", "listar_directorio", {})
    proveedor = ProveedorFalso([pedir(llamada) for _ in range(5)])
    resultado = ejecutar_bucle(
        proveedor, crear_mensajes_iniciales("T"), crear_herramientas(repo), max_pasos=3
    )
    assert resultado.es_limite_alcanzado
    assert resultado.pasos == 3
    assert len(proveedor.conversaciones) == 3


def test_respuesta_vacia_pide_continuar_y_consume_un_paso(repo: Path) -> None:
    proveedor = ProveedorFalso([vacia(), vacia("  "), terminar("Hecho")])
    avisos: list[str] = []
    mensajes = crear_mensajes_iniciales("T")
    resultado = ejecutar_bucle(
        proveedor, mensajes, crear_herramientas(repo), al_aviso=avisos.append
    )

    assert resultado.texto_final == "Hecho"
    assert resultado.pasos == 3
    assert not resultado.es_limite_alcanzado
    assert proveedor.conversaciones[1][-1] == Mensaje(Rol.USUARIO, MENSAJE_RESPUESTA_VACIA)
    # Las respuestas vacías no se guardan en la conversación.
    assert [m.rol for m in mensajes[2:]] == [Rol.USUARIO, Rol.USUARIO, Rol.ASISTENTE]
    assert avisos == [
        "El modelo respondió vacío: se le pide que continúe (1/2).",
        "El modelo respondió vacío: se le pide que continúe (2/2).",
    ]


def test_tercera_respuesta_vacia_seguida_corta_con_error(repo: Path) -> None:
    proveedor = ProveedorFalso([vacia(), vacia(), vacia(), terminar()])
    with pytest.raises(ErrorRespuestaVacia, match="3 veces seguidas"):
        ejecutar_bucle(proveedor, crear_mensajes_iniciales("T"), crear_herramientas(repo))
    assert len(proveedor.conversaciones) == 3


def test_respuestas_vacias_no_seguidas_no_cortan(repo: Path) -> None:
    leer = LlamadaHerramienta("c1", "leer_archivo", {"ruta": "src/app.py"})
    proveedor = ProveedorFalso([vacia(), vacia(), pedir(leer), vacia(), vacia(), terminar()])
    resultado = ejecutar_bucle(proveedor, crear_mensajes_iniciales("T"), crear_herramientas(repo))
    assert resultado.texto_final == "Listo"
    assert resultado.pasos == 6


def test_respuestas_vacias_cuentan_para_el_limite_de_pasos(repo: Path) -> None:
    proveedor = ProveedorFalso([vacia(), vacia()])
    resultado = ejecutar_bucle(
        proveedor, crear_mensajes_iniciales("T"), crear_herramientas(repo), max_pasos=2
    )
    assert resultado.es_limite_alcanzado
