"""Log de pasos de cada ejecución, en .harness/logs/<ticket>-<fecha>.jsonl del repo objetivo."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from harness.bucle import Paso, ResultadoBucle

CARPETA_LOGS = Path(".harness") / "logs"
MAX_CARACTERES_RESULTADO_LOG = 2_000
TEXTO_OCULTO = "***"


class RegistroPasos:
    """Escribe un evento JSON por línea; oculta los secretos que se le indiquen."""

    def __init__(self, repo: Path, ticket: str, secretos: list[str]) -> None:
        fecha = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.ruta = repo / CARPETA_LOGS / f"{ticket}-{fecha}.jsonl"
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self._secretos = [s for s in secretos if s]

    def registrar_inicio(self, proveedor: str, modelo: str) -> None:
        """Registra con qué proveedor y modelo empezó la ejecución (nunca la API key)."""
        self._escribir({"evento": "inicio", "proveedor": proveedor, "modelo": modelo})

    def registrar_paso(self, paso: Paso) -> None:
        """Registra una herramienta llamada y su resultado recortado."""
        self._escribir({
            "evento": "herramienta",
            "paso": paso.numero,
            "herramienta": paso.llamada.nombre,
            "argumentos": paso.llamada.argumentos,
            "es_error": paso.es_error,
            "resultado": paso.resultado[:MAX_CARACTERES_RESULTADO_LOG],
        })

    def registrar_fin(self, resultado: ResultadoBucle) -> None:
        """Registra cómo terminó el bucle."""
        self._escribir({
            "evento": "fin",
            "pasos": resultado.pasos,
            "es_limite_alcanzado": resultado.es_limite_alcanzado,
            "tokens_entrada": resultado.tokens_entrada,
            "tokens_salida": resultado.tokens_salida,
            "texto_final": resultado.texto_final,
        })

    def registrar_error(self, mensaje: str) -> None:
        """Registra un error que cortó la ejecución."""
        self._escribir({"evento": "error", "mensaje": mensaje})

    def _escribir(self, evento: dict[str, Any]) -> None:
        evento = {"hora": datetime.now().isoformat(timespec="seconds"), **evento}
        linea = json.dumps(evento, ensure_ascii=False)
        # Se reemplaza sobre el JSON ya armado para cubrir cualquier campo, incluso anidado.
        for secreto in self._secretos:
            linea = linea.replace(secreto, TEXTO_OCULTO)
        with self.ruta.open("a", encoding="utf-8") as archivo:
            archivo.write(f"{linea}\n")
