# harness

Harness de IA por consola para generación de código. Recibe un ticket de Jira
muy especificado y, usando un modelo de IA por API (no local), entiende el
proyecto, genera el código, ejecuta pruebas, se autocorrige si hay errores y
termina haciendo push a GitHub.

## Antes de empezar cualquier tarea

Lee el plan completo del proyecto en `docs/plan-harness-ia.md`. Ahí están las
herramientas a implementar, el flujo completo, la memoria, las decisiones
técnicas y el roadmap. Respeta ese plan; si algo debe cambiar, propónlo antes
de hacerlo.

Si la tarea toca la lectura o el uso de `harness.yaml`, sigue al pie de la letra
`docs/harness-yaml.md`: claves, tipos, valores por defecto y reglas de validación.

## Comandos del harness

```
harness run <TICKET>      # procesar un ticket desde cero (clave de Jira o ruta a ticket local)
harness resume <TICKET>   # continuar una ejecución que se cortó
harness init              # generar AGENTS.md / harness.yaml en un repo objetivo
harness config            # guardar credenciales la primera vez
harness version
```

Cada subcomando es una función con `@app.command()` en `src/harness/cli.py`.

## Stack

- Python 3.14 para desarrollo; `requires-python = ">=3.12"` para que otros puedan instalarlo
- **uv** para entorno, dependencias y distribución (NO usar pip ni requirements.txt)
- CLI con `typer` y `rich`
- SDK oficiales de los proveedores de modelos (`anthropic`, `openai`)
- Docker para ejecutar el código generado
- API REST de Jira y API de GitHub

## Estructura

```
harness/
├── CLAUDE.md
├── docs/
│   ├── plan-harness-ia.md  # plan, decisiones y roadmap
│   └── harness-yaml.md     # especificación completa de harness.yaml
├── pyproject.toml          # [project.scripts] define el comando "harness"
├── uv.lock
└── src/harness/
    ├── __init__.py
    └── cli.py              # punto de entrada (typer)
```

Todo el código nuevo va dentro de `src/harness/`.

## Comandos de desarrollo

```
uv add <libreria>         # agregar dependencia (nunca pip install)
uv run harness ...        # ejecutar el harness en desarrollo
uv sync                   # instalar dependencias después de clonar
uv tool install -e .      # dejar "harness" como comando global (editable)
```

## Reglas del proyecto

- **No usar LangChain ni frameworks de agentes.** El bucle de tool calling se
  implementa a mano; ese es el objetivo del proyecto.
- El harness debe ser agnóstico al modelo: todo acceso al LLM pasa por la
  interfaz `ProveedorLLM`. El bucle, la memoria y las sesiones usan solo tipos
  propios (`Mensaje`, `Herramienta`, `LlamadaHerramienta`, `RespuestaModelo`),
  nunca los objetos de un SDK.
- El proveedor y el modelo se configuran por usuario (`~/.harness/config.toml`
  o `.env` en desarrollo), nunca en `harness.yaml`. Los proveedores reintentan
  con espera ante errores 429.
- Todo código generado por el harness se ejecuta **solo dentro de Docker**,
  con timeout y lista de comandos permitidos.
- Si el proyecto objetivo necesita base de datos u otro servicio, el harness lo
  levanta en su propio contenedor (`servicios` en `harness.yaml`), en una red
  privada por ejecución, y lo destruye al terminar (`try/finally`). Nunca se
  conecta a bases de datos instaladas en la máquina del usuario.
- El harness no exige `pyproject.toml` ni `uv` en el proyecto objetivo: se
  adapta a lo que use mediante `docker.instalar` del `harness.yaml`.
- El harness hace push **solo a ramas** (`feature/<TICKET>`), nunca a `main`.
- Credenciales (API del modelo, GitHub, Jira) nunca en el código: variable de
  entorno o `~/.harness/config.toml`. En desarrollo se permite un `.env` local
  (ignorado por Git).
- Cada ejecución guarda un log de pasos (herramienta llamada + resultado).
- La configuración de cada proyecto objetivo vive en la raíz de ese repo
  (`AGENTS.md` y `harness.yaml`); el harness la descubre solo, no se le pasa.

## Memoria (ver plan para el detalle)

- Dentro de una ejecución: la lista `messages`, con recorte de salidas largas
  y resumen de pasos viejos cuando crece demasiado.
- Del proyecto: `AGENTS.md` / `harness.yaml` del repo objetivo.
- Entre ejecuciones, en el repo objetivo:
  - `.harness/memory.md` (se sube a Git) + herramienta `save_memory(nota)`
  - `.harness/sessions/<TICKET>.json` para `harness resume` (no se sube)

## Convenciones de código

Base: PEP 8 (la guía de estilo oficial de Python), con estas reglas propias.

### Largo de línea

- Máximo **100 caracteres** por línea, incluidos comentarios y docstrings.
- Indentación de 4 espacios, nunca tabs.
- Si una línea se pasa de 100, partirla dentro de paréntesis, no con `\`.

### Estilo de nombres (en Python NO se usa camelCase)

| Qué | Estilo | Ejemplo |
|---|---|---|
| Variables | `snake_case` | `ruta_archivo`, `intentos_maximos` |
| Funciones / métodos | `snake_case` | `leer_archivo()`, `ejecutar_tests()` |
| Clases | `PascalCase` | `GestorSesion`, `ClienteJira` |
| Constantes | `MAYUSCULAS_CON_GUION` | `MAX_PASOS`, `TIMEOUT_SEGUNDOS` |
| Módulos (archivos .py) | `snake_case` corto | `herramientas.py`, `jira_client.py` |
| Paquetes (carpetas) | minúsculas, sin guiones | `herramientas/`, `proveedores/` |
| Privado / interno | prefijo `_` | `_recortar_salida()`, `_cache` |
| Booleanos | prefijo `es_`, `tiene_`, `hay_` | `es_valido`, `tiene_errores` |

### Idioma de los nombres

- Nombres **en español**: variables, funciones, métodos, clases, módulos,
  herramientas que se exponen al modelo, comentarios, docstrings y mensajes al
  usuario.
- Se permite mezclar con **términos técnicos en inglés** cuando no tienen un
  equivalente natural o son la convención de la industria, sobre todo como
  prefijo/sufijo de rol: `service`, `handler`, `provider`, `client`, `tool`,
  `config`, `prompt`, `token`, `test`, `log`.
  - Bien: `handle_cargar`, `carga_service`, `jira_client`, `claude_provider`,
    `ejecutar_tests`, `config_proyecto`, `TicketService`.
  - Mal: `loadFile` (camelCase), `ReadFileFromDisk` (todo en inglés),
    `leerArchivo` (camelCase), `la_funcion_que_lee_el_archivo` (demasiado largo).
- **Sin tildes ni ñ** en identificadores (Python las permite, pero dan
  problemas con teclados y herramientas): `configuracion`, `anio`, `tamano`.
  En comentarios, docstrings y textos al usuario sí se usan tildes y ñ.
- Lo que viene de librerías o APIs externas se queda como está
  (`client.messages.create`, `tool_calls`, campos JSON de Jira/GitHub).
- Los nombres en inglés que aparecen en `docs/plan-harness-ia.md` (`read_file`,
  `save_memory`, `LLMProvider`...) son referenciales; al implementarlos se
  aplican estas reglas: `leer_archivo`, `guardar_memoria`, `ProveedorLLM`, etc.

### Otras reglas

- **Type hints** en todas las funciones (parámetros y retorno):
  `def leer_archivo(ruta: str) -> str:`
- **Docstrings** en español en funciones públicas y clases; una línea si basta.
- Funciones cortas, con una sola responsabilidad. Si pasa de ~40 líneas,
  dividirla.
- Usar f-strings para formatear texto: `f"Ticket {clave} procesado"`.
- Usar `pathlib.Path` para rutas, no concatenar strings (el proyecto corre en
  Windows y Linux).
- Imports al inicio del archivo en tres bloques separados por una línea vacía:
  librería estándar, librerías externas, código propio.
- No usar `import *` ni `except:` vacío; capturar excepciones concretas.
- Nada de valores mágicos sueltos: a constantes con nombre.
- Comentarios para explicar el **por qué**, no el qué.

## Convenciones de trabajo

- Mensajes de commit cortos, de una sola línea (ej: `feat: bucle basico de tool calling`).
- Avanzar en el orden del roadmap: primero el bucle básico con herramientas de
  archivos, luego ejecución en Docker + tests (autocorrección), y después lo demás.
