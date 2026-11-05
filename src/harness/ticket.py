"""Tickets: un .md local o un issue de Jira, normalizados al mismo formato para el modelo."""

from dataclasses import dataclass
from pathlib import Path

EXTENSIONES_TICKET = {".md"}
FUENTE_LOCAL = "local"
FUENTE_JIRA = "jira"
# Una descripción enorme se come el contexto del modelo en cada pedido; con esto entra de sobra
# cualquier ticket bien escrito.
MAX_CARACTERES_DESCRIPCION = 20_000


class ErrorTicket(Exception):
    """El ticket no existe o no se puede usar."""


@dataclass(frozen=True)
class Ticket:
    """Ticket normalizado: venga del disco o de Jira, el modelo lo recibe igual.

    Solo los campos que usa el harness: nada del autor (email, accountId, avatar).
    """

    clave: str
    titulo: str
    # Markdown. Para un ticket local es el archivo completo, con su título.
    descripcion: str
    fuente: str
    tipo: str | None = None
    prioridad: str | None = None
    estado: str | None = None
    # Para mostrarla en consola y en el log; al modelo no le sirve.
    url: str | None = None
    # Largo de la descripción antes de recortarla, si se recortó.
    caracteres_originales: int | None = None

    @property
    def es_recortado(self) -> bool:
        """True si la descripción se recortó por pasar de MAX_CARACTERES_DESCRIPCION."""
        return self.caracteres_originales is not None

    def a_markdown(self) -> str:
        """Lo que recibe el modelo, con el formato de los tickets .md locales."""
        if self.fuente == FUENTE_LOCAL:
            return self.descripcion
        lineas = [f"# {self.clave}: {self.titulo}", ""]
        datos = [("Tipo", self.tipo), ("Prioridad", self.prioridad), ("Estado", self.estado)]
        lineas += [f"**{nombre}:** {valor}" for nombre, valor in datos if valor]
        return "\n".join(lineas).strip() + f"\n\n{self.descripcion}".rstrip()


def limitar_descripcion(texto: str) -> tuple[str, int | None]:
    """Recorta por el final si pasa del máximo; devuelve el texto y el largo original (o None)."""
    if len(texto) <= MAX_CARACTERES_DESCRIPCION:
        return texto, None
    marca = (
        f"\n\n[... se omitieron {len(texto) - MAX_CARACTERES_DESCRIPCION} de {len(texto)} "
        "caracteres de la descripción ...]"
    )
    return texto[:MAX_CARACTERES_DESCRIPCION] + marca, len(texto)


def titulo_ticket(texto: str, clave: str) -> str:
    """Primera línea del ticket, sin '#' ni la clave: "# T-1: Crear tareas" -> "Crear tareas"."""
    primera = next((linea for linea in texto.splitlines() if linea.strip()), "")
    titulo = primera.lstrip("#").strip()
    prefijo = f"{clave}:"
    if titulo.lower().startswith(prefijo.lower()):
        titulo = titulo[len(prefijo):].strip()
    return titulo


def leer_ticket_local(ruta: Path) -> str:
    """Devuelve el texto de un ticket guardado en un archivo .md."""
    if ruta.suffix.lower() not in EXTENSIONES_TICKET:
        raise ErrorTicket(f"El ticket '{ruta}' debe ser un archivo .md.")
    if not ruta.is_file():
        raise ErrorTicket(f"No existe el ticket '{ruta}'.")
    texto = ruta.read_text(encoding="utf-8").strip()
    if not texto:
        raise ErrorTicket(f"El ticket '{ruta}' está vacío.")
    return texto
