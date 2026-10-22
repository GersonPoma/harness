"""Archivos del repo objetivo que pueden tener secretos reales (.env y .env.*).

El modelo no los puede leer ni escribir con sus herramientas, y dentro del contenedor se ven
vacíos: lo que el modelo lee termina en la API del proveedor.
"""

import os
from pathlib import Path

from harness.carpetas import CARPETAS_IGNORADAS

ARCHIVO_SECRETOS = ".env"
# La plantilla sin valores reales que se sube a Git.
EXCEPCIONES_SECRETOS = (".env.example",)


def es_archivo_de_secretos(nombre: str) -> bool:
    """True para .env y .env.* (en cualquier carpeta), salvo .env.example."""
    nombre = nombre.lower()
    if nombre in EXCEPCIONES_SECRETOS:
        return False
    return nombre == ARCHIVO_SECRETOS or nombre.startswith(f"{ARCHIVO_SECRETOS}.")


def buscar_archivos_de_secretos(repo: Path) -> list[str]:
    """Rutas relativas (con '/') de los .env del repo, también en subcarpetas."""
    raiz = repo.resolve()
    encontrados = []
    for carpeta, subcarpetas, archivos in os.walk(raiz):
        subcarpetas[:] = [s for s in subcarpetas if s not in CARPETAS_IGNORADAS]
        base = Path(carpeta).relative_to(raiz)
        for nombre in archivos:
            if es_archivo_de_secretos(nombre) and (Path(carpeta) / nombre).is_file():
                encontrados.append((base / nombre).as_posix())
    return sorted(encontrados)
