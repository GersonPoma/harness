"""Resumen legible de una ejecución, armado con los eventos de su log JSONL.

Cada evento lleva `t` (segundos desde el inicio) y los eventos `etapa` marcan dónde empieza
cada etapa (preparación, plan, implementación, validación...): el tiempo de una etapa es lo que
pasa hasta la siguiente marca. El resumen va al lado del log, como .md, sin secretos (sale de
los eventos ya ocultados).
"""

from collections.abc import Sequence
from typing import Any

ETAPA_PREPARACION = "preparación"
ETAPA_PLAN = "plan"
ETAPA_IMPLEMENTACION = "implementación"
ETAPA_FUERA_DEL_PLAN = "fuera del plan"
ETAPA_VALIDACION = "validación"
ETAPA_REVISION = "revisión"
ETAPA_ENTREGA = "entrega"
# Lo que el usuario tarda en responder las preguntas del plan (--aprobar-plan): no es del plan.
ETAPA_ESPERA_USUARIO = "esperando al usuario"
SEGUNDOS_POR_MINUTO = 60
# Una etapa que no llegó a hacer nada (por ejemplo, ningún archivo fuera del plan) no se lista.
MIN_SEGUNDOS_ETAPA = 0.5
MAX_CARACTERES_TEXTO = 300

Evento = dict[str, Any]


def formatear_duracion(segundos: float) -> str:
    """'8 s', '1 min 52 s' o 'menos de 1 s'."""
    total = round(segundos)
    if total < 1:
        return "menos de 1 s"
    minutos, resto = divmod(total, SEGUNDOS_POR_MINUTO)
    return f"{minutos} min {resto} s" if minutos else f"{resto} s"


def tiempos_por_etapa(
    eventos: Sequence[Evento], hasta: float | None = None
) -> dict[str, float]:
    """Segundos de cada etapa (sumando las que se repiten, como las validaciones), en orden.

    La última dura hasta `hasta` (segundos desde el inicio) o, sin él, hasta el último evento.
    """
    marcas = [(e.get("t", 0.0), e["etapa"]) for e in eventos if e.get("evento") == "etapa"]
    fin = hasta if hasta is not None else (eventos[-1].get("t", 0.0) if eventos else 0.0)
    tiempos: dict[str, float] = {}
    for indice, (inicio, nombre) in enumerate(marcas):
        hasta = marcas[indice + 1][0] if indice + 1 < len(marcas) else fin
        tiempos[nombre] = round(tiempos.get(nombre, 0.0) + max(hasta - inicio, 0.0), 1)
    return {nombre: s for nombre, s in tiempos.items() if s >= MIN_SEGUNDOS_ETAPA}


def formatear_tiempos(tiempos: dict[str, float]) -> str:
    """'preparación 14 s · plan 38 s · implementación 2 min 10 s'."""
    return " · ".join(f"{nombre} {formatear_duracion(s)}" for nombre, s in tiempos.items())


def resumir_log(eventos: Sequence[Evento]) -> str:
    """El resumen de la ejecución en Markdown."""
    inicio = _primero(eventos, "inicio") or {}
    ticket = _primero(eventos, "ticket") or {}
    titulo = f"{ticket.get('ticket', '')}: {ticket.get('titulo', '')}".strip(": ")
    lineas = [f"# {titulo or 'Ejecución del harness'}", ""]
    lineas += _datos_generales(eventos, inicio)
    lineas += _seccion_tiempos(eventos)
    lineas += _seccion_validaciones(eventos)
    lineas += _seccion_revision(eventos)
    lineas += _seccion_fuera_del_plan(eventos)
    lineas += _seccion_entrega(eventos)
    lineas += _seccion_avisos(eventos, inicio)
    return "\n".join(lineas).rstrip() + "\n"


def _datos_generales(eventos: Sequence[Evento], inicio: Evento) -> list[str]:
    fin = _ultimo(eventos, "fin") or {}
    modelos = [f"{inicio.get('proveedor', '?')}/{inicio.get('modelo', '?')}"]
    modelos += [e["siguiente"] for e in eventos if e.get("evento") == "proveedor"]
    perfil = f" (perfil {inicio['perfil']})" if inicio.get("perfil") else ""
    pasos = max((e.get("paso", 0) for e in eventos), default=0)
    duracion = eventos[-1].get("t", 0.0) if eventos else 0.0
    lineas = [
        f"- **Resultado:** {_resultado(eventos, fin)}",
        f"- **Modelo:** {', después '.join(modelos)}{perfil}",
    ]
    if inicio.get("revisor"):
        lineas.append(f"- **Revisor:** {inicio['revisor']}")
    lineas.append(
        f"- **Pasos:** {fin.get('pasos', pasos)} · **Tokens:** {fin.get('tokens_entrada', 0)} "
        f"entrada / {fin.get('tokens_salida', 0)} salida · **Duración:** "
        f"{formatear_duracion(duracion)}"
    )
    memoria = sum(1 for e in eventos if e.get("evento") == "memoria")
    if memoria:
        lineas.append(f"- **Memoria:** {memoria} notas nuevas en .harness/memory.md")
    return [*lineas, ""]


def _resultado(eventos: Sequence[Evento], fin: Evento) -> str:
    error = _ultimo(eventos, "error")
    entrega = _ultimo(eventos, "entrega")
    if error is not None and (entrega is None or error["t"] > entrega.get("t", 0)):
        return f"se cortó: {_recortar(error.get('mensaje', ''))}"
    if fin.get("plan_rechazado"):
        return "plan rechazado: no se implementó nada"
    if fin.get("es_limite_alcanzado"):
        return "se alcanzó el límite de pasos sin terminar"
    if fin.get("es_validado") is False:
        return f"los tests no pasan después de {fin.get('validaciones', 0)} validaciones"
    if entrega is not None:
        if entrega.get("pull_request"):
            return f"PR abierto: {entrega['pull_request']}"
        if entrega.get("commit"):
            return f"commit {str(entrega['commit'])[:7]} en {entrega.get('rama')}"
    return "terminó" if fin else "sin terminar"


def _seccion_tiempos(eventos: Sequence[Evento]) -> list[str]:
    tiempos = tiempos_por_etapa(eventos)
    if not tiempos:
        return []
    filas = [f"| {nombre} | {formatear_duracion(s)} |" for nombre, s in tiempos.items()]
    return ["## Tiempos por etapa", "", "| Etapa | Tiempo |", "|---|---|", *filas, ""]


def _seccion_validaciones(eventos: Sequence[Evento]) -> list[str]:
    validaciones = [e for e in eventos if e.get("evento") == "validacion"]
    if not validaciones:
        return []
    lineas = ["## Validaciones", ""]
    for v in validaciones:
        detalle = f" ({v['resumen_tests']})" if v.get("resumen_tests") else ""
        estado = f"pasó{detalle}" if v.get("es_valido") else "falló"
        lineas.append(f"- {v.get('intento')}: {estado}")
    return [*lineas, ""]


def _seccion_revision(eventos: Sequence[Evento]) -> list[str]:
    revisiones = [e for e in eventos if e.get("evento") == "revision"]
    if not revisiones:
        return []
    lineas = ["## Revisión", ""]
    for r in revisiones:
        quien = f" ({r['modelo']})" if r.get("modelo") else ""
        if r.get("es_fallida"):
            lineas.append(f"- Vuelta {r.get('vuelta')}{quien}: sin informe, se siguió sin revisión")
        elif r.get("es_aprobada"):
            lineas.append(f"- Vuelta {r.get('vuelta')}{quien}: sin problemas")
        else:
            cantidad = len(r.get("problemas", []))
            plural = "problema" if cantidad == 1 else "problemas"
            lineas.append(f"- Vuelta {r.get('vuelta')}{quien}: {cantidad} {plural}")
            lineas += [f"  - `{p.get('archivo')}`: {_recortar(p.get('descripcion', ''))}"
                       for p in r.get("problemas", [])]
    return [*lineas, ""]


def _seccion_fuera_del_plan(eventos: Sequence[Evento]) -> list[str]:
    alcance = _ultimo(eventos, "fuera_del_plan")
    fin = _ultimo(eventos, "fin") or {}
    if alcance is None and not fin.get("fuera_del_plan"):
        return []
    lineas = ["## Fuera del plan", ""]
    if alcance is not None:
        lineas += [f"- Justificado `{ruta}`: {_recortar(motivo)}"
                   for ruta, motivo in (alcance.get("justificados") or {}).items()]
        lineas += [f"- Revertido `{ruta}`" for ruta in alcance.get("revertidos") or []]
    lineas += [f"- Sin justificar `{ruta}`" for ruta in fin.get("fuera_del_plan") or []
               if alcance is None or ruta not in (alcance.get("justificados") or {})]
    return [*lineas, ""]


def _seccion_entrega(eventos: Sequence[Evento]) -> list[str]:
    entrega = _ultimo(eventos, "entrega")
    if entrega is None:
        return []
    lineas = ["## Entrega", ""]
    if entrega.get("commit"):
        lineas.append(f"- Commit `{str(entrega['commit'])[:7]}` en `{entrega.get('rama')}`")
    lineas.append(f"- Push: {'sí' if entrega.get('es_pusheado') else 'no'}")
    if entrega.get("pull_request"):
        lineas.append(f"- PR: {entrega['pull_request']}")
    elif entrega.get("motivo") or entrega.get("error_pr"):
        lineas.append(f"- Sin PR: {_recortar(entrega.get('error_pr') or entrega.get('motivo'))}")
    for evento in eventos:
        if evento.get("evento") == "jira_comentario":
            lineas.append(f"- Comentario en Jira: {evento.get('estado')}")
        elif evento.get("evento") == "jira_transicion":
            lineas.append(f"- Ticket a '{evento.get('estado_pedido')}': {evento.get('resultado')}")
    return [*lineas, ""]


def _seccion_avisos(eventos: Sequence[Evento], inicio: Evento) -> list[str]:
    avisos = [_recortar(e.get("mensaje", "")) for e in eventos if e.get("evento") == "aviso"]
    if inicio and not inicio.get("agents_md", True):
        avisos.append("El repo no tiene AGENTS.md: el modelo trabajó sin las convenciones del "
                      "proyecto.")
    errores = [_recortar(e.get("mensaje", "")) for e in eventos if e.get("evento") == "error"]
    lineas: list[str] = []
    if avisos:
        lineas += ["## Avisos", "", *(f"- {a}" for a in dict.fromkeys(avisos)), ""]
    if errores:
        lineas += ["## Errores", "", *(f"- {e}" for e in errores), ""]
    return lineas


def _primero(eventos: Sequence[Evento], nombre: str) -> Evento | None:
    return next((e for e in eventos if e.get("evento") == nombre), None)


def _ultimo(eventos: Sequence[Evento], nombre: str) -> Evento | None:
    return next((e for e in reversed(eventos) if e.get("evento") == nombre), None)


def _recortar(texto: Any) -> str:
    texto = " ".join(str(texto or "").split())
    return texto if len(texto) <= MAX_CARACTERES_TEXTO else f"{texto[:MAX_CARACTERES_TEXTO]}…"
