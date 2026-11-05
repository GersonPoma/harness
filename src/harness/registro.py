"""Log de pasos de cada ejecución, en .harness/logs/<ticket>-<fecha>.jsonl del repo objetivo."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from harness.bucle import Paso
from harness.cache_dependencias import EstadoCache
from harness.cambios import CambiosRepo
from harness.comentario_pr import ResultadoComentario
from harness.compactacion import Compactacion
from harness.config_mcp import ServidorMCP
from harness.carpetas import CARPETA_LOGS
from harness.contexto import ContextoProyecto
from harness.ejecucion import ResultadoEjecucion, Validacion
from harness.entorno_docker import Instalacion
from harness.entrega import ConfigEntrega, ResultadoEntrega
from harness.init import ResultadoInit
from harness.plan import Plan, archivos_fuera_del_plan
from harness.proveedores.respaldo import CambioProveedor
from harness.ticket import Ticket
from harness.variables import ocultar_secretos


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
        self,
        proveedor: str,
        modelo: str,
        contexto: ContextoProyecto | None = None,
        respaldo: list[str] | None = None,
    ) -> None:
        """Registra con qué proveedor, modelo y AGENTS.md empezó (nunca la API key)."""
        contexto = contexto or ContextoProyecto()
        self._escribir({
            "evento": "inicio",
            "proveedor": proveedor,
            "modelo": modelo,
            "respaldo": respaldo or [],
            "agents_md": contexto.existe,
            "agents_md_caracteres": contexto.caracteres_originales,
            "agents_md_recortado": contexto.es_recortado,
        })

    def registrar_paso(self, paso: Paso) -> None:
        """Registra una herramienta llamada y su resultado.

        El resultado va completo, tal como lo recibió el modelo (las salidas de comandos ya
        vienen recortadas a max_caracteres_salida): un recorte extra aquí escondería
        justo el final, donde suelen estar los errores, y el log no serviría para diagnosticar.
        """
        self._escribir({
            "evento": "herramienta",
            "paso": paso.numero,
            "herramienta": paso.llamada.nombre,
            "argumentos": paso.llamada.argumentos,
            "es_error": paso.es_error,
            "resultado": paso.resultado,
        })

    def registrar_validacion(self, validacion: Validacion) -> None:
        """Registra una corrida de tests + lint y su informe, el mismo que recibe el modelo."""
        self._escribir({
            "evento": "validacion",
            "intento": validacion.intento,
            "es_valido": validacion.es_valido,
            "informe": validacion.informe,
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

    def registrar_mcp(
        self,
        servidor: ServidorMCP,
        herramientas: list[str],
        es_pr: bool,
        error: str | None = None,
        usadas: list[str] | None = None,
    ) -> None:
        """Registra la conexión con un servidor MCP: para qué se usa, o por qué no conectó.

        `herramientas` son las que recibe el modelo; `usadas`, las que llama el harness.
        """
        self._escribir({
            "evento": "mcp",
            "servidor": servidor.nombre,
            "transporte": servidor.transporte,
            "herramientas": herramientas,
            "usadas_por_el_harness": usadas or [],
            "es_pr": es_pr,
            "error": error,
        })

    def registrar_ticket(self, ticket: Ticket) -> None:
        """Registra qué ticket se procesa y de dónde vino (nunca datos del autor)."""
        self._escribir({
            "evento": "ticket",
            "ticket": ticket.clave,
            "titulo": ticket.titulo,
            "fuente": ticket.fuente,
            "url": ticket.url,
            "caracteres": len(ticket.descripcion),
            "es_recortado": ticket.es_recortado,
            "caracteres_originales": ticket.caracteres_originales,
        })

    def registrar_comentario_jira(self, clave: str, resultado: ResultadoComentario) -> None:
        """Registra si se comentó el link del PR en Jira, por dónde, o por qué no."""
        self._escribir({
            "evento": "jira_comentario",
            "ticket": clave,
            "estado": resultado.estado,
            "camino": resultado.camino,
            "error": resultado.error,
        })

    def registrar_rama(self, config: ConfigEntrega) -> None:
        """Registra la rama del ticket creada al empezar."""
        self._escribir({"evento": "rama", "rama": config.rama, "base": config.base})

    def registrar_entrega(self, entrega: ResultadoEntrega) -> None:
        """Registra el commit, el push y el PR (o por qué no se hicieron)."""
        self._escribir({
            "evento": "entrega",
            "rama": entrega.rama,
            "commit": entrega.hash_commit,
            "archivos": entrega.archivos,
            "es_pusheado": entrega.es_pusheado,
            "pull_request": entrega.pull_request.url if entrega.pull_request else None,
            "es_borrador": entrega.pull_request.es_borrador if entrega.pull_request else None,
            "camino_pr": _camino_pr(entrega),
            "error_pr": entrega.error_pr,
            "url_pr_a_mano": entrega.url_pr_a_mano,
            "pr_pendiente": str(entrega.ruta_pr_pendiente) if entrega.ruta_pr_pendiente else None,
            "es_rama_borrada": entrega.es_rama_borrada,
            "motivo": entrega.motivo,
        })

    def registrar_cache(self, cache: EstadoCache) -> None:
        """Registra si se usó la caché de dependencias, con qué volumen, o por qué no."""
        self._escribir({
            "evento": "cache",
            "volumen": cache.volumen,
            "es_usada": cache.es_usada,
            "es_creado": cache.es_creado,
            "motivo": cache.motivo,
            "variables_respetadas": list(cache.variables_respetadas),
        })

    def registrar_instalacion(self, instalacion: Instalacion) -> None:
        """Registra cuánto tardó docker.instalar (el dato para medir la caché)."""
        self._escribir({
            "evento": "instalacion",
            "segundos": round(instalacion.segundos, 1),
            "es_reinstalacion": instalacion.es_reinstalacion,
            "archivo_cambiado": instalacion.archivo_cambiado,
            "es_ok": instalacion.es_ok,
        })

    def registrar_resume(
        self, estado: str, motivo_corte: str | None, cambios_desde_el_corte: list[str]
    ) -> None:
        """Registra que esta ejecución retoma otra cortada, y desde qué estado."""
        self._escribir({
            "evento": "resume",
            "estado": estado,
            "motivo_corte": motivo_corte,
            "cambios_desde_el_corte": cambios_desde_el_corte,
        })

    def registrar_cambio_proveedor(self, cambio: CambioProveedor, paso: int) -> None:
        """Registra que la conversación pasó al siguiente proveedor de respaldo."""
        self._escribir({
            "evento": "proveedor",
            "anterior": cambio.anterior,
            "siguiente": cambio.siguiente,
            "motivo": cambio.motivo,
            "detalle": cambio.detalle,
            "paso": paso,
        })

    def registrar_compactacion(self, compactacion: Compactacion) -> None:
        """Registra que se acortaron resultados viejos de la conversación."""
        self._escribir({
            "evento": "compactacion",
            "resultados_omitidos": compactacion.resultados_omitidos,
            "caracteres_ahorrados": compactacion.caracteres_ahorrados,
        })

    def registrar_memoria(self, nota: str) -> None:
        """Registra que el modelo guardó una nota (su largo: el texto queda en memory.md)."""
        self._escribir({"evento": "memoria", "caracteres": len(nota)})

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


def _camino_pr(entrega: ResultadoEntrega) -> str | None:
    if entrega.pull_request is None:
        return None
    return "mcp" if entrega.pull_request.por_mcp else "rest"
