import json
from dataclasses import asdict
from types import SimpleNamespace
from typing import Any

import httpx2
import openai
import pytest

from harness.config import ConfigModelo
from harness.proveedores import openai_compatible
from harness.proveedores.base import ErrorProveedor
from harness.proveedores.openai_compatible import (
    INTERVALO_MINIMO_SEGUNDOS,
    MAX_REINTENTOS_429,
    ProveedorOpenAICompatible,
)
from harness.tipos import Herramienta, LlamadaHerramienta, Mensaje, Rol

CONFIG = ConfigModelo("gemini", "modelo-x", "https://ejemplo/v1", "clave")


class ClienteFalso:
    """Imita client.chat.completions.create devolviendo (o lanzando) lo guionado."""

    def __init__(self, resultados: list[Any]) -> None:
        self._resultados = list(resultados)
        self.pedidos: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._crear))

    def _crear(self, **parametros: Any) -> Any:
        self.pedidos.append(parametros)
        resultado = self._resultados.pop(0)
        if isinstance(resultado, Exception):
            raise resultado
        return resultado


class Reloj:
    """Reloj falso: dormir avanza el tiempo sin esperar de verdad."""

    def __init__(self) -> None:
        self.ahora = 0.0
        self.esperas: list[float] = []

    def __call__(self) -> float:
        return self.ahora

    def dormir(self, segundos: float) -> None:
        self.esperas.append(segundos)
        self.ahora += segundos


def respuesta_openai(texto: str | None = None, llamadas: list[Any] | None = None) -> Any:
    mensaje = SimpleNamespace(content=texto, tool_calls=llamadas)
    uso = SimpleNamespace(prompt_tokens=12, completion_tokens=3)
    return SimpleNamespace(choices=[SimpleNamespace(message=mensaje)], usage=uso)


def llamada_openai(
    id_llamada: str | None, nombre: str, argumentos: str, extra: dict[str, Any] | None = None
) -> Any:
    funcion = SimpleNamespace(name=nombre, arguments=argumentos)
    # model_extra imita a pydantic: los campos fuera del estándar OpenAI.
    return SimpleNamespace(id=id_llamada, function=funcion, model_extra=extra or {})


def error_429(retry_after: str | None = None) -> openai.RateLimitError:
    cabeceras = {"retry-after": retry_after} if retry_after else {}
    respuesta = httpx2.Response(
        429, headers=cabeceras, request=httpx2.Request("POST", "https://ejemplo/v1")
    )
    return openai.RateLimitError("límite", response=respuesta, body=None)


def crear(resultados: list[Any]) -> tuple[ProveedorOpenAICompatible, ClienteFalso, Reloj]:
    cliente = ClienteFalso(resultados)
    reloj = Reloj()
    proveedor = ProveedorOpenAICompatible(CONFIG, cliente, dormir=reloj.dormir, reloj=reloj)
    return proveedor, cliente, reloj


HERRAMIENTA = Herramienta("leer_archivo", "Lee", {"type": "object"}, lambda ruta: ruta)


def test_traduce_mensajes_y_herramientas_al_formato_openai() -> None:
    proveedor, cliente, _ = crear([respuesta_openai("ok")])
    mensajes = [
        Mensaje(Rol.SISTEMA, "sis"),
        Mensaje(Rol.USUARIO, "hola"),
        Mensaje(Rol.ASISTENTE, None, [LlamadaHerramienta("c1", "leer_archivo", {"ruta": "ñ"})]),
        Mensaje(Rol.HERRAMIENTA, "contenido", id_llamada="c1"),
    ]
    proveedor.responder(mensajes, [HERRAMIENTA])

    pedido = cliente.pedidos[0]
    assert pedido["model"] == "modelo-x"
    assert [m["role"] for m in pedido["messages"]] == ["system", "user", "assistant", "tool"]
    llamada = pedido["messages"][2]["tool_calls"][0]
    assert llamada["function"] == {"name": "leer_archivo", "arguments": '{"ruta": "ñ"}'}
    assert pedido["messages"][3]["tool_call_id"] == "c1"
    assert pedido["tools"][0]["function"]["name"] == "leer_archivo"


def test_traduce_la_respuesta_a_tipos_propios() -> None:
    llamadas = [
        llamada_openai("c1", "leer_archivo", '{"ruta": "a.py"}'),
        llamada_openai(None, "listar_directorio", ""),
    ]
    proveedor, _, _ = crear([respuesta_openai(None, llamadas)])
    respuesta = proveedor.responder([Mensaje(Rol.USUARIO, "hola")], [])

    assert respuesta.llamadas == [
        LlamadaHerramienta("c1", "leer_archivo", {"ruta": "a.py"}),
        LlamadaHerramienta("llamada_1", "listar_directorio", {}),
    ]
    assert (respuesta.tokens_entrada, respuesta.tokens_salida) == (12, 3)


@pytest.mark.parametrize("argumentos", ["{ruta: a.py", '["a.py"]'])
def test_argumentos_invalidos_quedan_marcados(argumentos: str) -> None:
    llamadas = [llamada_openai("c1", "leer_archivo", argumentos)]
    proveedor, _, _ = crear([respuesta_openai(None, llamadas)])
    llamada = proveedor.responder([Mensaje(Rol.USUARIO, "hola")], []).llamadas[0]
    assert llamada.argumentos == {}
    assert llamada.error_argumentos


def test_reintenta_ante_429_y_respeta_retry_after() -> None:
    proveedor, cliente, reloj = crear([error_429("7"), error_429(), respuesta_openai("ok")])
    respuesta = proveedor.responder([Mensaje(Rol.USUARIO, "hola")], [])
    assert respuesta.texto == "ok"
    assert len(cliente.pedidos) == 3
    assert reloj.esperas == [7.0, openai_compatible.ESPERA_INICIAL_429_SEGUNDOS * 2]


def test_429_agotado_da_error_claro() -> None:
    proveedor, cliente, _ = crear([error_429() for _ in range(MAX_REINTENTOS_429 + 1)])
    with pytest.raises(ErrorProveedor, match="límite o la cuota.*'gemini'.*HARNESS_PROVEEDOR"):
        proveedor.responder([Mensaje(Rol.USUARIO, "hola")], [])
    assert len(cliente.pedidos) == MAX_REINTENTOS_429 + 1


def test_espera_el_intervalo_minimo_entre_pedidos() -> None:
    proveedor, _, reloj = crear([respuesta_openai("1"), respuesta_openai("2")])
    proveedor.responder([Mensaje(Rol.USUARIO, "hola")], [])
    reloj.ahora += 1.5
    proveedor.responder([Mensaje(Rol.USUARIO, "hola")], [])
    assert reloj.esperas == [INTERVALO_MINIMO_SEGUNDOS - 1.5]


def test_error_de_autenticacion_no_se_reintenta() -> None:
    respuesta = httpx2.Response(401, request=httpx2.Request("POST", "https://ejemplo/v1"))
    error = openai.AuthenticationError("no", response=respuesta, body=None)
    proveedor, cliente, _ = crear([error])
    with pytest.raises(ErrorProveedor, match="API key"):
        proveedor.responder([Mensaje(Rol.USUARIO, "hola")], [])
    assert len(cliente.pedidos) == 1


EXTRA_GEMINI = {"extra_content": {"google": {"thought_signature": "EmAKXg+firma/=="}}}


def _reenviar_llamadas(extra: dict[str, Any] | None) -> tuple[Any, dict[str, Any]]:
    """Pide una herramienta, devuelve su resultado y retorna (llamada, tool_call reenviado)."""
    llamadas = [llamada_openai("c1", "leer_archivo", '{"ruta": "AGENTS.md"}', extra)]
    proveedor, cliente, _ = crear([respuesta_openai(None, llamadas), respuesta_openai("ok")])
    mensajes = [Mensaje(Rol.USUARIO, "hola")]
    respuesta = proveedor.responder(mensajes, [HERRAMIENTA])
    mensajes += [
        Mensaje(Rol.ASISTENTE, respuesta.texto, respuesta.llamadas),
        Mensaje(Rol.HERRAMIENTA, "# AGENTS", id_llamada="c1"),
    ]
    proveedor.responder(mensajes, [HERRAMIENTA])
    return respuesta.llamadas[0], cliente.pedidos[1]["messages"][1]["tool_calls"][0]


def test_datos_del_proveedor_vuelven_identicos_en_el_siguiente_pedido() -> None:
    llamada, reenviada = _reenviar_llamadas(EXTRA_GEMINI)
    assert llamada.datos_proveedor == EXTRA_GEMINI
    assert reenviada["extra_content"] == EXTRA_GEMINI["extra_content"]
    assert reenviada["id"] == "c1"
    assert reenviada["function"]["name"] == "leer_archivo"


def test_sin_datos_del_proveedor_no_se_agrega_el_campo() -> None:
    llamada, reenviada = _reenviar_llamadas(None)
    assert llamada.datos_proveedor is None
    assert set(reenviada) == {"id", "type", "function"}


def test_llamada_con_datos_del_proveedor_se_guarda_en_json() -> None:
    llamada, _ = _reenviar_llamadas(EXTRA_GEMINI)
    restaurada = LlamadaHerramienta(**json.loads(json.dumps(asdict(llamada))))
    assert restaurada == llamada
