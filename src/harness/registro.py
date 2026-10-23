"""Log de pasos de cada ejecución, en .harness/logs/<ticket>-<fecha>.jsonl del repo objetivo."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from harness.bucle import Paso
from harness.cambios import CambiosRepo
from harness.contexto import ContextoProyecto
from harness.ejecucion import ResultadoEjecucion, Validacion
from harness.init import ResultadoInit
from harness.plan import Plan, archivos_fuera_del_plan
from harness.variables import ocultar_secretos

CARPETA_LOGS = Path(".harness") / "logs"
MAX_CARACTERES_RESULTADO_LOG = 2_000


class RegistroPasos:
    """Escribe un evento JSON por línea; oculta los secretos que se le indiquen."""

    def __init__(self, repo: Path, ticket: str, secretos: list[str]) -> None:
        fecha = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.ruta = repo / CARPETA_LOGS / f"{ticket}-{fecha}.jsonl"
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        # También en su forma escapada en JSON, por si el secreto tiene comillas o barras.
        self._secretos = [s for s in secretos if s]
        self._secretos += [json.dumps(s)[1:-1] for s in self._secretos]

    def registrar_inicio(
        self, proveedor: str, modelo: str, contexto: ContextoProyecto | None = None
    ) -> None:
        """Registra con qué proveedor, modelo y AGENTS.md empezó (nunca la API key)."""
        contexto = contexto or ContextoProyecto()
        self._escribir({
            "evento": "inicio",
            "proveedor": proveedor,
            "modelo": modelo,
            "agents_md": contexto.existe,
            "agents_md_caracteres": contexto.caracteres_originales,
            "agents_md_recortado": contexto.es_recortado,
        })

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

    def registrar_validacion(self, validacion: Validacion) -> None:
        """Registra una corrida de tests + lint y su informe recortado."""
        self._escribir({
            "evento": "validacion",
            "intento": validacion.intento,
            "es_valido": validacion.es_valido,
            "informe": validacion.informe[:MAX_CARACTERES_RESULTADO_LOG],
        })

    def registrar_fin(
        self, resultado: ResultadoEjecucion, cambios: CambiosRepo | None = None
    ) -> None:
        """Registra cómo terminó la ejecución y qué archivos cambiaron."""
        cambios = cambios or CambiosRepo()
        self._escribir({
            "evento": "fin",
            "pasos": resultado.pasos,
            "es_limite_alcanzado": resultado.es_limite_alcanzado,
            "es_validado": resultado.es_validado,
            "validaciones": resultado.validaciones,
            "tokens_entrada": resultado.tokens_entrada,
            "tokens_salida": resultado.tokens_salida,
            "texto_final": resultado.texto_final,
            "creados": cambios.creados,
            "modificados": cambios.modificados,
            "eliminados": cambios.eliminados,
            "plan_rechazado": resultado.es_plan_rechazado,
            "fuera_del_plan": (
                archivos_fuera_del_plan(resultado.plan, cambios) if resultado.plan else []
            ),
        })

    def registrar_fin_init(self, resultado: ResultadoInit) -> None:
        """Registra qué archivos creó harness init."""
        self._escribir({
            "evento": "fin",
            "creados": resultado.creados,
            "pasos": resultado.pasos,
            "tokens_entrada": resultado.tokens_entrada,
            "tokens_salida": resultado.tokens_salida,
            "texto_final": resultado.texto_final,
        })

    def registrar_plan(self, plan: Plan, es_aprobado: bool) -> None:
        """Registra el plan propuesto y si se siguió con la implementación."""
        self._escribir({"evento": "plan", "es_aprobado": es_aprobado, **plan.model_dump()})

    def registrar_aviso(self, mensaje: str) -> None:
        """Registra un aviso que no corta la ejecución (por ejemplo, una respuesta vacía)."""
        self._escribir({"evento": "aviso", "mensaje": mensaje})

    def registrar_reintento(self, mensaje: str) -> None:
        """Registra un reintento del proveedor ante un error pasajero."""
        self._escribir({"evento": "reintento", "mensaje": mensaje})

    def registrar_error(self, mensaje: str) -> None:
        """Registra un error que cortó la ejecución."""
        self._escribir({"evento": "error", "mensaje": mensaje})

    def _escribir(self, evento: dict[str, Any]) -> None:
        evento = {"hora": datetime.now().isoformat(timespec="seconds"), **evento}
        linea = json.dumps(evento, ensure_ascii=False)
        # Se reemplaza sobre el JSON ya armado para cubrir cualquier campo, incluso anidado.
        linea = ocultar_secretos(linea, self._secretos)
        with self.ruta.open("a", encoding="utf-8") as archivo:
            archivo.write(f"{linea}\n")
