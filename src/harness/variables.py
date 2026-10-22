"""Resolución de las referencias ${NOMBRE} de harness.yaml (docs/harness-yaml.md)."""

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from harness.config_proyecto import NOMBRE_ARCHIVO, ConfigProyecto

PATRON_REFERENCIA = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
TEXTO_OCULTO = "****"


class ErrorVariables(Exception):
    """Una referencia ${NOMBRE} apunta a una variable de entorno que no existe."""


@dataclass
class VariablesResueltas:
    """Variables listas para pasar a los contenedores, y qué valores son secretos."""

    proyecto: dict[str, str] = field(default_factory=dict)
    servicios: dict[str, dict[str, str]] = field(default_factory=dict)
    # Solo lo que vino de ${...}: los valores de prueba escritos en el yaml no son secretos.
    secretos: list[str] = field(default_factory=list)


def resolver_variables(
    config: ConfigProyecto, entorno: Mapping[str, str] = os.environ
) -> VariablesResueltas:
    """Reemplaza ${NOMBRE} con la variable de entorno; si falta alguna, avisa de todas juntas."""
    resueltas = VariablesResueltas()
    faltantes: list[str] = []
    resueltas.proyecto = _resolver_mapa(
        config.variables_entorno, "variables_entorno", entorno, resueltas.secretos, faltantes
    )
    for indice, servicio in enumerate(config.servicios):
        resueltas.servicios[servicio.nombre] = _resolver_mapa(
            servicio.variables,
            f"servicios[{indice}].variables",
            entorno,
            resueltas.secretos,
            faltantes,
        )
    if faltantes:
        lineas = [f"{NOMBRE_ARCHIVO} usa variables de entorno que no existen:"]
        lineas += [f"  {faltante}" for faltante in faltantes]
        raise ErrorVariables("\n".join(lineas))
    return resueltas


def _resolver_mapa(
    variables: Mapping[str, str],
    ruta: str,
    entorno: Mapping[str, str],
    secretos: list[str],
    faltantes: list[str],
) -> dict[str, str]:
    resultado: dict[str, str] = {}
    for clave, valor in variables.items():
        for nombre in PATRON_REFERENCIA.findall(valor):
            if nombre not in entorno:
                faltantes.append(f"{ruta}.{clave}: falta la variable de entorno {nombre}")
            elif entorno[nombre] and entorno[nombre] not in secretos:
                secretos.append(entorno[nombre])
        resultado[clave] = PATRON_REFERENCIA.sub(
            lambda coincidencia: entorno.get(coincidencia[1], ""), valor
        )
    return resultado


def ocultar_secretos(texto: str, secretos: Sequence[str]) -> str:
    """Reemplaza cada secreto del texto; los más largos primero, por si uno contiene a otro."""
    for secreto in sorted(secretos, key=len, reverse=True):
        if secreto:
            texto = texto.replace(secreto, TEXTO_OCULTO)
    return texto
