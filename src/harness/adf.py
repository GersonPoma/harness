"""ADF (Atlassian Document Format) a Markdown: así llegan al modelo los tickets de Jira.

Jira Cloud guarda la descripción como un árbol JSON de nodos. Cada equipo escribe sus tickets a
su manera (Markdown pegado, texto plano, casillas, tablas, paneles...), así que el conversor
cubre los nodos comunes y, ante uno que no conoce, saca su texto: nunca falla.
"""

from datetime import UTC, datetime
from typing import Any

SANGRIA_LISTA = "  "
TITULOS_PANEL = {
    "info": "Info",
    "note": "Nota",
    "success": "Éxito",
    "warning": "Advertencia",
    "error": "Error",
}
TITULO_PANEL_POR_DEFECTO = "Nota"
ESTADO_TAREA_HECHA = "DONE"
MAX_NIVEL_TITULO = 6
MILISEGUNDOS_POR_SEGUNDO = 1000


def adf_a_markdown(documento: Any) -> str:
    """Convierte un documento ADF a Markdown. Con un ADF roto o raro, devuelve lo que pueda."""
    if not isinstance(documento, dict):
        return ""
    try:
        return _unir_bloques(_hijos(documento)).strip()
    # Último recurso: un nodo con una forma inesperada no deja al modelo sin el ticket.
    except (AttributeError, KeyError, TypeError, ValueError):
        return _texto_plano(documento).strip()


# --- Bloques ------------------------------------------------------------------------------


def _unir_bloques(nodos: list[Any]) -> str:
    bloques = [_bloque(nodo) for nodo in nodos]
    return "\n\n".join(bloque for bloque in bloques if bloque.strip())


def _bloque(nodo: Any) -> str:
    if not isinstance(nodo, dict):
        return ""
    tipo = nodo.get("type")
    convertir = _BLOQUES.get(tipo)
    if convertir is not None:
        return convertir(nodo)
    if tipo in _INLINE:
        return _en_linea([nodo])
    # Nodo desconocido: si tiene bloques adentro (a cualquier profundidad, como las columnas
    # de un layout) se convierten; si no, queda su texto.
    hijos = _hijos(nodo)
    if any(n.get("type") in _BLOQUES for hijo in hijos for n in _recorrer(hijo)):
        return _unir_bloques(hijos)
    return _texto_plano(nodo)


def _parrafo(nodo: dict) -> str:
    return _en_linea(_hijos(nodo))


def _titulo(nodo: dict) -> str:
    nivel = _atributos(nodo).get("level", 1)
    nivel = min(max(nivel, 1), MAX_NIVEL_TITULO) if isinstance(nivel, int) else 1
    return f"{'#' * nivel} {_en_linea(_hijos(nodo))}"


def _regla(_: dict) -> str:
    return "---"


def _codigo(nodo: dict) -> str:
    lenguaje = _atributos(nodo).get("language") or ""
    texto = "".join(_texto_plano(hijo) for hijo in _hijos(nodo))
    return f"```{lenguaje}\n{texto}\n```"


def _cita(nodo: dict) -> str:
    return _con_prefijo(_unir_bloques(_hijos(nodo)), "> ")


def _panel(nodo: dict) -> str:
    tipo = _atributos(nodo).get("panelType")
    titulo = TITULOS_PANEL.get(tipo, TITULO_PANEL_POR_DEFECTO)
    return _con_prefijo(f"**{titulo}:** {_unir_bloques(_hijos(nodo))}", "> ")


def _desplegable(nodo: dict) -> str:
    titulo = _atributos(nodo).get("title") or ""
    contenido = _unir_bloques(_hijos(nodo))
    return f"**{titulo}**\n\n{contenido}" if titulo else contenido


def _medio(nodo: dict) -> str:
    # Imágenes y adjuntos: el modelo no los ve, pero tiene que saber que están.
    medios = [n for n in _recorrer(nodo) if n.get("type") in ("media", "mediaInline")]
    nombres = [_nombre_medio(medio) for medio in medios] or [_nombre_medio(nodo)]
    return "\n".join(f"[Imagen adjunta: {nombre}, no incluida]" for nombre in nombres)


def _nombre_medio(nodo: dict) -> str:
    atributos = _atributos(nodo)
    return str(atributos.get("alt") or atributos.get("id") or "sin nombre")


# --- Listas -------------------------------------------------------------------------------


def _lista_vinetas(nodo: dict) -> str:
    return "\n".join(_item_lista(item, "- ") for item in _hijos(nodo))


def _lista_numerada(nodo: dict) -> str:
    inicio = _atributos(nodo).get("order", 1)
    inicio = inicio if isinstance(inicio, int) else 1
    return "\n".join(
        _item_lista(item, f"{numero}. ") for numero, item in enumerate(_hijos(nodo), inicio)
    )


def _item_lista(item: Any, marcador: str) -> str:
    """El primer bloque va junto al marcador; los siguientes (sublistas), con sangría."""
    if not isinstance(item, dict):
        return marcador
    bloques = [_bloque(hijo) for hijo in _hijos(item)]
    bloques = [bloque for bloque in bloques if bloque.strip()] or [""]
    primero, *resto = bloques
    lineas = [f"{marcador}{_con_prefijo(primero, ' ' * len(marcador), desde_la_segunda=True)}"]
    lineas += [_con_prefijo(bloque, SANGRIA_LISTA) for bloque in resto]
    return "\n".join(lineas)


def _lista_tareas(nodo: dict) -> str:
    lineas = []
    for hijo in _hijos(nodo):
        if isinstance(hijo, dict) and hijo.get("type") == "taskList":
            # Lista de tareas anidada: va con sangría bajo la anterior.
            lineas.append(_con_prefijo(_lista_tareas(hijo), SANGRIA_LISTA))
        elif isinstance(hijo, dict):
            hecha = _atributos(hijo).get("state") == ESTADO_TAREA_HECHA
            lineas.append(f"- [{'x' if hecha else ' '}] {_contenido_tarea(hijo)}")
    return "\n".join(lineas)


def _contenido_tarea(item: dict) -> str:
    # El contenido de un taskItem suele ser texto suelto, pero puede traer párrafos.
    hijos = _hijos(item)
    if all(isinstance(h, dict) and h.get("type") in _INLINE for h in hijos):
        return _en_linea(hijos)
    return _unir_bloques(hijos).replace("\n\n", " ")


# --- Tablas -------------------------------------------------------------------------------


def _tabla(nodo: dict) -> str:
    filas = [
        [_celda(celda) for celda in _hijos(fila)]
        for fila in _hijos(nodo)
        if isinstance(fila, dict) and fila.get("type") == "tableRow"
    ]
    filas = [fila for fila in filas if fila]
    if not filas:
        return ""
    columnas = max(len(fila) for fila in filas)
    filas = [fila + [""] * (columnas - len(fila)) for fila in filas]
    # Markdown necesita una cabecera: es la primera fila, tenga tableHeader o no.
    cabecera, *cuerpo = filas
    lineas = [_fila(cabecera), _fila(["---"] * columnas)]
    lineas += [_fila(fila) for fila in cuerpo]
    return "\n".join(lineas)


def _celda(celda: Any) -> str:
    texto = _unir_bloques(_hijos(celda)) if isinstance(celda, dict) else ""
    return " ".join(texto.split()).replace("|", "\\|")


def _fila(celdas: list[str]) -> str:
    return "| " + " | ".join(celdas) + " |"


# --- En línea -----------------------------------------------------------------------------


def _en_linea(nodos: list[Any]) -> str:
    return "".join(_nodo_en_linea(nodo) for nodo in nodos)


def _nodo_en_linea(nodo: Any) -> str:
    if not isinstance(nodo, dict):
        return ""
    tipo = nodo.get("type")
    atributos = _atributos(nodo)
    if tipo == "text":
        return _con_marcas(str(nodo.get("text", "")), nodo.get("marks") or [])
    if tipo == "hardBreak":
        return "\n"
    if tipo == "mention":
        texto = str(atributos.get("text") or atributos.get("id") or "")
        return texto if texto.startswith("@") else f"@{texto}"
    if tipo == "emoji":
        return str(atributos.get("text") or atributos.get("shortName") or "")
    if tipo in ("inlineCard", "blockCard", "embedCard"):
        return str(atributos.get("url") or "")
    if tipo == "status":
        return f"[{atributos.get('text', '')}]"
    if tipo == "date":
        return _fecha(atributos.get("timestamp"))
    if tipo == "mediaInline":
        return f"[Imagen adjunta: {_nombre_medio(nodo)}, no incluida]"
    return _texto_plano(nodo)


def _con_marcas(texto: str, marcas: list[Any]) -> str:
    tipos = {m.get("type"): m for m in marcas if isinstance(m, dict)}
    # El código va solo: dentro de las comillas invertidas el resto de las marcas no se ve.
    if "code" in tipos:
        resultado = f"`{texto}`"
    else:
        resultado = texto
        if "strike" in tipos:
            resultado = f"~~{resultado}~~"
        if "em" in tipos:
            resultado = f"*{resultado}*"
        if "strong" in tipos:
            resultado = f"**{resultado}**"
    enlace = tipos.get("link")
    if enlace is not None:
        destino = _atributos(enlace).get("href")
        if destino:
            resultado = f"[{resultado}]({destino})"
    return resultado


def _fecha(marca: Any) -> str:
    try:
        segundos = int(marca) / MILISEGUNDOS_POR_SEGUNDO
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(segundos, UTC).date().isoformat()


# --- Utilidades ---------------------------------------------------------------------------


def _hijos(nodo: Any) -> list[Any]:
    contenido = nodo.get("content") if isinstance(nodo, dict) else None
    return contenido if isinstance(contenido, list) else []


def _atributos(nodo: Any) -> dict:
    atributos = nodo.get("attrs") if isinstance(nodo, dict) else None
    return atributos if isinstance(atributos, dict) else {}


def _recorrer(nodo: Any):
    if isinstance(nodo, dict):
        yield nodo
        for hijo in _hijos(nodo):
            yield from _recorrer(hijo)


def _texto_plano(nodo: Any) -> str:
    """Todo el texto de un nodo y sus hijos, sin formato (para nodos que no se conocen)."""
    if not isinstance(nodo, dict):
        return ""
    if nodo.get("type") == "text":
        return str(nodo.get("text", ""))
    if nodo.get("type") == "hardBreak":
        return "\n"
    return "".join(_texto_plano(hijo) for hijo in _hijos(nodo))


def _con_prefijo(texto: str, prefijo: str, desde_la_segunda: bool = False) -> str:
    """Antepone el prefijo a cada línea (a las vacías, sin espacios al final: "> " → ">")."""
    lineas = []
    for numero, linea in enumerate(texto.split("\n")):
        if desde_la_segunda and numero == 0:
            lineas.append(linea)
        elif not linea.strip():
            lineas.append(prefijo.rstrip())
        else:
            lineas.append(f"{prefijo}{linea}")
    return "\n".join(lineas)


_BLOQUES = {
    "paragraph": _parrafo,
    "heading": _titulo,
    "rule": _regla,
    "codeBlock": _codigo,
    "blockquote": _cita,
    "panel": _panel,
    "expand": _desplegable,
    "nestedExpand": _desplegable,
    "bulletList": _lista_vinetas,
    "orderedList": _lista_numerada,
    "taskList": _lista_tareas,
    "table": _tabla,
    "mediaSingle": _medio,
    "mediaGroup": _medio,
    "media": _medio,
}
_INLINE = {
    "text", "hardBreak", "mention", "emoji", "inlineCard", "status", "date", "mediaInline",
}
