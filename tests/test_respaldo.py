import pytest

from harness.config import ConfigModelo, ErrorConfiguracion, leer_respaldo
from harness.proveedores.base import (
    MOTIVO_CAIDO,
    MOTIVO_CUOTA_DIARIA,
    ErrorProveedor,
    ErrorProveedorCaido,
)
from harness.proveedores.fabrica import REINTENTOS_CON_RESPALDO, crear_proveedor
from harness.proveedores.openai_compatible import MAX_REINTENTOS
from harness.proveedores.respaldo import CambioProveedor, ProveedorConRespaldo
from harness.tipos import Mensaje, Rol
from tests.falsos import ProveedorFalso, terminar

MENSAJES = [Mensaje(Rol.USUARIO, "hola")]
CAIDO = ErrorProveedorCaido("Gemini no está disponible (503).", MOTIVO_CAIDO)


def _cadena(*proveedores: ProveedorFalso) -> tuple[ProveedorConRespaldo, list[CambioProveedor]]:
    cambios: list[CambioProveedor] = []
    nombres = ["gemini/a", "openrouter/b:free", "groq/c"]
    cadena = ProveedorConRespaldo(list(zip(nombres, proveedores)), cambios.append)
    return cadena, cambios


def test_sin_caidas_responde_el_primero() -> None:
    primero, segundo = ProveedorFalso([terminar("uno")]), ProveedorFalso([])
    cadena, cambios = _cadena(primero, segundo)
    assert cadena.responder(MENSAJES, []).texto == "uno"
    assert cambios == [] and cadena.actual == "gemini/a" and cadena.usados == ["gemini/a"]


def test_si_se_cae_sigue_con_el_siguiente_y_la_misma_conversacion() -> None:
    primero = ProveedorFalso([CAIDO])
    segundo = ProveedorFalso([terminar("dos"), terminar("tres")])
    cadena, cambios = _cadena(primero, segundo)

    assert cadena.responder(MENSAJES, []).texto == "dos"
    assert segundo.conversaciones[0] == MENSAJES
    assert cambios == [CambioProveedor("gemini/a", "openrouter/b:free", MOTIVO_CAIDO, str(CAIDO))]
    # No vuelve atrás: el siguiente pedido también va al segundo.
    assert cadena.responder(MENSAJES, []).texto == "tres"
    assert cadena.actual == "openrouter/b:free"
    assert cadena.usados == ["gemini/a", "openrouter/b:free"]


def test_salta_varios_hasta_el_que_responde() -> None:
    cuota = ErrorProveedorCaido("cuota diaria", MOTIVO_CUOTA_DIARIA)
    cadena, cambios = _cadena(
        ProveedorFalso([CAIDO]), ProveedorFalso([cuota]), ProveedorFalso([terminar("tres")])
    )
    assert cadena.responder(MENSAJES, []).texto == "tres"
    assert [c.motivo for c in cambios] == [MOTIVO_CAIDO, MOTIVO_CUOTA_DIARIA]


def test_si_cae_el_ultimo_se_propaga() -> None:
    cadena, _ = _cadena(ProveedorFalso([CAIDO]), ProveedorFalso([CAIDO]))
    with pytest.raises(ErrorProveedorCaido):
        cadena.responder(MENSAJES, [])


def test_un_error_de_configuracion_no_pasa_al_siguiente() -> None:
    clave = ErrorProveedor("El proveedor 'gemini' rechazó la API key: revisa el .env.")
    segundo = ProveedorFalso([terminar()])
    cadena, cambios = _cadena(ProveedorFalso([clave]), segundo)
    with pytest.raises(ErrorProveedor, match="API key"):
        cadena.responder(MENSAJES, [])
    assert cambios == [] and segundo.conversaciones == []


# --- Fábrica -------------------------------------------------------------------------------

PRINCIPAL = ConfigModelo("gemini", "g", "https://x", "clave-falsa-0123456789")
RESPALDO = ConfigModelo("openrouter", "o:free", "https://y", "clave-falsa-9876543210")


def test_sin_respaldo_es_un_proveedor_solo() -> None:
    proveedor = crear_proveedor(PRINCIPAL)
    assert not isinstance(proveedor, ProveedorConRespaldo)
    assert proveedor._max_reintentos == MAX_REINTENTOS


def test_con_respaldo_menos_reintentos_salvo_el_ultimo() -> None:
    cadena = crear_proveedor(PRINCIPAL, respaldo=[RESPALDO])
    assert isinstance(cadena, ProveedorConRespaldo)
    (nombre_1, primero), (nombre_2, ultimo) = cadena._proveedores
    assert (nombre_1, nombre_2) == ("gemini/g", "openrouter/o:free")
    assert primero._max_reintentos == REINTENTOS_CON_RESPALDO
    assert ultimo._max_reintentos == MAX_REINTENTOS


# --- HARNESS_RESPALDO ----------------------------------------------------------------------


def test_leer_respaldo_vacio(monkeypatch: pytest.MonkeyPatch) -> None:
    assert leer_respaldo() == []
    monkeypatch.setenv("HARNESS_RESPALDO", " , ")
    assert leer_respaldo() == []


def test_leer_respaldo_separa_en_el_primer_dos_puntos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_RESPALDO", "OpenRouter:qwen/qwen3-coder:free, groq:llama-3")
    monkeypatch.setenv("OPENROUTER_API_KEY", "clave-openrouter-0123456789")
    monkeypatch.setenv("GROQ_API_KEY", "clave-groq-0123456789")
    respaldo = leer_respaldo()
    assert [(c.proveedor, c.modelo) for c in respaldo] == [
        ("openrouter", "qwen/qwen3-coder:free"), ("groq", "llama-3"),
    ]
    assert respaldo[0].base_url == "https://openrouter.ai/api/v1"
    assert respaldo[0].api_key == "clave-openrouter-0123456789"
    assert respaldo[0].variable_api_key == "OPENROUTER_API_KEY"


def test_leer_respaldo_quita_el_modelo_actual(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_RESPALDO", "openrouter:nemotron:free, openrouter:gemma:free")
    monkeypatch.setenv("OPENROUTER_API_KEY", "clave-openrouter-0123456789")
    actual = ConfigModelo("openrouter", "gemma:free", None, "clave-openrouter-0123456789")
    assert [c.modelo for c in leer_respaldo(actual)] == ["nemotron:free"]
    # El mismo modelo con otro proveedor es otro: se queda.
    otro = ConfigModelo("groq", "nemotron:free", None, "clave-groq-0123456789")
    assert [c.modelo for c in leer_respaldo(otro)] == ["nemotron:free", "gemma:free"]


@pytest.mark.parametrize(
    ("valor", "mensaje"),
    [
        ("openrouter", "no es proveedor:modelo"),
        ("openrouter:", "no es proveedor:modelo"),
        ("otro:modelo", "Proveedor 'otro' no válido en HARNESS_RESPALDO"),
        ("personalizado:modelo", "Proveedor 'personalizado' no válido"),
        ("groq:llama-3", "Falta GROQ_API_KEY en el .env"),
    ],
)
def test_leer_respaldo_con_errores(
    monkeypatch: pytest.MonkeyPatch, valor: str, mensaje: str
) -> None:
    monkeypatch.setenv("HARNESS_RESPALDO", valor)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(ErrorConfiguracion, match=mensaje):
        leer_respaldo()
