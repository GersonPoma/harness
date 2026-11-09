"""Sesión de una ejecución en .harness/sessions/<TICKET>.json, para harness resume.

Se escribe después de cada paso, de cada validación, al terminar y al cortar (límite, Ctrl+C,
proveedor caído, error), así una ejecución cortada se retoma desde donde quedó en vez de
empezar de cero. La carpeta está en el .gitignore, protegida de las herramientas del modelo y
montada de solo lectura en el contenedor. Antes de escribir, cada texto pasa por
ocultar_secretos: el archivo nunca guarda una API key ni un token.
"""

import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from harness.cambios import Instantanea, tomar_instantanea
from harness.ejecucion import ResultadoEjecucion
from harness.git import ErrorGit, ejecutar_git
from harness.plan import Plan
from harness.prompts import crear_mensaje_retomar
from harness.revision import EstadoRevision
from harness.tipos import LlamadaHerramienta, Mensaje, Rol
from harness.variables import ocultar_secretos

CARPETA_SESIONES = Path(".harness") / "sessions"
VERSION_SESION = 1

ESTADO_PLAN = "plan"
ESTADO_IMPLEMENTACION = "implementacion"
ESTADO_ENTREGA = "entrega"
ESTADO_COMPLETADA = "completada"

MOTIVO_LIMITE_PASOS = "limite_pasos"
MOTIVO_VALIDACION = "validacion"
MOTIVO_CTRL_C = "ctrl_c"
MOTIVO_PROVEEDOR = "proveedor"
MOTIVO_ERROR = "error"
MOTIVO_BUCLE = "bucle"


class ErrorSesion(Exception):
    """La sesión no existe, está rota o es de una versión que no se entiende."""


@dataclass
class Sesion:
    """Todo lo necesario para retomar una ejecución de un ticket."""

    # Del ticket solo lo que se usa al retomar (nunca datos del autor de Jira).
    ticket: dict[str, str | None]
    estado: str
    proveedor: str
    modelos_usados: list[str] = field(default_factory=list)
    mensajes: list[Mensaje] = field(default_factory=list)
    pasos: int = 0
    # De esos, los que no descuentan de max_pasos (revisión): ResultadoEjecucion.pasos_extra.
    pasos_extra: int = 0
    tokens_entrada: int = 0
    tokens_salida: int = 0
    validaciones: int = 0
    es_con_plan: bool = True
    plan: dict[str, Any] | None = None
    # Rama, base, remoto, es_con_push, es_con_pr y el HEAD al cortar (None sin harness.yaml).
    rama: dict[str, Any] | None = None
    # Hash de cada archivo del repo al empezar (después de instalar) y al cortar: con la
    # primera se calculan los cambios del ticket completo; con la segunda, si alguien tocó el
    # repo después del corte.
    repo_al_empezar: Instantanea = field(default_factory=dict)
    repo_al_cortar: Instantanea = field(default_factory=dict)
    entrega: dict[str, Any] = field(default_factory=dict)
    # El resumen final del modelo: con él, resume arma la descripción del PR sin el modelo.
    texto_final: str | None = None
    # Cuántos tests pasaron en la última validación: también para la descripción del PR.
    resumen_tests: str | None = None
    # EstadoRevision como diccionario: vueltas hechas y problemas de la última.
    revision: dict[str, Any] = field(default_factory=dict)
    motivo_corte: str | None = None
    detalle_corte: str | None = None

    @property
    def clave(self) -> str:
        return str(self.ticket["clave"])

    @property
    def es_completada(self) -> bool:
        return self.estado == ESTADO_COMPLETADA


class SesionEnCurso:
    """La sesión de la ejecución actual: se actualiza y se guarda en cada momento clave.

    Guardarla nunca corta la ejecución: si el disco falla, se avisa una vez y se sigue.
    """

    def __init__(
        self,
        repo: Path,
        sesion: Sesion,
        secretos: list[str],
        al_fallar: Callable[[str], None] | None = None,
    ) -> None:
        self.repo = repo
        self.sesion = sesion
        self._secretos = secretos
        self._al_fallar = al_fallar
        self._mensajes: list[Mensaje] | None = None
        self._es_fallo_avisado = False

    def usar_mensajes(self, mensajes: list[Mensaje], repo_al_empezar: Instantanea) -> None:
        """La conversación viva (el bucle la va agregando) y la foto del repo al empezar."""
        self._mensajes = mensajes
        self.sesion.repo_al_empezar = repo_al_empezar
        self.guardar()

    def paso(self, numero: int) -> None:
        self.sesion.pasos = max(self.sesion.pasos, numero)
        self.guardar()

    def plan_aprobado(self, plan: dict[str, Any]) -> None:
        self.sesion.plan = plan
        self.sesion.estado = ESTADO_IMPLEMENTACION
        self.guardar()

    def validacion(self) -> None:
        self.sesion.validaciones += 1
        self.guardar()

    def revision(self, estado: EstadoRevision) -> None:
        self.sesion.revision = asdict(estado)
        self.guardar()

    def cambio_de_proveedor(self, siguiente: str) -> None:
        self.sesion.proveedor = siguiente
        self.sesion.modelos_usados.append(siguiente)
        self.guardar()

    def cortar(self, motivo: str, detalle: str | None = None) -> None:
        """La ejecución se cortó: queda lo necesario para retomarla con harness resume."""
        self.sesion.motivo_corte = motivo
        self.sesion.detalle_corte = detalle
        self.sesion.repo_al_cortar = tomar_instantanea(self.repo)
        if self.sesion.rama is not None:
            self.sesion.rama["head_al_cortar"] = cabeza(self.repo)
        self.guardar()

    def completar(self) -> None:
        self.sesion.estado = ESTADO_COMPLETADA
        self.sesion.motivo_corte = self.sesion.detalle_corte = None
        self.guardar()

    def guardar(self) -> None:
        if self._mensajes is not None:
            self.sesion.mensajes = list(self._mensajes)
        try:
            guardar_sesion(self.repo, self.sesion, self._secretos)
        except OSError as error:
            if self._al_fallar and not self._es_fallo_avisado:
                self._al_fallar(f"No se pudo guardar la sesión para harness resume: {error}")
            self._es_fallo_avisado = True


@dataclass
class Reanudacion:
    """Lo que harness resume le pasa a la ejecución para seguir desde el corte."""

    # La conversación sin el system prompt (se regenera con el AGENTS.md y la memoria de hoy).
    mensajes: list[Mensaje]
    # Pasos, tokens, validaciones y plan ya hechos: siguen contando desde ahí.
    continuacion: ResultadoEjecucion
    # Para calcular los cambios del ticket completo, no solo los de esta parte.
    repo_al_empezar: Instantanea


def armar_reanudacion(sesion: Sesion, motivo: str, cambiados: list[str]) -> Reanudacion:
    """Prepara la conversación guardada para seguir: sin llamadas a medias y con la nota."""
    mensajes = quitar_llamadas_sin_respuesta(sesion.mensajes)[1:]
    nota = crear_mensaje_retomar(motivo, cambiados)
    if mensajes and mensajes[-1].rol == Rol.HERRAMIENTA:
        # Dentro del último resultado y no como mensaje aparte: algunos proveedores no aceptan
        # un mensaje de usuario entre los resultados de herramientas y la siguiente respuesta.
        ultimo = mensajes[-1]
        mensajes[-1] = Mensaje(Rol.HERRAMIENTA, f"{ultimo.contenido}\n\n{nota}",
                               id_llamada=ultimo.id_llamada)
    else:
        mensajes.append(Mensaje(Rol.USUARIO, nota))
    plan = Plan.model_validate(sesion.plan) if sesion.plan else None
    continuacion = ResultadoEjecucion(
        None, sesion.pasos, sesion.tokens_entrada, sesion.tokens_salida, False, None,
        sesion.validaciones, plan, revision=estado_revision(sesion),
        pasos_extra=sesion.pasos_extra,
    )
    return Reanudacion(mensajes, continuacion, sesion.repo_al_empezar)


def estado_revision(sesion: Sesion) -> EstadoRevision:
    """Cómo iba la revisión al cortar (vacía en sesiones de antes de la revisión)."""
    return EstadoRevision(**sesion.revision) if sesion.revision else EstadoRevision()


def cabeza(repo: Path) -> str | None:
    """El commit en el que está el repo, o None si no es un repositorio Git."""
    try:
        return ejecutar_git(repo, "rev-parse", "HEAD")
    except ErrorGit:
        return None


def ruta_sesion(repo: Path, clave: str) -> Path:
    """.harness/sessions/<TICKET>.json del repo objetivo."""
    return repo / CARPETA_SESIONES / f"{clave}.json"


def guardar_sesion(repo: Path, sesion: Sesion, secretos: list[str]) -> Path:
    """Escribe la sesión de forma atómica: un Ctrl+C a mitad de la escritura no la rompe."""
    ruta = ruta_sesion(repo, sesion.clave)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    datos = _ocultar(sesion_a_json(sesion), secretos)
    texto = json.dumps(datos, ensure_ascii=False, indent=1)
    descriptor, temporal = tempfile.mkstemp(dir=ruta.parent, prefix=f".{sesion.clave}-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as archivo:
            archivo.write(texto)
        os.replace(temporal, ruta)
    except BaseException:
        Path(temporal).unlink(missing_ok=True)
        raise
    return ruta


def cargar_sesion(repo: Path, clave: str) -> Sesion | None:
    """La sesión del ticket, o None si no hay. Lanza ErrorSesion si el archivo no sirve."""
    ruta = ruta_sesion(repo, clave)
    if not ruta.is_file():
        return None
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        if datos.get("version") != VERSION_SESION:
            raise ErrorSesion(
                f"{ruta.name} es de otra versión del harness ({datos.get('version')})."
            )
        return sesion_desde_json(datos)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ErrorSesion(f"No se pudo leer la sesión {ruta}: {error}") from error


def descartar_sesion(repo: Path, clave: str, ahora: datetime | None = None) -> Path:
    """Renombra la sesión a <TICKET>.descartada-<fecha>.json (no se borra: queda para revisar)."""
    ruta = ruta_sesion(repo, clave)
    fecha = (ahora or datetime.now()).strftime("%Y%m%d-%H%M%S")
    destino = ruta.with_name(f"{clave}.descartada-{fecha}.json")
    ruta.replace(destino)
    return destino


def sesion_a_json(sesion: Sesion) -> dict[str, Any]:
    """La sesión como diccionario serializable (los mensajes, con sus llamadas)."""
    datos = asdict(sesion)
    datos["mensajes"] = [_mensaje_a_json(m) for m in sesion.mensajes]
    return {"version": VERSION_SESION, **datos}


def sesion_desde_json(datos: dict[str, Any]) -> Sesion:
    """La inversa de sesion_a_json."""
    campos = {k: v for k, v in datos.items() if k not in ("version", "mensajes")}
    return Sesion(**campos, mensajes=[_mensaje_desde_json(m) for m in datos["mensajes"]])


def quitar_llamadas_sin_respuesta(mensajes: list[Mensaje]) -> list[Mensaje]:
    """Sin la última respuesta del asistente si a alguna de sus llamadas le falta el resultado.

    Pasa con un Ctrl+C a mitad de un paso; los proveedores rechazan una llamada sin respuesta.
    """
    for indice in range(len(mensajes) - 1, -1, -1):
        mensaje = mensajes[indice]
        if mensaje.rol == Rol.ASISTENTE and mensaje.llamadas:
            respondidas = {m.id_llamada for m in mensajes[indice + 1:] if m.rol == Rol.HERRAMIENTA}
            if all(llamada.id in respondidas for llamada in mensaje.llamadas):
                return list(mensajes)
            return list(mensajes[:indice])
        if mensaje.rol != Rol.HERRAMIENTA:
            return list(mensajes)
    return list(mensajes)


def _mensaje_a_json(mensaje: Mensaje) -> dict[str, Any]:
    return {
        "rol": mensaje.rol.value,
        "contenido": mensaje.contenido,
        "llamadas": [asdict(llamada) for llamada in mensaje.llamadas],
        "id_llamada": mensaje.id_llamada,
    }


def _mensaje_desde_json(datos: dict[str, Any]) -> Mensaje:
    llamadas = [LlamadaHerramienta(**llamada) for llamada in datos.get("llamadas", [])]
    return Mensaje(Rol(datos["rol"]), datos.get("contenido"), llamadas, datos.get("id_llamada"))


def _ocultar(valor: Any, secretos: list[str]) -> Any:
    """Oculta los secretos en cada texto, a cualquier profundidad: el JSON sigue siendo válido."""
    if isinstance(valor, str):
        return ocultar_secretos(valor, secretos)
    if isinstance(valor, dict):
        return {clave: _ocultar(v, secretos) for clave, v in valor.items()}
    if isinstance(valor, list):
        return [_ocultar(v, secretos) for v in valor]
    return valor
