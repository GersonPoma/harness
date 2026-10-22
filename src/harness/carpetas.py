"""Carpetas del repo objetivo que el harness no recorre ni le muestra al modelo.

Vive fuera de herramientas/ para que entorno_docker pueda usarla sin import circular.
"""

CARPETAS_IGNORADAS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".idea"}
