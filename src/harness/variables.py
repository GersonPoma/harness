"""Resolución de las referencias ${NOMBRE} de harness.yaml (docs/harness-yaml.md)."""

import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from harness.config_proyecto import NOMBRE_ARCHIVO, ConfigProyecto

PATRON_REFERENCIA = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
TEXTO_OCULTO = "****"
# Un secreto más corto no se oculta: reemplazarlo en todo el texto rompería el código y los
# nombres de campos del log (con "abc" por error en el .env, cada "abc" sería "****"). Un token
# real nunca es tan corto; si lo es, se avisa al cargar la configuración.
MIN_CARACTERES_SECRETO = 8


class ErrorVariables(Exception):
    """Una referencia ${NOMBRE} apunta a una variable de entorno que no existe."""


@dataclass
class VariablesResueltas:
    """Variables listas para pasar a los contenedores, y qué valores son secretos."""

    proyecto: dict[str, str] = field(default_factory=dict)
    servicios: dict[str, dict[str, str]] = field(default_factory=dict)
    # Solo lo que vino de ${...}: los valores de prueba escritos en el yaml no son secretos.
    secretos: list[str] = field(default_factory=list)
    # Las variables de entorno de donde salieron, para avisar si alguna es demasiado corta.
    nombres_secretos: list[str] = field(default_factory=list)


def resolver_variables(
    config: ConfigProyecto, entorno: Mapping[str, str] = os.environ
) -> VariablesResueltas:
    """Reemplaza ${NOMBRE} con la variable de entorno; si falta alguna, avisa de todas juntas."""
    resueltas = VariablesResueltas()
    faltantes: list[str] = []
    resueltas.proyecto = resolver_mapa(
        config.variables_entorno, "variables_entorno", entorno, resueltas.secretos, faltantes,
        resueltas.nombres_secretos,
    )
    for indice, servicio in enumerate(config.servicios):
        resueltas.servicios[servicio.nombre] = resolver_mapa(
            servicio.variables,
            f"servicios[{indice}].variables",
            entorno,
            resueltas.secretos,
            faltantes,
            resueltas.nombres_secretos,
        )
    if faltantes:
        lineas = [f"{NOMBRE_ARCHIVO} usa variables de entorno que no existen:"]
        lineas += [f"  {faltante}" for faltante in faltantes]
        raise ErrorVariables("\n".join(lineas))
    return resueltas


def resolver_mapa(
    variables: Mapping[str, str],
    ruta: str,
    entorno: Mapping[str, str],
    secretos: list[str],
    faltantes: list[str],
    nombres: list[str] | None = None,
) -> dict[str, str]:
    """Reemplaza ${NOMBRE} en cada valor; anota los secretos usados y las variables que faltan.

    También la usa mcp.json (config_mcp), con la misma sintaxis que harness.yaml. En `nombres`
    anota de qué variables salieron los secretos.
    """
    resultado: dict[str, str] = {}
    for clave, valor in variables.items():
        for nombre in PATRON_REFERENCIA.findall(valor):
            if nombre not in entorno:
                faltantes.append(f"{ruta}.{clave}: falta la variable de entorno {nombre}")
                continue
            if entorno[nombre] and entorno[nombre] not in secretos:
                secretos.append(entorno[nombre])
            if nombres is not None and nombre not in nombres:
                nombres.append(nombre)
        resultado[clave] = PATRON_REFERENCIA.sub(
            lambda coincidencia: entorno.get(coincidencia[1], ""), valor
        )
    return resultado


def ocultar_secretos(texto: str, secretos: Sequence[str]) -> str:
    """Reemplaza cada secreto del texto; los más largos primero, por si uno contiene a otro.

    Los de menos de MIN_CARACTERES_SECRETO no se tocan (ver avisos_secretos_cortos).
    """
    for secreto in sorted(secretos, key=len, reverse=True):
        if len(secreto) >= MIN_CARACTERES_SECRETO:
            texto = texto.replace(secreto, TEXTO_OCULTO)
    return texto


def avisos_secretos_cortos(
    nombres: Iterable[str], entorno: Mapping[str, str] = os.environ
) -> list[str]:
    """Tokens de APIs (modelo, GitHub, Jira, mcp.json) demasiado cortos: casi seguro un error."""
    return [
        f"El valor de {nombre} es demasiado corto para ser un token real: revisa el .env."
        for nombre in _cortas(nombres, entorno)
    ]


def avisos_variables_cortas_yaml(
    nombres: Iterable[str], entorno: Mapping[str, str] = os.environ
) -> list[str]:
    """Variables de harness.yaml cortas: suelen ser a propósito (la contraseña de una base de
    prueba), así que no es un error, pero conviene saber que no se ocultan."""
    return [
        f"El valor de {nombre} (harness.yaml) es corto y no se oculta en la consola ni en el "
        f"log; si es un secreto real, usa uno de al menos {MIN_CARACTERES_SECRETO} caracteres."
        for nombre in _cortas(nombres, entorno)
    ]


def _cortas(nombres: Iterable[str], entorno: Mapping[str, str]) -> list[str]:
    cortas = []
    for nombre in dict.fromkeys(nombres):
        valor = entorno.get(nombre, "").strip()
        if valor and len(valor) < MIN_CARACTERES_SECRETO:
            cortas.append(nombre)
    return cortas
