from pathlib import Path

from harness.ejecucion import Validacion, ejecutar_ticket
from harness.herramientas import crear_herramientas
from harness.prompts import crear_mensajes_iniciales
from harness.tipos import LlamadaHerramienta, Rol
from tests.falsos import ProveedorFalso, pedir, terminar


class ValidadorFalso:
    """Devuelve resultados guionados de tests: True = pasan."""

    def __init__(self, resultados: list[bool]) -> None:
        self._resultados = list(resultados)
        self.llamadas = 0

    def __call__(self) -> tuple[bool, str]:
        self.llamadas += 1
        es_valido = self._resultados.pop(0)
        return es_valido, "3 passed" if es_valido else "FAILED test_crear - AssertionError"


def _escribir(id_llamada: str) -> LlamadaHerramienta:
    return LlamadaHerramienta(id_llamada, "escribir_archivo", {"ruta": "a.py", "contenido": "x"})


def test_sin_validador_termina_cuando_el_modelo_termina(repo: Path) -> None:
    proveedor = ProveedorFalso([terminar("hecho")])
    resultado = ejecutar_ticket(
        proveedor, crear_mensajes_iniciales("T"), crear_herramientas(repo), 10
    )
    assert resultado.texto_final == "hecho"
    assert resultado.es_validado is None


def test_validacion_correcta_a_la_primera(repo: Path) -> None:
    validador = ValidadorFalso([True])
    proveedor = ProveedorFalso([pedir(_escribir("c1")), terminar()])
    resultado = ejecutar_ticket(
        proveedor, crear_mensajes_iniciales("T"), crear_herramientas(repo), 10, validador, 3
    )
    assert resultado.es_validado is True
    assert resultado.validaciones == 1
    assert resultado.pasos == 2


def test_autocorreccion_el_modelo_recibe_el_error_y_corrige(repo: Path) -> None:
    validador = ValidadorFalso([False, True])
    proveedor = ProveedorFalso([
        pedir(_escribir("c1")), terminar("listo"),
        pedir(_escribir("c2")), terminar("corregido"),
    ])
    validaciones: list[Validacion] = []
    pasos: list[int] = []
    mensajes = crear_mensajes_iniciales("T", hay_validacion=True)
    resultado = ejecutar_ticket(
        proveedor, mensajes, crear_herramientas(repo), 10, validador, 3,
        al_paso=lambda p: pasos.append(p.numero), al_validar=validaciones.append,
    )

    assert resultado.es_validado is True
    assert resultado.texto_final == "corregido"
    assert resultado.pasos == 4
    assert pasos == [1, 3]  # la numeración sigue entre vueltas
    assert [v.es_valido for v in validaciones] == [False, True]
    # El tercer pedido al modelo ya incluye el error de los tests.
    ultimo = proveedor.conversaciones[2][-1]
    assert ultimo.rol == Rol.USUARIO
    assert "corrección 1 de 3" in ultimo.contenido
    assert "FAILED test_crear" in ultimo.contenido


def test_se_rinde_al_agotar_los_intentos_de_validacion(repo: Path) -> None:
    validador = ValidadorFalso([False, False, False])
    proveedor = ProveedorFalso([terminar(), terminar(), terminar()])
    resultado = ejecutar_ticket(
        proveedor, crear_mensajes_iniciales("T"), crear_herramientas(repo), 10, validador, 2
    )
    assert resultado.es_validado is False
    # 1 validación inicial + 2 vueltas de corrección.
    assert resultado.validaciones == 3
    assert validador.llamadas == 3


def test_max_pasos_es_el_total_de_todas_las_vueltas(repo: Path) -> None:
    validador = ValidadorFalso([False])
    proveedor = ProveedorFalso([
        pedir(_escribir("c1")), terminar(),
        pedir(_escribir("c2")), pedir(_escribir("c3")),
    ])
    resultado = ejecutar_ticket(
        proveedor, crear_mensajes_iniciales("T"), crear_herramientas(repo), 3, validador, 3
    )
    assert resultado.es_limite_alcanzado
    assert resultado.pasos == 3
    assert len(proveedor.conversaciones) == 3


def test_limite_justo_despues_de_validar(repo: Path) -> None:
    validador = ValidadorFalso([False])
    proveedor = ProveedorFalso([pedir(_escribir("c1")), terminar()])
    resultado = ejecutar_ticket(
        proveedor, crear_mensajes_iniciales("T"), crear_herramientas(repo), 2, validador, 3
    )
    assert resultado.es_limite_alcanzado
    assert resultado.es_validado is False
