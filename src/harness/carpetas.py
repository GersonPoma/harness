"""Carpetas del repo objetivo con un papel especial para el harness.

Vive fuera de herramientas/ para que entorno_docker y entrega puedan usarla sin import circular.
"""

from pathlib import Path

# Donde el harness escribe los logs de cada ejecución (y la descripción de un PR pendiente).
CARPETA_LOGS = Path(".harness") / "logs"
# Las que no recorre ni le muestra al modelo.
CARPETAS_IGNORADAS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".idea"}
