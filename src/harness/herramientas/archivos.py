"""Herramientas de archivos que se le ofrecen al modelo."""

from collections.abc import Callable, Sequence
from pathlib import Path

from harness.carpetas import CARPETAS_IGNORADAS
from harness.herramientas.rutas import resolver_ruta, validar_escritura

# Limita lo que se manda al modelo: los planes gratis tienen pocos tokens por minuto.
MAX_CARACTERES_LECTURA = 12_000
MAX_ENTRADAS_LISTADO = 200
PREFIJO_ARCHIVO = "Archivo: "
# Cuántas líneas de coincidencia se le muestran al modelo si el fragmento se repite.
MAX_LINEAS_COINCIDENCIAS = 10
# El modelo a veces reescribe un archivo entero para cambiar unas líneas (TAREAS-4): gasta
# tokens y puede perder lo que no copió. Se le sugiere editar_archivo para la próxima vez.
AVISO_REESCRIBIR = (
    "Aviso: el archivo ya existía y lo reemplazaste entero. Para los próximos cambios en "
    "archivos existentes usa editar_archivo, que cambia solo el fragmento."
)


class ErrorHerramienta(Exception):
    """Error al ejecutar una herramienta; su mensaje se le devuelve al modelo."""


def leer_archivo(repo: Path, ruta: str) -> str:
    """Devuelve el contenido de un archivo de texto del repo, después de "Archivo: <ruta>".

    El encabezado identifica cada lectura cuando el modelo pide varias en un mismo paso.
    """
    return f"{PREFIJO_ARCHIVO}{ruta}\n{_leer_contenido(repo, ruta)}"


def _leer_contenido(repo: Path, ruta: str) -> str:
    archivo = resolver_ruta(repo, ruta)
    if not archivo.is_file():
        raise ErrorHerramienta(f"No existe el archivo '{ruta}'.")
    try:
        contenido = archivo.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ErrorHerramienta(f"'{ruta}' no es un archivo de texto UTF-8.") from error
    if len(contenido) > MAX_CARACTERES_LECTURA:
        return (
            f"{contenido[:MAX_CARACTERES_LECTURA]}\n"
            f"[... recortado: se muestran {MAX_CARACTERES_LECTURA} de {len(contenido)} "
            f"caracteres]"
        )
    return contenido


def escribir_archivo(
    repo: Path,
    ruta: str,
    contenido: str,
    protegidos: Sequence[str] = (),
    al_escribir: Callable[[str], None] | None = None,
) -> str:
    """Crea o reemplaza un archivo del repo, creando las carpetas que falten.

    `protegidos` son los globs de archivos_protegidos; `al_escribir` recibe la ruta relativa
    escrita (el entorno Docker la usa para saber si debe reinstalar dependencias).
    """
    archivo = resolver_ruta(repo, ruta)
    validar_escritura(repo, archivo, protegidos)
    if archivo.is_dir():
        raise ErrorHerramienta(f"'{ruta}' es una carpeta, no un archivo.")
    es_existente = archivo.is_file()
    archivo.parent.mkdir(parents=True, exist_ok=True)
    # newline="" para escribir los saltos de línea tal cual vienen, también en Windows.
    archivo.write_text(contenido, encoding="utf-8", newline="")
    if al_escribir:
        al_escribir(archivo.relative_to(repo.resolve()).as_posix())
    resultado = f"Archivo '{ruta}' escrito ({len(contenido)} caracteres)."
    return f"{resultado} {AVISO_REESCRIBIR}" if es_existente else resultado


def editar_archivo(
    repo: Path,
    ruta: str,
    texto_actual: str,
    texto_nuevo: str,
    reemplazar_todas: bool = False,
    protegidos: Sequence[str] = (),
    al_escribir: Callable[[str], None] | None = None,
) -> str:
    """Reemplaza un fragmento exacto de un archivo existente por otro.

    El fragmento tiene que aparecer una sola vez (o reemplazar_todas). Si el archivo usa
    finales de línea de Windows y el fragmento no, se busca también con \\r\\n.
    """
    archivo = resolver_ruta(repo, ruta)
    validar_escritura(repo, archivo, protegidos)
    if not texto_actual:
        raise ErrorHerramienta("texto_actual está vacío: copia el fragmento que quieres cambiar.")
    if not archivo.is_file():
        raise ErrorHerramienta(
            f"No existe el archivo '{ruta}'. Para crear un archivo usa escribir_archivo."
        )
    contenido = _leer_tal_cual(archivo, ruta)
    buscado, nuevo = _con_finales_del_archivo(contenido, texto_actual, texto_nuevo)
    posiciones = _posiciones(contenido, buscado)
    if not posiciones:
        raise ErrorHerramienta(
            f"texto_actual no aparece en '{ruta}'. Vuelve a leer el archivo y copia el "
            "fragmento exacto (espacios e indentación incluidos)."
        )
    lineas = [contenido.count("\n", 0, posicion) + 1 for posicion in posiciones]
    if len(posiciones) > 1 and not reemplazar_todas:
        raise ErrorHerramienta(
            f"texto_actual aparece {len(posiciones)} veces en '{ruta}', en las líneas "
            f"{_listar_lineas(lineas)}: agrega líneas de contexto para que sea único, o usa "
            "reemplazar_todas: true."
        )
    resultado = contenido.replace(buscado, nuevo, -1 if reemplazar_todas else 1)
    archivo.write_text(resultado, encoding="utf-8", newline="")
    if al_escribir:
        al_escribir(archivo.relative_to(repo.resolve()).as_posix())
    reemplazos = "1 reemplazo" if len(lineas) == 1 else f"{len(lineas)} reemplazos"
    donde = "línea" if len(lineas) == 1 else "líneas"
    return f"Archivo '{ruta}' editado ({reemplazos}, {donde} {_listar_lineas(lineas)})."


def _leer_tal_cual(archivo: Path, ruta: str) -> str:
    # Sin read_text: convertiría \r\n en \n y el archivo se escribiría con otros finales.
    try:
        return archivo.read_bytes().decode("utf-8")
    except UnicodeDecodeError as error:
        raise ErrorHerramienta(f"'{ruta}' no es un archivo de texto UTF-8.") from error


def _con_finales_del_archivo(contenido: str, actual: str, nuevo: str) -> tuple[str, str]:
    """El fragmento y su reemplazo con \\r\\n, si el archivo los usa y el modelo mandó \\n."""
    if actual in contenido or "\r\n" not in contenido or "\r\n" in actual:
        return actual, nuevo
    return actual.replace("\n", "\r\n"), nuevo.replace("\r\n", "\n").replace("\n", "\r\n")


def _posiciones(contenido: str, buscado: str) -> list[int]:
    posiciones = []
    inicio = contenido.find(buscado)
    while inicio >= 0:
        posiciones.append(inicio)
        inicio = contenido.find(buscado, inicio + len(buscado))
    return posiciones


def _listar_lineas(lineas: list[int]) -> str:
    visibles = [str(linea) for linea in lineas[:MAX_LINEAS_COINCIDENCIAS]]
    resto = len(lineas) - MAX_LINEAS_COINCIDENCIAS
    texto = ", ".join(visibles[:-1]) + f" y {visibles[-1]}" if len(visibles) > 1 else visibles[0]
    return f"{texto} (y {resto} más)" if resto > 0 else texto


def listar_directorio(repo: Path, ruta: str = ".") -> str:
    """Lista el contenido de una carpeta del repo; las carpetas terminan en '/'."""
    carpeta = resolver_ruta(repo, ruta)
    if not carpeta.is_dir():
        raise ErrorHerramienta(f"No existe la carpeta '{ruta}'.")
    entradas = sorted(
        (e for e in carpeta.iterdir() if e.name not in CARPETAS_IGNORADAS),
        key=lambda e: (not e.is_dir(), e.name.lower()),
    )
    if not entradas:
        return f"La carpeta '{ruta}' está vacía."
    nombres = [f"{e.name}/" if e.is_dir() else e.name for e in entradas]
    if len(nombres) > MAX_ENTRADAS_LISTADO:
        sobrantes = len(nombres) - MAX_ENTRADAS_LISTADO
        nombres = [*nombres[:MAX_ENTRADAS_LISTADO], f"[... y {sobrantes} entradas más]"]
    return "\n".join(nombres)
