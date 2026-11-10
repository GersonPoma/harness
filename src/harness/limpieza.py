"""harness limpiar: borra lo que dejó el harness en Docker, y solo eso.

Todo se busca por etiqueta (harness=1), nunca por patrón de nombre, y se borra por id. Nunca
se toca algo de una ejecución viva. Los volúmenes de caché solo con --cache, y las imágenes
solo con --imagenes (las del harness.yaml del proyecto, si ningún contenedor las usa): las
imágenes no son del harness, así que es la única excepción a "solo por etiqueta".
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from harness.cache_dependencias import ETIQUETA_CACHE
from harness.etiquetas import (
    CONTENEDOR,
    ETIQUETA_PROYECTO,
    ETIQUETA_TICKET,
    ETIQUETA_TIPO,
    FILTRO_HARNESS,
    RED,
    VOLUMEN,
    Recurso,
    es_de_ejecucion_viva,
    listar,
)

IMAGEN = "imagen"
TIMEOUT_DOCKER_SEGUNDOS = 120
# Tamaños como los muestra docker ("82.38MB"): Docker usa unidades decimales.
PATRON_TAMANO = re.compile(r"^\s*([\d.]+)\s*([kKMGT]?B)\s*$")
MULTIPLICADORES = {"B": 1, "kB": 10**3, "KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12}
MOTIVO_VIVA = "es de una ejecución que sigue corriendo"

Ejecutor = Callable[[list[str], float], tuple[int, str]]


@dataclass(frozen=True)
class Elemento:
    """Algo que se puede borrar, con lo que se le muestra al usuario antes de confirmar."""

    tipo: str
    id: str
    nombre: str
    detalle: str = ""
    bytes: int | None = None


@dataclass
class PlanLimpieza:
    """Lo que se borraría y lo que se deja (con el motivo)."""

    a_borrar: list[Elemento] = field(default_factory=list)
    omitidos: list[tuple[Elemento, str]] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(e.bytes or 0 for e in self.a_borrar)


@dataclass
class ResultadoLimpieza:
    borrados: list[Elemento] = field(default_factory=list)
    fallidos: list[tuple[Elemento, str]] = field(default_factory=list)

    @property
    def bytes_liberados(self) -> int:
        return sum(e.bytes or 0 for e in self.borrados)


def planear(
    ejecutar: Ejecutor,
    con_cache: bool = False,
    proyecto_cache: str | None = None,
    imagenes: list[str] | None = None,
    filtro_extra: list[str] | None = None,
) -> PlanLimpieza:
    """Arma la lista de lo que se borraría, sin borrar nada.

    `filtro_extra` (etiquetas adicionales, 'label=...') es para los tests con Docker real:
    así solo ven lo que crearon ellos.
    """
    extra = list(filtro_extra or [])
    plan = PlanLimpieza()
    contenedores = listar(ejecutar, CONTENEDOR, [FILTRO_HARNESS, *extra], con_tamano=True)
    _separar(plan, [(c, _elemento_contenedor(c)) for c in contenedores])
    redes = listar(ejecutar, RED, [FILTRO_HARNESS, *extra])
    _separar(plan, [(r, _elemento_red(r)) for r in redes])
    ids_a_borrar = {e.id for e in plan.a_borrar if e.tipo == CONTENEDOR}
    if con_cache:
        _agregar_volumenes(ejecutar, plan, proyecto_cache, extra, ids_a_borrar)
    if imagenes:
        _agregar_imagenes(ejecutar, plan, imagenes, ids_a_borrar)
    return plan


def borrar(ejecutar: Ejecutor, plan: PlanLimpieza) -> ResultadoLimpieza:
    """Borra lo del plan, por id y en orden: contenedores, redes, volúmenes, imágenes."""
    resultado = ResultadoLimpieza()
    for tipo in (CONTENEDOR, RED, VOLUMEN, IMAGEN):
        for elemento in [e for e in plan.a_borrar if e.tipo == tipo]:
            codigo, salida = ejecutar(_comando_borrar(elemento), TIMEOUT_DOCKER_SEGUNDOS)
            if codigo == 0:
                resultado.borrados.append(elemento)
            else:
                resultado.fallidos.append((elemento, salida.strip() or f"código {codigo}"))
    return resultado


def bytes_de(texto: str) -> int | None:
    """'82.38MB' -> 82380000; None si no se entiende."""
    coincidencia = PATRON_TAMANO.match(texto)
    if not coincidencia:
        return None
    numero, unidad = coincidencia.groups()
    try:
        return round(float(numero) * MULTIPLICADORES[unidad])
    except (ValueError, KeyError):
        return None


def _separar(plan: PlanLimpieza, recursos: list[tuple[Recurso, Elemento]]) -> None:
    for recurso, elemento in recursos:
        if es_de_ejecucion_viva(recurso.etiquetas):
            plan.omitidos.append((elemento, MOTIVO_VIVA))
        else:
            plan.a_borrar.append(elemento)


def _descripcion(recurso: Recurso) -> str:
    etiquetas = recurso.etiquetas
    partes = [etiquetas.get(ETIQUETA_PROYECTO, ""), etiquetas.get(ETIQUETA_TICKET, "")]
    if etiquetas.get(ETIQUETA_TIPO):
        partes.append(f"servidor {etiquetas[ETIQUETA_TIPO]}")
    if recurso.es_conservado:
        partes.append("conservado")
    return " / ".join(p for p in partes if p)


def _elemento_contenedor(recurso: Recurso) -> Elemento:
    estado = "corriendo" if recurso.es_corriendo else "detenido"
    detalle = ", ".join(p for p in (_descripcion(recurso), estado) if p)
    return Elemento(CONTENEDOR, recurso.id, recurso.nombre, detalle, recurso.bytes)


def _elemento_red(recurso: Recurso) -> Elemento:
    return Elemento(RED, recurso.id, recurso.nombre, _descripcion(recurso))


def _agregar_volumenes(
    ejecutar: Ejecutor,
    plan: PlanLimpieza,
    proyecto: str | None,
    extra: list[str],
    ids_a_borrar: set[str],
) -> None:
    etiqueta = f"label={ETIQUETA_CACHE}={proyecto}" if proyecto else f"label={ETIQUETA_CACHE}"
    volumenes = listar(ejecutar, VOLUMEN, [FILTRO_HARNESS, etiqueta, *extra])
    tamanos = _tamanos_volumenes(ejecutar) if volumenes else {}
    for volumen in volumenes:
        detalle = f"caché de {volumen.etiquetas.get(ETIQUETA_CACHE, '?')}"
        elemento = Elemento(VOLUMEN, volumen.id, volumen.nombre, detalle, tamanos.get(volumen.id))
        usuarios = _contenedores_que_usan(ejecutar, f"volume={volumen.id}") - ids_a_borrar
        if usuarios:
            plan.omitidos.append((elemento, _motivo_en_uso(usuarios)))
        else:
            plan.a_borrar.append(elemento)


def _agregar_imagenes(
    ejecutar: Ejecutor, plan: PlanLimpieza, imagenes: list[str], ids_a_borrar: set[str]
) -> None:
    for imagen in dict.fromkeys(imagenes):
        codigo, salida = ejecutar(
            ["docker", "image", "inspect", "--format", "{{.Id}} {{.Size}}", imagen],
            TIMEOUT_DOCKER_SEGUNDOS,
        )
        if codigo != 0:
            continue  # No está descargada: no hay nada que borrar.
        partes = salida.split()
        tamano = int(partes[1]) if len(partes) > 1 and partes[1].isdigit() else None
        # Se borra por nombre (docker rmi <nombre>): por id fallaría si tiene otros tags, y
        # así nunca se borra un tag que no esté en el harness.yaml.
        elemento = Elemento(IMAGEN, imagen, imagen, "del harness.yaml", tamano)
        usuarios = _contenedores_que_usan(ejecutar, f"ancestor={imagen}") - ids_a_borrar
        if usuarios:
            plan.omitidos.append((elemento, _motivo_en_uso(usuarios)))
        else:
            plan.a_borrar.append(elemento)


def _contenedores_que_usan(ejecutar: Ejecutor, filtro: str) -> set[str]:
    """Los ids de TODOS los contenedores (del harness o no) que cumplen el filtro."""
    codigo, salida = ejecutar(
        ["docker", "ps", "-a", "-q", "--no-trunc", "--filter", filtro], TIMEOUT_DOCKER_SEGUNDOS
    )
    if codigo != 0:
        # Si no se puede saber, se asume que está en uso: no se borra.
        return {"(no se pudo comprobar)"}
    return set(salida.split())


def _motivo_en_uso(usuarios: set[str]) -> str:
    cuantos = len(usuarios)
    return f"lo usa{'n' if cuantos > 1 else ''} {cuantos} contenedor{'es' if cuantos > 1 else ''}"


def _tamanos_volumenes(ejecutar: Ejecutor) -> dict[str, int]:
    codigo, salida = ejecutar(
        ["docker", "system", "df", "-v", "--format", "{{json .Volumes}}"],
        TIMEOUT_DOCKER_SEGUNDOS,
    )
    if codigo != 0:
        return {}
    try:
        datos = json.loads(salida)
    except json.JSONDecodeError:
        return {}
    tamanos: dict[str, int] = {}
    for volumen in datos if isinstance(datos, list) else []:
        if not isinstance(volumen, dict):
            continue
        tamano = bytes_de(str(volumen.get("Size", "")))
        if tamano is not None:
            tamanos[str(volumen.get("Name"))] = tamano
    return tamanos


def _comando_borrar(elemento: Elemento) -> list[str]:
    if elemento.tipo == CONTENEDOR:
        # -v: también los volúmenes anónimos del contenedor (nunca uno con nombre).
        return ["docker", "rm", "-f", "-v", elemento.id]
    if elemento.tipo == RED:
        return ["docker", "network", "rm", elemento.id]
    if elemento.tipo == VOLUMEN:
        return ["docker", "volume", "rm", elemento.id]
    return ["docker", "rmi", elemento.id]
