"""Lectura de tickets locales (la integración con Jira llega en la fase 6)."""

from pathlib import Path

EXTENSIONES_TICKET = {".md"}


class ErrorTicket(Exception):
    """El ticket no existe o no se puede usar."""


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
