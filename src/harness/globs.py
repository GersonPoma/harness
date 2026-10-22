"""Globs relativos a la raíz del repo, como los de archivos_protegidos en harness.yaml.

Propios porque PurePath.full_match (que entiende '**') no existe en Python 3.12.
- '*' y '?' no cruzan carpetas.
- '**' cruza cualquier cantidad de carpetas, incluso ninguna.
"""

import re
from collections.abc import Iterable
from functools import cache


def coincide_alguno(ruta: str, patrones: Iterable[str]) -> bool:
    """Indica si una ruta relativa con '/' coincide con alguno de los globs."""
    return any(_a_regex(patron).fullmatch(ruta) for patron in patrones)


@cache
def _a_regex(patron: str) -> re.Pattern[str]:
    partes: list[str] = []
    i = 0
    while i < len(patron):
        if patron.startswith("**/", i):
            partes.append("(?:.*/)?")
            i += 3
        elif patron.startswith("/**", i) and i + 3 == len(patron):
            partes.append("(?:/.*)?")
            i += 3
        elif patron.startswith("**", i):
            partes.append(".*")
            i += 2
        elif patron[i] == "*":
            partes.append("[^/]*")
            i += 1
        elif patron[i] == "?":
            partes.append("[^/]")
            i += 1
        else:
            partes.append(re.escape(patron[i]))
            i += 1
    # Sin distinguir mayúsculas: en Windows 'Docs/' y 'docs/' son la misma carpeta, y para
    # proteger archivos es mejor equivocarse de más que de menos.
    return re.compile("".join(partes), re.IGNORECASE | re.DOTALL)
