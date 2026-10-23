"""Contexto del proyecto que el harness le da al modelo al empezar (AGENTS.md del repo)."""

from dataclasses import dataclass
from pathlib import Path

NOMBRE_AGENTS = "AGENTS.md"
# El mismo tope que leer_archivo: un AGENTS.md enorme se comería los tokens de cada pedido.
MAX_CARACTERES_AGENTS = 12_000


@dataclass(frozen=True)
class ContextoProyecto:
    """AGENTS.md ya leído (recortado si es muy largo), o nada si el repo no lo tiene."""

    agents_md: str | None = None
    caracteres_originales: int = 0

    @property
    def existe(self) -> bool:
        """True si el repo tiene AGENTS.md."""
        return self.agents_md is not None

    @property
    def tiene_contenido(self) -> bool:
        """True si el repo tiene AGENTS.md y no está vacío (solo espacios cuenta como vacío)."""
        return bool(self.agents_md and self.agents_md.strip())

    @property
    def es_recortado(self) -> bool:
        """True si AGENTS.md superaba el tope y se cortó."""
        return self.caracteres_originales > MAX_CARACTERES_AGENTS


def cargar_agents_md(repo: Path) -> ContextoProyecto:
    """Lee AGENTS.md de la raíz del repo; si no existe, devuelve un contexto vacío."""
    ruta = repo / NOMBRE_AGENTS
    if not ruta.is_file():
        return ContextoProyecto()
    # utf-8-sig quita la BOM que agregan algunos editores de Windows.
    texto = ruta.read_text(encoding="utf-8-sig", errors="replace")
    return ContextoProyecto(texto[:MAX_CARACTERES_AGENTS], len(texto))
