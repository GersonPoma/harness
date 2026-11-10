import pytest

from harness.proveedores.base import MOTIVO_CAIDO, MOTIVO_CUOTA_DIARIA, ErrorProveedorCaido
from harness.proveedores.reintentos import (
    ESPERA_MAXIMA_SEGUNDOS,
    ErrorPasajero,
    Reintentos,
    espera_de_retry_after,
)


class ErrorDePrueba(Exception):
    def __init__(self, pasajero: ErrorPasajero | None) -> None:
        super().__init__("falla")
        self.pasajero = pasajero


def _pedido(resultados: list):
    pendientes = list(resultados)

    def pedir() -> str:
        resultado = pendientes.pop(0)
        if isinstance(resultado, BaseException):
            raise resultado
        return resultado

    return pedir


def _clasificar(error: BaseException) -> ErrorPasajero | None:
    return error.pasajero if isinstance(error, ErrorDePrueba) else None


def test_reintenta_lo_pasajero_con_espera_creciente_y_avisa() -> None:
    esperas: list[float] = []
    avisos: list[str] = []
    reintentos = Reintentos("groq", 3, esperas.append, lambda: 0.0, avisos.append)
    pasajero = ErrorPasajero("saturado (503)")
    pedido = _pedido([ErrorDePrueba(pasajero), ErrorDePrueba(pasajero), "ok"])
    assert reintentos.pedir(pedido, _clasificar, (ErrorDePrueba,)) == "ok"
    assert esperas == [5.0, 10.0]
    assert avisos[0] == "Groq saturado (503), reintento 1/3 en 5 s..."


def test_lo_que_no_es_pasajero_se_propaga_tal_cual() -> None:
    reintentos = Reintentos("groq", 3, lambda _: pytest.fail("no debía esperar"))
    with pytest.raises(ErrorDePrueba):
        reintentos.pedir(_pedido([ErrorDePrueba(None)]), _clasificar, (ErrorDePrueba,))
    with pytest.raises(ValueError):
        reintentos.pedir(_pedido([ValueError("otro")]), _clasificar, (ErrorDePrueba,))


def test_al_agotar_queda_caido_y_la_cuota_diaria_no_se_reintenta() -> None:
    reintentos = Reintentos("groq", 1, lambda _: None)
    caido = ErrorDePrueba(ErrorPasajero("sin conexión"))
    with pytest.raises(ErrorProveedorCaido) as error:
        reintentos.pedir(_pedido([caido, caido]), _clasificar, (ErrorDePrueba,))
    assert error.value.motivo == MOTIVO_CAIDO
    cuota = ErrorDePrueba(ErrorPasajero("límite", es_limite=True, es_cuota_diaria=True))
    with pytest.raises(ErrorProveedorCaido) as error:
        reintentos.pedir(_pedido([cuota]), _clasificar, (ErrorDePrueba,))
    assert error.value.motivo == MOTIVO_CUOTA_DIARIA


def test_intervalo_minimo_entre_pedidos() -> None:
    esperas: list[float] = []
    horas = iter([10.0, 11.0])
    reintentos = Reintentos("gemini", 1, esperas.append, lambda: next(horas),
                            intervalo_minimo=4.0)
    reintentos.pedir(_pedido(["a"]), _clasificar, (ErrorDePrueba,))
    reintentos.pedir(_pedido(["b"]), _clasificar, (ErrorDePrueba,))
    assert esperas == [3.0]


@pytest.mark.parametrize(
    ("valor", "esperado"), [("7", 7.0), ("999", ESPERA_MAXIMA_SEGUNDOS), ("x", None), (None, None)]
)
def test_espera_de_retry_after(valor: str | None, esperado: float | None) -> None:
    assert espera_de_retry_after(valor) == esperado
