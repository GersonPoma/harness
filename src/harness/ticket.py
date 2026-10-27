"""Lectura de tickets locales (la integración con Jira llega en la fase 7)."""

from pathlib import Path

EXTENSIONES_TICKET = {".md"}


class ErrorTicket(Exception):
    """El ticket no existe o no se puede usar."""


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
