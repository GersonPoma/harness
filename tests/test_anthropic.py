"""ProveedorAnthropic con un cliente falso: la API de Anthropic es de pago y estos tests no
necesitan key."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from harness.config import ConfigModelo
from harness.proveedores.anthropic import (
    CACHE_DEL_PROMPT,
    ERROR_CORTADA,
    MAX_TOKENS_RESPUESTA,
    ProveedorAnthropic,
    a_mensajes_anthropic,
    normalizar_id,
)
from harness.proveedores.base import (
    MOTIVO_CAIDO,
    MOTIVO_LIMITE,
    ErrorProveedor,
    ErrorProveedorCaido,
)
from harness.proveedores.fabrica import crear_proveedor
from harness.proveedores.presets import PRESETS, FormatoApi
from harness.proveedores.reintentos import ESPERA_INICIAL_SEGUNDOS, MAX_REINTENTOS
from harness.proveedores.respaldo import ProveedorConRespaldo
from harness.tipos import Herramienta, LlamadaHerramienta, Mensaje, Rol
from tests.falsos import ProveedorFalso, terminar

CONFIG = ConfigModelo(
    "anthropic", "claude-sonnet-5-5", None, "clave-anthropic-0123456789", "ANTHROPIC_API_KEY"
)
PEDIDO = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
DATOS = Path(__file__).parent / "datos"


class ClienteFalso:
    """Imita anthropic.Anthropic: guarda cada pedido y devuelve (o lanza) lo guionado."""

    def __init__(self, resultados: list[Any]) -> None:
        self._resultados = list(resultados)
        self.pedidos: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._crear)

    def _crear(self, **parametros: Any) -> Any:
        self.pedidos.append(json.loads(json.dumps(parametros)))
        resultado = self._resultados.pop(0)
        if isinstance(resultado, BaseException):
            raise resultado
        return resultado


def respuesta(*bloques: Any, stop: str = "end_turn", entrada: int = 100) -> Any:
    uso = SimpleNamespace(
        input_tokens=entrada, output_tokens=20, cache_creation_input_tokens=30,
        cache_read_input_tokens=50,
    )
    return SimpleNamespace(content=list(bloques), stop_reason=stop, usage=uso)


def texto(contenido: str) -> Any:
    return SimpleNamespace(type="text", text=contenido)


def uso_de(id_bloque: str, nombre: str, entrada: dict) -> Any:
    return SimpleNamespace(type="tool_use", id=id_bloque, name=nombre, input=entrada)


def error_http(
    clase: type[anthropic.APIStatusError], estado: int, retry: str | None = None
) -> anthropic.APIStatusError:
    cabeceras = {"retry-after": retry} if retry else {}
    return clase("falla", response=httpx2.Response(estado, headers=cabeceras, request=PEDIDO),
                 body=None)


def crear(
    resultados: list[Any], avisos: list[str] | None = None, reintentos: int = MAX_REINTENTOS
) -> tuple[ProveedorAnthropic, ClienteFalso, list[float]]:
    cliente = ClienteFalso(resultados)
    esperas: list[float] = []
    proveedor = ProveedorAnthropic(
        CONFIG, cliente=cliente, dormir=esperas.append, reloj=lambda: 0.0,
        al_reintentar=avisos.append if avisos is not None else None, max_reintentos=reintentos,
    )
    return proveedor, cliente, esperas


LEER = Herramienta(
    "leer_archivo", "Lee un archivo.",
    {"type": "object", "properties": {"ruta": {"type": "string"}}, "required": ["ruta"]},
    lambda ruta: ruta,
)


# --- Traducción de los tipos propios -------------------------------------------------------


def test_el_system_va_aparte_y_los_roles_se_alternan() -> None:
    mensajes = [
        Mensaje(Rol.SISTEMA, "Eres el harness."),
        Mensaje(Rol.USUARIO, "Ticket T-1"),
        Mensaje(Rol.ASISTENTE, "Leo dos archivos.", [
            LlamadaHerramienta("call_1", "leer_archivo", {"ruta": "a.py"}),
            LlamadaHerramienta("call_2", "leer_archivo", {"ruta": "b.py"}),
        ]),
        Mensaje(Rol.HERRAMIENTA, "contenido a", id_llamada="call_1"),
        Mensaje(Rol.HERRAMIENTA, "contenido b", id_llamada="call_2"),
        # Un pedido de validación justo después de los resultados: va en el mismo mensaje.
        Mensaje(Rol.USUARIO, "Los tests fallaron."),
    ]
    proveedor, cliente, _ = crear([respuesta(texto("ok"))])
    proveedor.responder(mensajes, [LEER])
    (pedido,) = cliente.pedidos
    assert pedido["system"] == "Eres el harness."
    assert [m["role"] for m in pedido["messages"]] == ["user", "assistant", "user"]
    assert pedido["messages"][1]["content"] == [
        {"type": "text", "text": "Leo dos archivos."},
        {"type": "tool_use", "id": "call_1", "name": "leer_archivo", "input": {"ruta": "a.py"}},
        {"type": "tool_use", "id": "call_2", "name": "leer_archivo", "input": {"ruta": "b.py"}},
    ]
    assert pedido["messages"][2]["content"] == [
        {"type": "tool_result", "tool_use_id": "call_1", "content": "contenido a"},
        {"type": "tool_result", "tool_use_id": "call_2", "content": "contenido b"},
        {"type": "text", "text": "Los tests fallaron."},
    ]
    assert pedido["max_tokens"] == MAX_TOKENS_RESPUESTA
    assert pedido["cache_control"] == CACHE_DEL_PROMPT
    assert pedido["tools"] == [{
        "name": "leer_archivo", "description": "Lee un archivo.",
        "input_schema": LEER.parametros,
    }]
    assert "tool_choice" not in pedido


def test_un_asistente_sin_texto_no_manda_un_bloque_vacio() -> None:
    mensajes = [
        Mensaje(Rol.USUARIO, "hola"),
        Mensaje(Rol.ASISTENTE, None, [LlamadaHerramienta("c", "leer_archivo", {"ruta": "x"})]),
        Mensaje(Rol.HERRAMIENTA, "x", id_llamada="c"),
    ]
    convertidos = a_mensajes_anthropic(mensajes)
    assert [b["type"] for b in convertidos[1]["content"]] == ["tool_use"]


def test_los_ids_de_otro_proveedor_se_normalizan_igual_en_los_dos_lados() -> None:
    # Una conversación que llega del respaldo con ids que Anthropic no acepta.
    mensajes = [
        Mensaje(Rol.USUARIO, "hola"),
        Mensaje(Rol.ASISTENTE, None, [LlamadaHerramienta("fc.1/a b", "leer_archivo", {})]),
        Mensaje(Rol.HERRAMIENTA, "x", id_llamada="fc.1/a b"),
    ]
    convertidos = a_mensajes_anthropic(mensajes)
    assert convertidos[1]["content"][0]["id"] == "fc_1_a_b"
    assert convertidos[2]["content"][0]["tool_use_id"] == "fc_1_a_b"
    assert normalizar_id("") == "llamada"


def test_herramienta_obligatoria_va_como_tool_choice() -> None:
    proveedor, cliente, _ = crear([respuesta(uso_de("u1", "leer_archivo", {"ruta": "a"}))])
    proveedor.responder([Mensaje(Rol.USUARIO, "hola")], [LEER], "leer_archivo")
    assert cliente.pedidos[0]["tool_choice"] == {"type": "tool", "name": "leer_archivo"}


def test_la_respuesta_en_tipos_propios() -> None:
    proveedor, _, _ = crear([respuesta(
        texto("Leo el archivo. "), texto("Y sigo."), uso_de("toolu_1", "leer_archivo",
                                                             {"ruta": "a.py"}),
    )])
    resultado = proveedor.responder([Mensaje(Rol.USUARIO, "hola")], [LEER])
    assert resultado.texto == "Leo el archivo. Y sigo."
    assert resultado.llamadas == [LlamadaHerramienta(
        "toolu_1", "leer_archivo", {"ruta": "a.py"}, origen="anthropic/claude-sonnet-5-5"
    )]
    # La entrada suma lo leído de la caché y lo que se escribió en ella.
    assert (resultado.tokens_entrada, resultado.tokens_salida) == (180, 20)


def test_una_llamada_cortada_por_max_tokens_vuelve_como_error() -> None:
    proveedor, _, _ = crear([respuesta(
        uso_de("u1", "escribir_archivo", {"ruta": "a.py"}), stop="max_tokens"
    )])
    (llamada,) = proveedor.responder([Mensaje(Rol.USUARIO, "hola")], [LEER]).llamadas
    assert llamada.error_argumentos == ERROR_CORTADA


def test_solo_texto_termina() -> None:
    proveedor, _, _ = crear([respuesta(texto("Listo."))])
    resultado = proveedor.responder([Mensaje(Rol.USUARIO, "hola")], [])
    assert (resultado.texto, resultado.llamadas) == ("Listo.", [])


# --- adaptar_esquema ----------------------------------------------------------------------


def test_adaptar_esquema_quita_extensiones_y_garantiza_un_objeto() -> None:
    proveedor, _, _ = crear([])
    assert proveedor.adaptar_esquema({}) == {"type": "object", "properties": {}}
    esquema = {
        "type": "object",
        "properties": {"owner": {"type": "string", "x-mcp-header": "Owner"}},
        "$defs": {"a": {"type": "string"}},
    }
    assert proveedor.adaptar_esquema(esquema) == {
        "type": "object", "properties": {"owner": {"type": "string"}},
        "$defs": {"a": {"type": "string"}},
    }


def test_adaptar_esquema_con_los_esquemas_reales_de_github() -> None:
    proveedor, _, _ = crear([])
    esquemas = json.loads((DATOS / "esquemas_github.json").read_text(encoding="utf-8"))
    for esquema in esquemas.values():
        adaptado = proveedor.adaptar_esquema(esquema)
        assert adaptado["type"] == "object" and "x-mcp-header" not in json.dumps(adaptado)


# --- Reintentos y errores -----------------------------------------------------------------


def test_reintenta_429_529_5xx_y_sin_conexion_y_respeta_retry_after() -> None:
    avisos: list[str] = []
    resultados = [
        error_http(anthropic.RateLimitError, 429, retry="7"),
        error_http(anthropic.OverloadedError, 529),
        error_http(anthropic.InternalServerError, 500),
        anthropic.APIConnectionError(request=PEDIDO),
        respuesta(texto("ok")),
    ]
    proveedor, cliente, esperas = crear(resultados, avisos)
    assert proveedor.responder([Mensaje(Rol.USUARIO, "hola")], []).texto == "ok"
    assert len(cliente.pedidos) == 5
    assert esperas == [7.0, ESPERA_INICIAL_SEGUNDOS * 2, ESPERA_INICIAL_SEGUNDOS * 4,
                       ESPERA_INICIAL_SEGUNDOS * 8]
    assert avisos[0] == (
        f"Anthropic con límite de pedidos (429), reintento 1/{MAX_REINTENTOS} en 7 s..."
    )
    assert avisos[1].startswith("Anthropic sobrecargado (529), reintento 2/")


def test_sin_intervalo_minimo_entre_pedidos() -> None:
    proveedor, _, esperas = crear([respuesta(texto("a")), respuesta(texto("b"))])
    proveedor.responder([Mensaje(Rol.USUARIO, "hola")], [])
    proveedor.responder([Mensaje(Rol.USUARIO, "hola")], [])
    assert esperas == []


@pytest.mark.parametrize(
    ("error", "motivo"),
    [
        (lambda: error_http(anthropic.RateLimitError, 429), MOTIVO_LIMITE),
        (lambda: error_http(anthropic.OverloadedError, 529), MOTIVO_CAIDO),
        (lambda: anthropic.APIConnectionError(request=PEDIDO), MOTIVO_CAIDO),
    ],
)
def test_al_agotar_los_reintentos_queda_caido(error, motivo: str) -> None:
    proveedor, cliente, _ = crear([error() for _ in range(3)], reintentos=2)
    with pytest.raises(ErrorProveedorCaido) as excepcion:
        proveedor.responder([Mensaje(Rol.USUARIO, "hola")], [])
    assert excepcion.value.motivo == motivo and len(cliente.pedidos) == 3


def test_api_key_rechazada_dice_de_donde_sale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "clave-anthropic-0123456789")
    proveedor, cliente, _ = crear([error_http(anthropic.AuthenticationError, 401)])
    with pytest.raises(ErrorProveedor, match="rechazó la API key: revisa ANTHROPIC_API_KEY "
                       r"\(viene del entorno\)") as excepcion:
        proveedor.responder([Mensaje(Rol.USUARIO, "hola")], [])
    assert not isinstance(excepcion.value, ErrorProveedorCaido)
    assert len(cliente.pedidos) == 1


@pytest.mark.parametrize(
    ("clase", "estado"), [(anthropic.BadRequestError, 400), (anthropic.NotFoundError, 404)]
)
def test_400_y_404_no_se_reintentan(clase: type[anthropic.APIStatusError], estado: int) -> None:
    proveedor, cliente, _ = crear([error_http(clase, estado)])
    with pytest.raises(ErrorProveedor, match=f"respondió con error {estado}"):
        proveedor.responder([Mensaje(Rol.USUARIO, "hola")], [])
    assert len(cliente.pedidos) == 1


def test_si_anthropic_se_cae_el_respaldo_sigue_con_el_siguiente() -> None:
    principal, _, _ = crear([error_http(anthropic.OverloadedError, 529) for _ in range(2)],
                            reintentos=1)
    siguiente = ProveedorFalso([terminar("Sigo yo")])
    cambios: list = []
    cadena = ProveedorConRespaldo(
        [("anthropic/claude", principal), ("openai/gpt", siguiente)], cambios.append
    )
    assert cadena.responder([Mensaje(Rol.USUARIO, "hola")], []).texto == "Sigo yo"
    assert [(c.anterior, c.siguiente, c.motivo) for c in cambios] == [
        ("anthropic/claude", "openai/gpt", MOTIVO_CAIDO)
    ]


# --- Preset y fábrica ---------------------------------------------------------------------


def test_preset_anthropic_y_fabrica() -> None:
    preset = PRESETS["anthropic"]
    assert (preset.formato, preset.variable_api_key, preset.es_con_tool_choice) == (
        FormatoApi.ANTHROPIC, "ANTHROPIC_API_KEY", True
    )
    assert isinstance(crear_proveedor(CONFIG), ProveedorAnthropic)
