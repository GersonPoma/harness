"""Comandos de la configuración por usuario (~/.harness/config.toml): harness config,
harness perfiles y harness perfil.

Nunca se muestra un secreto completo ni se recibe por argumento: las credenciales se piden con
la entrada oculta (o se leen de stdin, sin terminal) y se muestran enmascaradas.
"""

import os
import sys
import time
from pathlib import Path
from typing import Annotated

import typer
from dotenv import dotenv_values

from harness import config as configuracion
from harness import consola, menu
from harness.config import (
    PREFIJO_VARIABLES_HARNESS,
    VAR_API_KEY_ENV,
    VAR_BASE_URL,
    VAR_JIRA_EMAIL,
    VAR_JIRA_TOKEN,
    VAR_JIRA_URL,
    VAR_MODELO,
    VAR_PROVEEDOR,
    VAR_RESPALDO,
    VAR_TOKEN_GITHUB,
    ErrorConfiguracion,
    cargar_env,
    origen_variable,
)
from harness.config_usuario import (
    PATRON_PERFIL,
    PATRON_VARIABLE,
    ConfigUsuario,
    ErrorConfigUsuario,
    JiraUsuario,
    Perfil,
    crear_mcp_usuario,
    enmascarar,
    guardar_config_usuario,
    leer_config_usuario,
    ruta_config,
    ruta_mcp_usuario,
)
from harness.proveedores.base import ErrorProveedor
from harness.proveedores.fabrica import crear_proveedor
from harness.proveedores.presets import PRESETS, PROVEEDOR_PERSONALIZADO
from harness.tipos import Herramienta, Mensaje, Rol

PROVEEDOR_POR_DEFECTO = "gemini"
# Otras credenciales que se reconocen al importar el .env y al mostrar la configuración.
VAR_JIRA_MCP_TOKEN = "JIRA_MCP_TOKEN"
CREDENCIALES_CONOCIDAS = (
    *(p.variable_api_key for p in PRESETS.values() if p.variable_api_key),
    VAR_TOKEN_GITHUB,
    VAR_JIRA_TOKEN,
    VAR_JIRA_MCP_TOKEN,
)
MENSAJE_SIN_TERMINAL = (
    "harness config pregunta los valores y no hay terminal interactiva. Sin terminal: "
    "'harness config --importar-env' (pasa el .env de desarrollo a config.toml), "
    "'harness perfil agregar <nombre> --proveedor X --modelo Y' y "
    "'harness config --credencial NOMBRE' (el valor por stdin)."
)


def config(
    mostrar: Annotated[
        bool, typer.Option("--mostrar", help="Mostrar la configuración (secretos enmascarados).")
    ] = False,
    credencial: Annotated[
        str | None,
        typer.Option(
            metavar="NOMBRE",
            help="Guardar una credencial (GITHUB_TOKEN...): se pide oculta, o se lee de stdin "
            "sin terminal. Nunca el valor por argumento.",
        ),
    ] = None,
    importar_env: Annotated[
        bool,
        typer.Option(
            "--importar-env", help="Copiar el modelo y las credenciales del .env de desarrollo."
        ),
    ] = False,
    perfil: Annotated[
        str | None, typer.Option(help="Con --importar-env: nombre del perfil que se crea.")
    ] = None,
) -> None:
    """Guarda el modelo y las credenciales en ~/.harness/config.toml (la primera vez)."""
    usuario = _leer()
    if mostrar:
        mostrar_configuracion(usuario)
    elif credencial is not None:
        _guardar_credencial(usuario, credencial)
    elif importar_env:
        _importar_env(usuario, perfil)
    elif not consola.es_terminal_interactiva():
        consola.mostrar_error(MENSAJE_SIN_TERMINAL)
        raise typer.Exit(1)
    else:
        _asistente(usuario)


def mostrar_configuracion(usuario: ConfigUsuario) -> None:
    """Ruta, perfiles, respaldo, revisor y cada credencial enmascarada con su origen."""
    _cargar_entorno()
    mostrar_perfiles(usuario)
    nombres = [*dict.fromkeys([*CREDENCIALES_CONOCIDAS, *usuario.credenciales])]
    filas = [(nombre, *_valor_y_origen(nombre, usuario)) for nombre in nombres]
    consola.mostrar_credenciales(filas, usuario.jira.url, usuario.jira.email)


def _asistente(usuario: ConfigUsuario) -> None:
    ruta = ruta_config()
    consola.mostrar_ruta_config(ruta, ruta.is_file())
    actual = usuario.perfiles.get(usuario.perfil or "")
    perfil = _preguntar_perfil(actual)
    _preguntar_credencial(usuario, perfil.api_key_env or PRESETS[perfil.proveedor].variable_api_key)
    nombre = _preguntar_nombre(usuario.perfil or perfil.proveedor)
    usuario.perfiles[nombre] = perfil
    usuario.perfil = nombre
    if consola.preguntar_si_no("¿Guardar el token de GitHub para abrir el PR?"):
        _preguntar_credencial(usuario, VAR_TOKEN_GITHUB)
    if consola.preguntar_si_no("¿Configurar Jira?"):
        usuario.jira = JiraUsuario(
            url=consola.preguntar("URL del sitio (https://...atlassian.net)", usuario.jira.url),
            email=consola.preguntar("Email de tu cuenta de Jira", usuario.jira.email),
        )
        _preguntar_credencial(usuario, VAR_JIRA_TOKEN)
    es_crear_mcp = not ruta_mcp_usuario().exists() and consola.preguntar_si_no(
        f"¿Crear {ruta_mcp_usuario()} a partir del ejemplo?"
    )
    _guardar(usuario)
    if es_crear_mcp:
        consola.mostrar_ok(f"Creado {crear_mcp_usuario()} (revisa los servidores que quieras).")
    consola.mostrar_ok(
        f"Guardado. Perfil predeterminado: {nombre} ({perfil.proveedor} / {perfil.modelo})."
    )
    consola.mostrar_texto(f"  Comprueba la key con: harness perfil probar {nombre}")
    _avisar_si_el_env_gana()


def preguntar_perfil(actual: Perfil | None = None) -> Perfil:
    """Proveedor, modelo y, para personalizado, URL y variable de la key."""
    return _preguntar_perfil(actual)


def _preguntar_perfil(actual: Perfil | None) -> Perfil:
    while True:
        proveedor = consola.preguntar(
            f"Proveedor ({', '.join(PRESETS)})",
            actual.proveedor if actual else PROVEEDOR_POR_DEFECTO,
        ).lower()
        if proveedor in PRESETS:
            break
        consola.mostrar_aviso(f"'{proveedor}' no es un proveedor conocido.")
    es_mismo = actual is not None and actual.proveedor == proveedor
    modelo = _preguntar_obligatorio("Modelo", actual.modelo if es_mismo else None)
    if proveedor != PROVEEDOR_PERSONALIZADO:
        return Perfil(proveedor=proveedor, modelo=modelo)
    base_url = _preguntar_obligatorio(
        "URL base (https://...)", actual.base_url if es_mismo else None
    )
    while True:
        variable = _preguntar_obligatorio(
            "Variable de la API key", actual.api_key_env if es_mismo else None
        )
        if PATRON_VARIABLE.fullmatch(variable):
            break
        consola.mostrar_aviso(f"'{variable}' no es un nombre de variable válido.")
    return Perfil(proveedor=proveedor, modelo=modelo, base_url=base_url, api_key_env=variable)


def preguntar_credencial(usuario: ConfigUsuario, variable: str) -> None:
    """Pide una credencial oculta; Enter deja la que ya está."""
    _preguntar_credencial(usuario, variable)


def _preguntar_credencial(usuario: ConfigUsuario, variable: str | None) -> None:
    if variable is None:
        return
    guardada = usuario.credenciales.get(variable)
    origen = origen_variable(variable)
    if guardada:
        pista = f"guardada: {enmascarar(guardada)}, Enter para dejarla"
    elif origen is not None:
        pista = f"está en {origen}, Enter para no guardarla aquí"
    else:
        pista = "no se muestra al escribir"
    valor = consola.preguntar_oculto(f"{variable} ({pista})")
    if valor:
        usuario.credenciales[variable] = valor


def _preguntar_nombre(defecto: str) -> str:
    while True:
        nombre = consola.preguntar("Nombre del perfil", defecto).lower()
        if PATRON_PERFIL.fullmatch(nombre):
            return nombre
        consola.mostrar_aviso("El nombre solo admite minúsculas, números, '-' y '_'.")


def _preguntar_obligatorio(texto: str, defecto: str | None) -> str:
    while True:
        valor = consola.preguntar(texto, defecto)
        if valor:
            return valor
        consola.mostrar_aviso("Este valor hace falta.")


def _guardar_credencial(usuario: ConfigUsuario, nombre: str) -> None:
    if not PATRON_VARIABLE.fullmatch(nombre):
        consola.mostrar_error(f"'{nombre}' no es un nombre de variable válido.")
        raise typer.Exit(1)
    if nombre.startswith(PREFIJO_VARIABLES_HARNESS):
        consola.mostrar_error(
            f"{nombre} es configuración, no una credencial: usa un perfil (harness perfil "
            "agregar)."
        )
        raise typer.Exit(1)
    if consola.es_terminal_interactiva():
        valor = consola.preguntar_oculto(f"{nombre} (no se muestra al escribir)")
    else:
        valor = sys.stdin.read().strip()
    if not valor:
        consola.mostrar_error(f"No se recibió ningún valor para {nombre}: no se guardó nada.")
        raise typer.Exit(1)
    usuario.credenciales[nombre] = valor
    _guardar(usuario)
    consola.mostrar_ok(f"{nombre} guardada en {ruta_config()} ({enmascarar(valor)}).")
    _cargar_entorno()
    origen = origen_variable(nombre)
    if origen not in (None, configuracion.ORIGEN_USUARIO):
        consola.mostrar_aviso(f"{nombre} también está en {origen}, que gana sobre config.toml.")


def _importar_env(usuario: ConfigUsuario, nombre: str | None) -> None:
    ruta_env = configuracion.RUTA_ENV
    if not ruta_env.is_file():
        consola.mostrar_error(f"No hay .env de desarrollo que importar ({ruta_env}).")
        raise typer.Exit(1)
    valores = {k: (v or "").strip() for k, v in dotenv_values(ruta_env).items()}
    importados: list[str] = []
    perfil = _perfil_del_env(valores)
    if perfil is not None:
        nombre = (nombre or perfil.proveedor).lower()
        if not PATRON_PERFIL.fullmatch(nombre):
            consola.mostrar_error("El nombre del perfil solo admite [a-z0-9_-].")
            raise typer.Exit(1)
        usuario.perfiles[nombre] = perfil
        usuario.perfil = usuario.perfil or nombre
        importados.append(f"perfil {nombre}: {perfil.proveedor} / {perfil.modelo}")
    propia = [perfil.api_key_env] if perfil and perfil.api_key_env else []
    variables = [*CREDENCIALES_CONOCIDAS, *propia]
    for variable in dict.fromkeys(variables):
        if valores.get(variable):
            usuario.credenciales[variable] = valores[variable]
            importados.append(f"{variable} ({enmascarar(valores[variable])})")
    if valores.get(VAR_JIRA_URL) or valores.get(VAR_JIRA_EMAIL):
        usuario.jira = JiraUsuario(
            url=valores.get(VAR_JIRA_URL) or usuario.jira.url,
            email=valores.get(VAR_JIRA_EMAIL) or usuario.jira.email,
        )
        importados.append("Jira (URL y email)")
    respaldo = [e.strip() for e in valores.get(VAR_RESPALDO, "").split(",") if e.strip()]
    if respaldo:
        usuario.respaldo = respaldo
        importados.append(f"respaldo: {', '.join(respaldo)}")
    if not importados:
        consola.mostrar_aviso(f"El .env ({ruta_env}) no tiene nada que importar.")
        return
    _guardar(usuario)
    consola.mostrar_ok(f"Importado del .env a {ruta_config()}:")
    for linea in importados:
        consola.mostrar_texto(f"  {linea}")
    _avisar_si_el_env_gana()


def _perfil_del_env(valores: dict[str, str]) -> Perfil | None:
    proveedor = valores.get(VAR_PROVEEDOR, "").lower()
    modelo = valores.get(VAR_MODELO, "")
    if not proveedor or not modelo:
        return None
    try:
        if proveedor == PROVEEDOR_PERSONALIZADO:
            return Perfil(
                proveedor=proveedor, modelo=modelo, base_url=valores.get(VAR_BASE_URL) or None,
                api_key_env=valores.get(VAR_API_KEY_ENV) or None,
            )
        return Perfil(proveedor=proveedor, modelo=modelo)
    except ValueError as error:
        consola.mostrar_aviso(f"El modelo del .env no se importó: {error}")
        return None


def _avisar_si_el_env_gana() -> None:
    _cargar_entorno()
    origen = origen_variable(VAR_PROVEEDOR)
    if origen is not None:
        consola.mostrar_aviso(
            f"{VAR_PROVEEDOR} está en {origen}: mientras esté, gana sobre el perfil "
            "predeterminado (sácalo, o usa --perfil)."
        )


def mostrar_perfiles(usuario: ConfigUsuario) -> None:
    """La lista de perfiles con el estado de su key, el respaldo, el revisor y el modelo en uso."""
    consola.mostrar_perfiles(
        ruta_config(), usuario, _estado_keys(usuario), _respaldo_en_uso(usuario),
        _revisor_en_uso(usuario), _modelo_en_uso(),
    )


def _respaldo_en_uso(usuario: ConfigUsuario) -> str | None:
    """El respaldo que se usaría: HARNESS_RESPALDO gana sobre el de config.toml."""
    texto = os.environ.get(VAR_RESPALDO, "").strip()
    if texto:
        return f"{texto} ({VAR_RESPALDO} {_de(origen_variable(VAR_RESPALDO))})"
    if usuario.respaldo:
        return f"{', '.join(usuario.respaldo)} (config.toml)"
    return None


def _revisor_en_uso(usuario: ConfigUsuario) -> str | None:
    """Quién revisa el diff, si no es el mismo modelo que implementa."""
    return f"{usuario.revisor} (config.toml)" if usuario.revisor else None


def _de(origen: str | None) -> str:
    origen = origen or configuracion.ORIGEN_ENTORNO
    return f"del {origen.removeprefix('el ')}" if origen.startswith("el ") else f"de {origen}"


def _estado_keys(usuario: ConfigUsuario) -> dict[str, bool]:
    """Para cada perfil, si la variable de su key tiene valor en algún lado."""
    _cargar_entorno()
    estado = {}
    for nombre, perfil in usuario.perfiles.items():
        variable = perfil.api_key_env or PRESETS[perfil.proveedor].variable_api_key or ""
        estado[nombre] = origen_variable(variable) is not None
    return estado


def _modelo_en_uso() -> str | None:
    """Qué modelo usaría un run sin opciones, y por qué ("gemini / x (predeterminado)")."""
    try:
        elegido = configuracion.cargar_config()
    except ErrorConfiguracion as error:
        return f"ninguno ({error})"
    nombre = f"{elegido.proveedor} / {elegido.modelo}"
    detalle = f"perfil {elegido.perfil}, " if elegido.perfil else ""
    return f"{nombre} ({detalle}{elegido.origen})"


def _valor_y_origen(nombre: str, usuario: ConfigUsuario) -> tuple[str | None, str | None]:
    """El valor enmascarado que gana y de dónde sale (avisando si config.toml tiene otro)."""
    valor = os.environ.get(nombre, "").strip()
    if not valor:
        return None, None
    origen = origen_variable(nombre) or configuracion.ORIGEN_ENTORNO
    if origen != configuracion.ORIGEN_USUARIO and nombre in usuario.credenciales:
        origen += "; config.toml también tiene una"
    return enmascarar(valor), origen


def _cargar_entorno() -> None:
    try:
        cargar_env()
    except ErrorConfiguracion as error:
        consola.mostrar_error(str(error))
        raise typer.Exit(1) from error


def _leer() -> ConfigUsuario:
    try:
        return leer_config_usuario()
    except ErrorConfigUsuario as error:
        consola.mostrar_error(str(error))
        raise typer.Exit(1) from error


def _guardar(usuario: ConfigUsuario) -> Path:
    try:
        return guardar_config_usuario(ConfigUsuario.model_validate(usuario.model_dump()))
    except (ErrorConfigUsuario, ValueError) as error:
        consola.mostrar_error(f"No se guardó: {error}")
        raise typer.Exit(1) from error


# --- harness perfiles y harness perfil ------------------------------------------------------

perfil_app = typer.Typer(
    help="Perfiles de modelo de ~/.harness/config.toml (la lista: harness perfiles).",
    no_args_is_help=True,
)
NOMBRE_HERRAMIENTA_PRUEBA = "responder_prueba"
MENSAJES_PRUEBA = (
    "Eres una prueba de conexión del harness: responde solo llamando a la herramienta.",
    f"Llama a la herramienta {NOMBRE_HERRAMIENTA_PRUEBA} con el texto 'ok'.",
)
AYUDA_PROVEEDOR_PERFIL = f"Proveedor ({', '.join(PRESETS)})."
AYUDA_BASE_URL = "Solo para personalizado: la URL base de la API."
AYUDA_API_KEY_ENV = "Solo para personalizado: la variable con la API key."
SIN_PERFILES = "No hay perfiles: créalos con 'harness config' o 'harness perfil agregar <nombre>'."


def perfiles() -> None:
    """Lista los perfiles de ~/.harness/config.toml y marca el predeterminado."""
    mostrar_perfiles(_leer())


@perfil_app.command("usar")
def usar(
    nombre: Annotated[str | None, typer.Argument(help="El perfil; sin él, un menú.")] = None,
) -> None:
    """Cambia el perfil predeterminado (queda guardado)."""
    usuario = _leer()
    if not usuario.perfiles:
        consola.mostrar_error(SIN_PERFILES)
        raise typer.Exit(1)
    if nombre is None:
        nombre = _elegir_con_menu(usuario)
    _existente(usuario, nombre)
    usuario.perfil = nombre
    _guardar(usuario)
    perfil = usuario.perfiles[nombre]
    consola.mostrar_ok(
        f"Perfil predeterminado: {nombre} ({perfil.proveedor} / {perfil.modelo})."
    )
    _avisar_si_el_env_gana()


@perfil_app.command("agregar")
def agregar(
    nombre: Annotated[str, typer.Argument(help="Nombre del perfil ([a-z0-9_-]).")],
    proveedor: Annotated[str | None, typer.Option(help=AYUDA_PROVEEDOR_PERFIL)] = None,
    modelo: Annotated[str | None, typer.Option(help="Id del modelo.")] = None,
    base_url: Annotated[str | None, typer.Option(help=AYUDA_BASE_URL)] = None,
    api_key_env: Annotated[str | None, typer.Option(help=AYUDA_API_KEY_ENV)] = None,
) -> None:
    """Agrega un perfil; con terminal pregunta lo que falte (y la key si no está en ningún lado)."""
    usuario = _leer()
    nombre = _validar_nombre(nombre)
    if nombre in usuario.perfiles:
        consola.mostrar_error(f"El perfil '{nombre}' ya existe: cámbialo con 'harness perfil "
                              f"editar {nombre}'.")
        raise typer.Exit(1)
    perfil = _armar_perfil(None, proveedor, modelo, base_url, api_key_env)
    usuario.perfiles[nombre] = perfil
    usuario.perfil = usuario.perfil or nombre
    _pedir_key_si_falta(usuario, perfil)
    _guardar(usuario)
    es_predeterminado = usuario.perfil == nombre
    consola.mostrar_ok(
        f"Perfil {nombre} agregado ({perfil.proveedor} / {perfil.modelo})"
        + (", y es el predeterminado." if es_predeterminado else ".")
    )


@perfil_app.command("editar")
def editar(
    nombre: Annotated[str, typer.Argument(help="El perfil a cambiar.")],
    proveedor: Annotated[str | None, typer.Option(help=AYUDA_PROVEEDOR_PERFIL)] = None,
    modelo: Annotated[str | None, typer.Option(help="Id del modelo.")] = None,
    base_url: Annotated[str | None, typer.Option(help=AYUDA_BASE_URL)] = None,
    api_key_env: Annotated[str | None, typer.Option(help=AYUDA_API_KEY_ENV)] = None,
) -> None:
    """Cambia un perfil; con terminal pregunta, con los valores actuales por defecto."""
    usuario = _leer()
    actual = _existente(usuario, nombre)
    opciones = (proveedor, modelo, base_url, api_key_env)
    if not consola.es_terminal_interactiva() and not any(opciones):
        consola.mostrar_error(
            "Sin terminal, di qué cambiar: --proveedor, --modelo, --base-url o --api-key-env."
        )
        raise typer.Exit(1)
    perfil = _armar_perfil(actual, *opciones)
    usuario.perfiles[nombre] = perfil
    _pedir_key_si_falta(usuario, perfil)
    _guardar(usuario)
    consola.mostrar_ok(f"Perfil {nombre}: {perfil.proveedor} / {perfil.modelo}.")


@perfil_app.command("borrar")
def borrar(
    nombre: Annotated[str, typer.Argument(help="El perfil a borrar.")],
    si: Annotated[bool, typer.Option("--si", help="No preguntar (para scripts).")] = False,
) -> None:
    """Borra un perfil (las credenciales quedan: las pueden usar otros)."""
    usuario = _leer()
    _existente(usuario, nombre)
    if not si:
        if not consola.es_terminal_interactiva():
            consola.mostrar_error(
                f"Sin terminal no se borra: para confirmar, 'harness perfil borrar {nombre} --si'."
            )
            raise typer.Exit(1)
        if not consola.preguntar_si_no(f"¿Borrar el perfil {nombre}?"):
            consola.mostrar_aviso("No se borró nada.")
            return
    del usuario.perfiles[nombre]
    avisos = []
    if usuario.perfil == nombre:
        usuario.perfil = None
        avisos.append(
            "Ya no hay perfil predeterminado: elige uno con 'harness perfil usar'."
        )
    if nombre in usuario.respaldo:
        usuario.respaldo = [e for e in usuario.respaldo if e != nombre]
        avisos.append(f"Se sacó {nombre} del respaldo.")
    if usuario.revisor == nombre:
        usuario.revisor = None
        avisos.append(f"{nombre} era el revisor: ahora revisa el mismo modelo que implementa.")
    _guardar(usuario)
    consola.mostrar_ok(f"Perfil {nombre} borrado.")
    for aviso in avisos:
        consola.mostrar_aviso(aviso)


@perfil_app.command("probar")
def probar(
    nombre: Annotated[
        str | None, typer.Argument(help="El perfil; sin él, el predeterminado.")
    ] = None,
) -> None:
    """Hace un pedido de prueba: comprueba la key y que el modelo llame herramientas."""
    usuario = _leer()
    nombre = nombre or usuario.perfil
    if nombre is None:
        consola.mostrar_error(
            "No hay perfil predeterminado: di cuál, 'harness perfil probar <nombre>'."
        )
        raise typer.Exit(1)
    try:
        elegido = configuracion.cargar_config(perfil=nombre)
        proveedor = crear_proveedor(elegido, consola.mostrar_aviso)
        inicio = time.monotonic()
        with consola.estado(f"Probando {nombre} ({elegido.proveedor} / {elegido.modelo})..."):
            respuesta = proveedor.responder(
                _mensajes_prueba(), [_herramienta_prueba()], NOMBRE_HERRAMIENTA_PRUEBA
            )
        segundos = time.monotonic() - inicio
    except (ErrorConfiguracion, ErrorProveedor) as error:
        consola.mostrar_error(f"{nombre}: {error}")
        raise typer.Exit(1) from error
    tokens = respuesta.tokens_entrada + respuesta.tokens_salida
    if any(llamada.nombre == NOMBRE_HERRAMIENTA_PRUEBA for llamada in respuesta.llamadas):
        consola.mostrar_ok(
            f"{nombre}: la key funciona y el modelo llamó a la herramienta "
            f"({consola.formatear_duracion(segundos)}, {tokens} tokens)."
        )
        return
    consola.mostrar_error(
        f"{nombre}: la key funciona, pero el modelo respondió sin llamar a la herramienta: "
        "puede no soportar tool calling (el harness lo necesita)."
    )
    raise typer.Exit(1)


def _mensajes_prueba() -> list[Mensaje]:
    sistema, pedido = MENSAJES_PRUEBA
    return [Mensaje(Rol.SISTEMA, sistema), Mensaje(Rol.USUARIO, pedido)]


def _herramienta_prueba() -> Herramienta:
    parametros = {
        "type": "object",
        "properties": {"texto": {"type": "string", "description": "El texto pedido."}},
        "required": ["texto"],
    }
    return Herramienta(
        NOMBRE_HERRAMIENTA_PRUEBA, "Responde a la prueba de conexión.", parametros,
        lambda texto: texto,
    )


def _elegir_con_menu(usuario: ConfigUsuario) -> str:
    if not consola.es_terminal_interactiva():
        consola.mostrar_error("Sin terminal no hay menú: di cuál, 'harness perfil usar <nombre>'.")
        raise typer.Exit(1)
    nombres = list(usuario.perfiles)
    opciones = [
        f"{n}  ({usuario.perfiles[n].proveedor} / {usuario.perfiles[n].modelo})"
        + ("  [predeterminado]" if n == usuario.perfil else "")
        for n in nombres
    ]
    inicial = nombres.index(usuario.perfil) if usuario.perfil in nombres else 0
    indice = menu.elegir(opciones, consola.consola, inicial)
    if indice is None:
        consola.mostrar_aviso("Cancelado: el predeterminado no cambió.")
        raise typer.Exit(0)
    return nombres[indice]


def _armar_perfil(
    actual: Perfil | None,
    proveedor: str | None,
    modelo: str | None,
    base_url: str | None,
    api_key_env: str | None,
) -> Perfil:
    """El perfil con las opciones; con terminal y sin opciones, se pregunta."""
    opciones = (proveedor, modelo, base_url, api_key_env)
    if not any(opciones) and consola.es_terminal_interactiva():
        return _preguntar_perfil(actual)
    base = actual.model_dump() if actual else {}
    cambios = {"proveedor": proveedor, "modelo": modelo, "base_url": base_url,
               "api_key_env": api_key_env}
    datos = {**base, **{k: v for k, v in cambios.items() if v is not None}}
    if proveedor and proveedor.lower() != PROVEEDOR_PERSONALIZADO:
        datos.pop("base_url", None)
        datos.pop("api_key_env", None)
    faltan = [f"--{clave}" for clave in ("proveedor", "modelo") if not datos.get(clave)]
    if faltan:
        consola.mostrar_error(f"Falta {' y '.join(faltan)}.")
        raise typer.Exit(1)
    try:
        return Perfil.model_validate(datos)
    except ValueError as error:
        consola.mostrar_error(f"Perfil no válido: {error}")
        raise typer.Exit(1) from error


def _pedir_key_si_falta(usuario: ConfigUsuario, perfil: Perfil) -> None:
    variable = perfil.api_key_env or PRESETS[perfil.proveedor].variable_api_key
    if variable is None or variable in usuario.credenciales:
        return
    _cargar_entorno()
    if origen_variable(variable) is not None:
        return
    if consola.es_terminal_interactiva():
        _preguntar_credencial(usuario, variable)
    if variable not in usuario.credenciales:
        consola.mostrar_aviso(f"Falta {variable}: guárdala con 'harness config --credencial "
                              f"{variable}'.")


def _validar_nombre(nombre: str) -> str:
    nombre = nombre.lower()
    if not PATRON_PERFIL.fullmatch(nombre):
        consola.mostrar_error("El nombre del perfil solo admite minúsculas, números, '-' y '_'.")
        raise typer.Exit(1)
    return nombre


def _existente(usuario: ConfigUsuario, nombre: str) -> Perfil:
    perfil = usuario.perfiles.get(nombre)
    if perfil is None:
        existentes = ", ".join(usuario.perfiles) or "ninguno"
        consola.mostrar_error(f"No existe el perfil '{nombre}' (perfiles: {existentes}).")
        raise typer.Exit(1)
    return perfil
