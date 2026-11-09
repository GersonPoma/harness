from harness.compactacion import (
    INICIO_MARCA,
    PASOS_RECIENTES,
    Compactacion,
    ProveedorQueCompacta,
    compactar,
    tamano,
)
from harness.tipos import LlamadaHerramienta, Mensaje, Rol
from tests.falsos import ProveedorFalso, terminar

FIRMA = {"extra_content": {"google": {"thought_signature": "firma"}}}


def _conversacion(pasos: int, largo: int = 1000) -> list[Mensaje]:
    """Sistema, ticket, plan y `pasos` lecturas, cada una con un resultado de `largo`."""
    mensajes = [
        Mensaje(Rol.SISTEMA, "s" * 500),
        Mensaje(Rol.USUARIO, "ticket " * 100),
        Mensaje(Rol.USUARIO, "Plan registrado. Ahora impleméntalo."),
    ]
    for numero in range(1, pasos + 1):
        llamada = LlamadaHerramienta(f"c{numero}", "leer_archivo", {"ruta": f"src/a{numero}.py"},
                                     datos_proveedor=FIRMA, origen="gemini/x")
        mensajes.append(Mensaje(Rol.ASISTENTE, f"Leo a{numero}", [llamada]))
        contenido = "\n".join(["linea"] * (largo // 6))
        mensajes.append(Mensaje(Rol.HERRAMIENTA, contenido, id_llamada=f"c{numero}"))
    return mensajes


def test_por_debajo_del_maximo_no_hace_nada() -> None:
    mensajes = _conversacion(3)
    assert compactar(mensajes, tamano(mensajes)) is None


def test_acorta_los_resultados_viejos_y_conserva_el_resto() -> None:
    mensajes = _conversacion(12)
    originales = list(mensajes)
    maximo = tamano(mensajes) // 2

    resultado = compactar(mensajes, maximo)

    assert resultado is not None and resultado.resultados_omitidos > 0
    # Hace lo que puede: los últimos pasos quedan enteros aunque no se llegue al máximo.
    assert tamano(mensajes) == tamano(originales) - resultado.caracteres_ahorrados
    # Nunca se borra un mensaje: misma cantidad, mismos roles e ids de llamada.
    assert [(m.rol, m.id_llamada) for m in mensajes] == [
        (m.rol, m.id_llamada) for m in originales
    ]
    # Sistema, ticket, plan y textos del asistente (con sus firmas) quedan intactos.
    assert mensajes[:3] == originales[:3]
    asistentes = [m for m in mensajes if m.rol == Rol.ASISTENTE]
    assert asistentes == [m for m in originales if m.rol == Rol.ASISTENTE]
    assert asistentes[0].llamadas[0].datos_proveedor == FIRMA
    # El más viejo se omitió, con una marca que dice qué era.
    assert mensajes[4].contenido == (
        "[Resultado omitido para ahorrar contexto: leer_archivo('src/a1.py'), 166 líneas. "
        "Vuelve a llamar la herramienta si lo necesitas.]"
    )


def test_los_ultimos_pasos_quedan_enteros() -> None:
    mensajes = _conversacion(10)
    originales = list(mensajes)
    compactar(mensajes, 1)
    resultados = [i for i, m in enumerate(mensajes) if m.rol == Rol.HERRAMIENTA]
    recientes = resultados[-PASOS_RECIENTES:]
    assert all(mensajes[i] == originales[i] for i in recientes)
    viejos = resultados[:-PASOS_RECIENTES]
    assert all(mensajes[i].contenido.startswith(INICIO_MARCA) for i in viejos)


def test_lo_ya_compactado_no_se_vuelve_a_contar() -> None:
    mensajes = _conversacion(10)
    compactar(mensajes, 1)
    assert compactar(mensajes, 1) is None


def test_el_proveedor_compacta_la_misma_lista_antes_de_pedir() -> None:
    mensajes = _conversacion(10)
    falso = ProveedorFalso([terminar()])
    avisos: list[Compactacion] = []
    proveedor = ProveedorQueCompacta(falso, tamano(mensajes) // 2, avisos.append)

    proveedor.responder(mensajes, [], "proponer_plan")

    assert len(avisos) == 1
    assert falso.obligatorias == ["proponer_plan"]
    # El proveedor recibió lo compactado, y la lista del bucle (y de la sesión) también cambió.
    assert falso.conversaciones[0] == mensajes
    assert any(m.contenido.startswith(INICIO_MARCA) for m in mensajes if m.rol == Rol.HERRAMIENTA)
