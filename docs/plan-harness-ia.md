# Harness de IA para generación de código

## Objetivo

Construir un harness propio (aplicación de consola) que se conecta a un modelo de IA por API
(Claude, GPT, Gemini; **no** un modelo local) y le da herramientas para que, a partir de un
ticket de Jira muy especificado, haga todo solo: entender el proyecto, generar código,
ejecutar pruebas, autocorregirse si hay errores y terminar haciendo push a GitHub.

```
harness run PROJ-123
```

### Subcomandos

| Comando | Qué hace |
|---|---|
| `harness run <TICKET>` | Procesa un ticket desde cero (clave de Jira o ruta a un ticket local) |
| `harness resume <TICKET>` | Continúa una ejecución que se cortó |
| `harness init` | Genera `AGENTS.md` / `harness.yaml` en un repo objetivo |
| `harness config` | Guarda las credenciales la primera vez |
| `harness version` | Muestra la versión |

> Los nombres de herramientas y clases en inglés de este documento (`read_file`,
> `save_memory`, `LLMProvider`...) son referenciales. Al implementarlos se aplican las
> convenciones de `CLAUDE.md`: `leer_archivo`, `guardar_memoria`, `ProveedorLLM`, etc.

---

## Idea central: el bucle de herramientas (tool calling)

El modelo solo produce texto. **El harness es el que ejecuta las acciones.**

1. Se le manda al modelo: ticket + contexto del proyecto + lista de herramientas
   (nombre, descripción, parámetros en JSON Schema).
2. El modelo responde: "quiero llamar a la herramienta X con estos argumentos".
3. El harness ejecuta esa herramienta de verdad y le devuelve el resultado.
4. Se repite hasta que el modelo dice que terminó.

La **autocorrección** sale de este mismo bucle: si los tests fallan, el error se le devuelve
al modelo y en el siguiente turno lo corrige.

```python
messages = [system_prompt, ticket_y_contexto]
for paso in range(MAX_PASOS):
    resp = llm.chat(messages, tools=TOOLS)
    messages.append(resp)
    if not resp.tool_calls:
        break                                      # el modelo dice que terminó
    for call in resp.tool_calls:
        resultado = ejecutar(call.name, call.args)  # código real
        messages.append(tool_result(call.id, resultado))
```

---

## Herramientas a implementar

### Entrada

- **`get_jira_ticket(id)`**: API REST de Jira (`/rest/api/3/issue/{id}`). Obtiene resumen,
  descripción y criterios de aceptación. Al inicio, aceptar también un ticket local
  (`.json` / `.md`).

### Entender el proyecto

- **`list_dir`**, **`read_file`**, **`search_code`** (tipo grep).
- **Archivos de configuración dentro del repo objetivo** (en su raíz). No se le pasan al
  harness: el harness los busca solo al arrancar y los mete en el contexto inicial del
  modelo. Quedan versionados con Git junto al código.

  | Archivo | Para quién | Contenido |
  |---|---|---|
  | `harness.yaml` | El harness (lo ejecuta) | Imagen Docker, instalación, tests, lint, servicios (BD), variables de entorno, rama base, límites. **Especificación completa: [`docs/harness-yaml.md`](harness-yaml.md)** |
  | `AGENTS.md` | El modelo (lo lee) | Estructura de carpetas, arquitectura, convenciones de código |

  Si no existen:
  - el modelo explora con `list_dir` / `read_file` (más lento), o
  - `harness init` los genera analizando el proyecto, para revisarlos y subirlos al repo.

  `harness init [--repo] [--verificar]`:
  - Genera solo los que faltan y **nunca sobrescribe**. Si ya existen los dos, no llama al
    modelo.
  - El modelo explora con herramientas de lectura y guarda con `guardar_agents_md` y
    `guardar_harness_yaml`. La segunda valida el YAML con las mismas reglas que
    `harness run` antes de escribirlo; si no cumple, el error vuelve al modelo para que lo
    corrija, así que no puede quedar un `harness.yaml` inválido.
  - El prompt trae recomendaciones por stack: en Maven/Gradle, `limites` más amplios
    (timeout 600 s, 50 pasos, 20 de plan, 6000 caracteres de salida) y tests con `clean` y la
    salida redirigida a archivos; en Python chico, los valores por defecto; en Spring Boot,
    las variables `SPRING_DATASOURCE_*`; y los tests de arquitectura en
    `archivos_protegidos`. Usa como ejemplo el `harness.yaml` de tienda-api.
  - Al terminar recuerda revisarlos: `harness.yaml` define qué se ejecuta (`instalar`,
    `comandos_permitidos`).
  - `--verificar`: levanta el entorno con el `harness.yaml` (servicios incluidos), instala y
    corre los tests, e informa si funcionan (código 1 si no). Solo informa: no corrige el YAML.
  - `harness run` no llama a `init` por su cuenta: si falta algún archivo, avisa y lo sugiere.
    Un `harness.yaml` tiene que verlo una persona antes del primer uso.

### Escribir código

- **`write_file`**
- **`edit_file`**: reemplaza un fragmento exacto, no reescribe todo el archivo.

### Ejecutar y validar (aquí está la autocorrección)

- **`run_command`**: **siempre** dentro de un contenedor Docker con el repo montado, con
  timeout y lista de comandos permitidos.
- **`run_tests`**, **`run_lint`**: devuelven la salida recortada (últimas N líneas del error).
- Si el proyecto necesita base de datos u otro servicio, el harness lo levanta en su propio
  contenedor (ver [Entorno de ejecución en Docker](#entorno-de-ejecución-en-docker)).

### Entrega

- **`git_create_branch`**, **`git_commit`**, **`git_push`**
- *(opcional)* **`create_pull_request`**: API de GitHub.
- *(opcional)* **`comment_jira`**: deja el link del PR en el ticket.

---

## Flujo completo

```
harness run PROJ-123
 ├─ 1. Leer ticket de Jira
 ├─ 2. Repo objetivo = carpeta actual (o --repo <ruta>) + leer AGENTS.md / harness.yaml
 ├─ 3. Planificación: el modelo propone un plan (archivos a tocar, pasos)
 ├─ 4. Implementación: bucle de herramientas
 ├─ 5. Validación: tests + lint ── si fallan → volver a 4 (máx. N intentos)
 ├─ 6. Commit en rama feature/PROJ-123 + push
 └─ 7. PR + comentario en Jira (opcional)
```

### Planificación (paso 3)

- El harness carga `AGENTS.md` en el system prompt (recortado a 12 000 caracteres): el modelo
  no gasta un paso en leerlo.
- En la ronda de plan el modelo solo tiene herramientas de lectura (`leer_archivo`,
  `listar_directorio`) y la herramienta `proponer_plan(resumen, archivos, pasos, tests)`,
  donde cada archivo es `{ruta, accion: crear|modificar|eliminar, motivo}`. La ronda termina
  cuando la llama con un plan válido; si el plan no cumple el formato, el error vuelve al
  modelo para que lo corrija.
- Tiene hasta `limites.max_pasos_plan` pasos (10 sin la clave), descontados de `max_pasos` y
  nunca más de la mitad de ellos, para que quede al menos la mitad para implementar.
  Si responde con texto o se le acaban, se le pide "llama a `proponer_plan` ahora", ya sin
  herramientas de lectura; si aun así no hay plan, la ejecución se corta con error.
- El pedido de plan le dice cuántos pasos tiene y que puede pedir varias lecturas en una misma
  respuesta (cada resultado de `leer_archivo` empieza con `Archivo: <ruta>`). Cuando le quedan
  3 pasos o menos, el resultado de la última herramienta del paso termina con "Te quedan N
  pasos para planificar: llama a proponer_plan pronto." (dentro del resultado, no como
  mensaje aparte, porque algunos proveedores no aceptan ese orden de mensajes).
- El plan se muestra en un panel (armado desde la estructura, no desde texto libre) y queda
  en el log como evento `plan`. Después el modelo implementa con todas las herramientas.
- Al terminar, el harness compara el plan con los archivos que de verdad cambiaron y avisa
  en amarillo de los que no estaban en el plan. Si el repo es Git, esa foto de cambios solo
  mira los archivos que Git no ignora (`git ls-files --cached --others --exclude-standard`),
  así las salidas de compilación de cada stack quedan fuera según su `.gitignore`; si no es
  Git, se usa una lista fija de carpetas (más `target`, `build`, `dist`, `out`, `.gradle` y
  `bin`, la salida de algunos IDEs).
- `--aprobar-plan`: muestra el plan y pregunta "¿Implementar? [s/n]"; con "n" termina sin
  implementar (código 0). Por defecto el harness sigue solo.
- `--sin-plan`: salta la ronda de plan (tickets triviales o para ahorrar pedidos).

---

## Entorno de ejecución en Docker

Todo el código del proyecto objetivo (instalación, tests, comandos del modelo) corre en
contenedores que crea el harness. **Decisión tomada:** si el proyecto necesita base de datos
(PostgreSQL, MySQL, Redis...), el harness levanta un **contenedor propio para ese servicio**.
Nunca se conecta a bases de datos instaladas en la máquina (`host.docker.internal`).

### Dónde corre cada cosa

```
Tu PC
├── harness  (Python normal, en su .venv; NO está en Docker)
│     ├── habla con la API del modelo por internet
│     ├── edita los archivos del proyecto objetivo en tu disco
│     └── ejecuta comandos "docker ..." cuando necesita correr algo
│
└── Docker
      ├── contenedor temporal: imagen del proyecto + repo montado (-v)
      └── contenedor temporal: postgres:16   (solo si hay "servicios")
```

- El repo se **monta** en el contenedor (`-v <repo>:/app`): cuando el modelo edita un archivo
  en el disco, el contenedor ve el cambio al instante.
- El harness **no usa el `Dockerfile` ni el `docker-compose.yml` del proyecto**. Esos sirven
  para correr la aplicación de verdad (puertos, base real, despliegue); el harness solo
  necesita un lugar aislado para instalar dependencias y correr tests.
- El harness **no construye imágenes** (`docker build`): usa imágenes ya hechas de Docker Hub
  (`python:3.12-slim`, `postgres:16`) con `docker run`. Docker las descarga la primera vez.
- Lo de `servicios` hace lo mismo que un docker-compose, pero lo arma el harness desde Python
  y es temporal: solo para tests, sin puertos expuestos, todo se borra al terminar.

### Por qué servicios propios

- **Nunca toca datos reales:** cada ejecución usa una base nueva y la destruye al terminar.
- **Tests confiables:** la base empieza siempre vacía. Con una base compartida pueden quedar
  datos de ejecuciones anteriores que hagan fallar tests sin motivo, y eso confunde a la
  autocorrección (el modelo intentaría "arreglar" código que estaba bien).
- **Funciona en cualquier máquina:** solo hace falta Docker, no PostgreSQL instalado.
- **Sin secretos:** la contraseña del contenedor es de prueba, puede ir en `harness.yaml`.
- **Sin configurar nada en la PC:** ni `listen_addresses` ni `pg_hba.conf`.

### Secuencia que ejecuta el harness

```
AL EMPEZAR LA EJECUCIÓN (una sola vez)
1. docker network create harness-PROJ-123              ← red privada por ejecución
2. docker run -d --network harness-PROJ-123 --network-alias db \
     --name harness-PROJ-123-db -e POSTGRES_PASSWORD -e POSTGRES_DB postgres:16
3. esperar a que "pg_isready" responda                 ← Postgres tarda unos segundos
4. docker run -d --network harness-PROJ-123 --name proyecto \
     -e DATABASE_URL \
     -v <repo>:/app -w /app <imagen> sleep infinity    ← queda vivo toda la ejecución
5. docker exec proyecto <docker.instalar>              ← dependencias UNA sola vez

CADA VEZ QUE EL MODELO CORRE TESTS O UN COMANDO
6. docker exec proyecto <comando>                      ← mismo contenedor, sin reinstalar

AL TERMINAR (siempre, en un try/finally)
7. borrar contenedores y red
```

- Sin servicios (como tareas-api) se saltan los pasos 1 a 3.
- Dentro de la red, el contenedor del proyecto llega a la base usando el nombre del servicio
  (`db`) como dirección.
- Los valores de las variables (`-e NOMBRE`) van en el entorno del proceso `docker`, no en la
  línea de comandos.
- Un solo contenedor vivo + `docker exec` evita reinstalar dependencias en cada `pytest`,
  que sería muy lento.

### En `harness.yaml`

Fragmento con lo relacionado a Docker. Todas las claves, tipos, valores por defecto y reglas de
validación están en [`docs/harness-yaml.md`](harness-yaml.md).

```yaml
docker:
  imagen: python:3.11-slim
  instalar: pip install -r requirements.txt -r requirements-dev.txt

servicios:                          # opcional: solo si el proyecto necesita BD u otros
  - nombre: db
    imagen: postgres:16
    variables:
      POSTGRES_PASSWORD: test
      POSTGRES_DB: app_test
    listo_cuando: pg_isready -h 127.0.0.1 -U postgres

variables_entorno:                  # se pasan al contenedor del proyecto
  DATABASE_URL: postgresql://postgres:test@db:5432/app_test
  SECRET_KEY: clave-de-prueba
```

- `harness.yaml` se sube a Git: solo valores de prueba. Si algún valor fuera secreto de
  verdad, se escribe como `${NOMBRE_VARIABLE}` y el harness lo toma de la máquina del usuario.
- El harness no exige `pyproject.toml` ni `uv` en el proyecto objetivo: se adapta a lo que
  use (`pip` + `requirements.txt`, `uv`, Maven, npm...) mediante `docker.instalar`.

### Diseño del código

- Una clase (por ejemplo `EntornoDocker`) que maneja la red, la lista de contenedores
  (proyecto + servicios) y la limpieza.
- Desde la fase 2 se escribe pensando en varios contenedores, aunque al inicio solo haya uno:
  agregar servicios después es sumar un contenedor a la lista, no reescribir.
- El paso 7 (limpieza) va en `try/finally` para no dejar contenedores colgados si la
  ejecución falla o se corta con Ctrl+C.

---

## Decisiones técnicas

- **Lenguaje:** Python, con los SDK oficiales de Anthropic y OpenAI. Consola con `typer` y
  `rich`. Entorno y dependencias con `uv` (ver
  [Entorno, dependencias y distribución](#entorno-dependencias-y-distribución)).
- **Sin LangChain ni frameworks de agentes:** el bucle lo hacemos nosotros; ese es el punto
  del proyecto.
- **Agnóstico al modelo:** interfaz `LLMProvider` desde la fase 1 (ver
  [Proveedores de modelo](#proveedores-de-modelo)).
- **Seguridad:**
  - push solo a ramas, **nunca** a `main`
  - ejecución de código solo en Docker
  - bases de datos solo en contenedores propios del harness, nunca las de la máquina
  - límite de pasos y de tokens
  - credenciales de GitHub / Jira / API del modelo fuera del código (variable de entorno o
    `~/.harness/config.toml`)
- **Logs:** guardar cada paso (herramienta llamada + resultado) en un archivo por ejecución.
  Sirve para depurar y para la presentación.

---

## Proveedores de modelo

### La interfaz `LLMProvider` (`ProveedorLLM` en el código)

El bucle **nunca** habla directo con un SDK. Habla con una interfaz propia, y cada proveedor la
implementa traduciendo a su formato.

```python
@dataclass
class LlamadaHerramienta:
    id: str
    nombre: str
    argumentos: dict


@dataclass
class RespuestaModelo:
    texto: str | None
    llamadas: list[LlamadaHerramienta]   # vacía = el modelo terminó
    tokens_entrada: int
    tokens_salida: int


class ProveedorLLM(Protocol):
    def responder(
        self, mensajes: list[Mensaje], herramientas: list[Herramienta]
    ) -> RespuestaModelo: ...
```

- El bucle, la memoria y `harness resume` trabajan solo con estos tipos propios (`Mensaje`,
  `Herramienta`, `LlamadaHerramienta`, `RespuestaModelo`). Así las sesiones guardadas no
  dependen del proveedor, y cambiar de modelo no toca el bucle.
- Se hace **en la fase 1**, no al final: con APIs gratuitas se acaban las cuotas y hay que
  poder cambiar de proveedor desde el primer día.

### El usuario elige el proveedor

**El harness no está atado a ningún proveedor.** Cada usuario elige cuál usar (y paga o no
según el que elija) en su configuración. Por dentro hay una clase por **formato de API**, no
por empresa:

| Formato | Proveedores que lo usan | Clase interna | Fase |
|---|---|---|---|
| OpenAI | Gemini, Groq, OpenRouter, OpenAI, DeepSeek, Mistral... | `ProveedorOpenAICompatible` (SDK `openai` + `base_url`) | 1 |
| Anthropic | Claude | `ProveedorAnthropic` (SDK `anthropic`) | 8 |

### Proveedores conocidos (presets)

Para que el usuario no tenga que saber de URLs ni formatos, el harness trae una tabla de
proveedores conocidos. Cada uno define el formato, la `base_url` y la variable de la key:

| `proveedor` | Formato | `base_url` | Variable de la API key |
|---|---|---|---|
| `gemini` | OpenAI | `https://generativelanguage.googleapis.com/v1beta/openai/` | `GEMINI_API_KEY` |
| `groq` | OpenAI | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` |
| `openrouter` | OpenAI | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` |
| `openai` | OpenAI | (la del SDK) | `OPENAI_API_KEY` |
| `anthropic` | Anthropic | (la del SDK) | `ANTHROPIC_API_KEY` |
| `personalizado` | OpenAI | la escribe el usuario | la escribe el usuario |

Una función `crear_proveedor(config) -> ProveedorLLM` mira el preset y crea la clase que
corresponde. Agregar un proveedor nuevo con formato OpenAI es agregar una fila a esa tabla.

### Configuración (por usuario, no por proyecto)

Nunca en `harness.yaml`. Hay dos lugares, según el momento:

| Archivo | Cuándo | Dónde está |
|---|---|---|
| `.env` | **Mientras se desarrolla el harness** (fases 1 a 7) | Raíz del repo del harness (en `.gitignore`) |
| `config.toml` | Cuando otros lo instalan con `uv tool install` (no tienen el repo ni el `.env`) | `C:\Users\<usuario>\.harness\config.toml` / `~/.harness/config.toml` |

**`.env` en desarrollo:**

```
HARNESS_PROVEEDOR=gemini
HARNESS_MODELO=<id del modelo, ver en AI Studio>
GEMINI_API_KEY=tu_key
```

**`~/.harness/config.toml`** (lo crea `harness config`, no se escribe a mano):

```toml
[modelo]
proveedor = "gemini"
nombre = "<id del modelo, ver en AI Studio>"

# Solo para proveedor = "personalizado":
# base_url = "https://..."
# api_key_env = "MI_API_KEY"

[credenciales]
GEMINI_API_KEY = "..."
GITHUB_TOKEN = "..."
JIRA_TOKEN = "..."
```

- `harness config` pregunta el proveedor (de la lista de presets), el modelo y las keys, y
  escribe el archivo. Se lee con `tomllib` (viene con Python, no hay que instalar nada).
- El archivo está en la carpeta del usuario, fuera de todo repo: nunca llega a Git.

**Orden de prioridad** (el primero que tenga el valor gana):

1. Opciones del comando: `--proveedor`, `--modelo`.
2. Variables de entorno / `.env`.
3. `~/.harness/config.toml`.
4. Si no hay nada: error con el mensaje "ejecuta `harness config`".

Para probar otro proveedor o modelo en una ejecución, sin tocar la configuración:

```powershell
harness run TAREAS-1 --proveedor groq --modelo openai/gpt-oss-120b
```

### Modelos gratuitos para desarrollar (verificado en octubre 2026)

| Proveedor | `base_url` | Cuota gratis aprox. | Ojo |
|---|---|---|---|
| **Google Gemini** (AI Studio) — **principal** | `https://generativelanguage.googleapis.com/v1beta/openai/` | Flash-Lite ~500 pedidos/día; Flash ~20/día (reportes no oficiales; ver límites en AI Studio) | En el plan gratis Google puede usar los datos para mejorar sus productos: no mandar código privado ni secretos |
| **Groq** — respaldo | `https://api.groq.com/openai/v1` | gpt-oss-120b / qwen: 30 pedidos/min, 1.000/día, **8.000 tokens/min**, 200.000 tokens/día | Muy rápido, pero 8K tokens/min obliga a mantener el contexto chico |
| **OpenRouter** — emergencia | `https://openrouter.ai/api/v1` | ~50 pedidos/día en modelos `:free` | Muy poco para un agente; los modelos gratis cambian sin aviso |

- Cada paso del bucle es un pedido: un ticket chico gasta entre 10 y 30.
- Desarrollar con el modelo gratuito más barato; los modelos fuertes solo para la demo final.
- El proveedor debe **reintentar con espera** (backoff) cuando la API responde 429 (límite
  alcanzado), en lugar de cortar la ejecución.
- Los modelos chicos fallan más con tool calling (JSON mal armado, herramientas inexistentes):
  el bucle debe devolverle el error al modelo, no romperse.

---

## Memoria

El modelo por sí solo **no recuerda nada**: cada llamada a la API empieza de cero. Toda la
memoria la pone el harness. Hay tres niveles.

### 1. Dentro de una ejecución (obligatoria)

- Es la lista `messages` del bucle: ticket, herramientas llamadas, resultados y errores.
  Gracias a ella se autocorrige.
- Manejo del contexto, porque la lista crece rápido:
  - recortar salidas largas (archivos, tests, lint);
  - si pasa de cierto tamaño, resumir los pasos viejos y reemplazarlos por el resumen.

### 2. Del proyecto (obligatoria)

- `AGENTS.md` / `harness.yaml` en la raíz del repo objetivo.
- Conocimiento fijo que se carga al inicio de cada ejecución.

### 3. Entre ejecuciones (lo que sí vamos a hacer)

**a) Archivo de memoria en el repo objetivo: `.harness/memory.md`**

- Herramienta `save_memory(nota)` para que el modelo guarde lo que aprende
  (ej: *"los tests necesitan la variable DB_URL"*).
- Se carga en el contexto inicial de la siguiente ejecución.
- Queda en Git; se puede revisar y corregir a mano.

**b) Reanudar ejecuciones: `harness resume PROJ-123`**

- Después de cada paso se guarda la lista `messages` + el estado (fase actual, intentos,
  rama) en un archivo por ticket: `.harness/sessions/PROJ-123.json`.
- Si la ejecución se corta (error, Ctrl+C, límite de tokens, sin internet), `resume` carga
  ese archivo y continúa desde el último paso en vez de empezar de cero.
- Al terminar con éxito se puede borrar o marcar como completada.
- `.harness/sessions/` va al `.gitignore` (no se sube).

> **No se hará:** búsqueda semántica con embeddings / base vectorial (es casi otro proyecto).

---

## Entorno, dependencias y distribución

**Herramienta elegida: `uv`** (en vez de `venv` + `pip`).

### Por qué uv

- Crea el `pyproject.toml` solo (necesario para que el harness sea instalable).
- `uv.lock`: todo el grupo tiene exactamente las mismas versiones.
- `uv tool install`: comando global sin necesitar `pipx` aparte.
- Descarga y administra versiones de Python por su cuenta.
- Mucho más rápido que `pip`.

### Sin riesgo

- El `.venv` que crea `uv` es un entorno virtual normal; se puede seguir usando `pip` dentro
  si hace falta (o `uv pip install ...`).
- `pyproject.toml` es estándar de Python: el harness se instala igual con `pip`, `pipx` o
  `uv`.
- Solo usaría `venv` + `pip` si el docente lo exige.

### Aislamiento

- **Librerías:** cada proyecto tiene su propio `.venv`; nunca van al Python global.
- **Versión de Python:** desarrollo con **3.14**; `requires-python = ">=3.12"` para que otros
  puedan instalarlo. Si falta una versión, `uv` la descarga en su propia carpeta sin tocar el
  Python del sistema. Queda anotada en `.python-version`.
- **Comandos globales** (`uv tool install`): cada uno con su entorno aislado.
- El código que **genera** el harness se ejecuta en Docker, no en el `.venv`.

### Comandos del día a día

```powershell
uv init --package --python 3.14 harness   # UNA vez. --package es obligatorio para que sea
                                          # instalable como comando
uv add <libreria>                         # en vez de pip install
uv run harness ...                        # ejecutar dentro del entorno sin activarlo
uv sync                                   # los compañeros, después de clonar
```

Dependencias iniciales:

```powershell
uv add typer rich anthropic openai python-dotenv
```

Comando de consola, en `pyproject.toml`:

```toml
[project.scripts]
harness = "harness.cli:main"
```

### Instalación global

En mi máquina, mientras desarrollo (editable: los cambios se reflejan al instante):

```powershell
uv tool install -e .
```

Que otros lo instalen desde GitHub (repo público):

```powershell
uv tool install git+https://github.com/GersonPoma/harness
# o con pip:
pipx install git+https://github.com/GersonPoma/harness
```

Versiones estables con tags:

```powershell
git tag v0.1.0
git push origin v0.1.0
uv tool install git+https://github.com/GersonPoma/harness@v0.1.0
```

### Para que funcione en cualquier máquina

- **Credenciales por usuario:** variable de entorno o archivo global `~/.harness/config.toml`
  (`harness config` pide la API key la primera vez), no un `.env` dentro del proyecto.
- **Requisitos:** Docker y git. El harness lo comprueba al arrancar y avisa si falta algo.
  No hace falta tener PostgreSQL ni otras bases instaladas: van en contenedores.
- `.venv/`, `.env`, `.harness/sessions/` y `.harness/logs/` van al `.gitignore`.
- El nombre `harness` probablemente está ocupado en PyPI; si algún día se publica ahí, el
  paquete debe llamarse distinto (el comando puede seguir siendo `harness`).

---

## Orden de construcción (roadmap)

- [x] **1.** Interfaz `ProveedorLLM` + `ProveedorOpenAICompatible` con presets (gemini, groq,
      openrouter, openai, personalizado) + bucle básico con `read_file`, `write_file`,
      `list_dir` sobre tareas-api. Configuración leída solo del `.env`
- [x] **2.** Carga y validación de `harness.yaml` (todas las claves, según
      `docs/harness-yaml.md`) + `run_command` en Docker + tests + lint (autocorrección
      funcionando). Un solo contenedor, probado con tareas-api; la clase `EntornoDocker` ya
      pensada para varios. `servicios` y `variables_entorno` se validaban, pero si traían algo
      la ejecución se detenía hasta la fase 3. Sin `harness.yaml`, el harness avisa y trabaja
      como en la fase 1 (sin Docker ni validación)
- [x] **3.** Servicios en Docker: `servicios` y `variables_entorno` del `harness.yaml`, red
      privada, espera de `listo_cuando` y limpieza. Probado con notas-api (PostgreSQL)
- [ ] **4.** Lectura automática de `AGENTS.md` al contexto inicial (+ `harness init` si no
      existen `AGENTS.md` / `harness.yaml`) + fase de planificación. (La lectura de
      `harness.yaml` se adelantó a la fase 2 porque Docker y los tests dependen de ella)
- [ ] **5.** Git: rama, commit, push. **No se hace commit ni push de código que no pasó la
      validación** (tests + lint), ni de ejecuciones sin `harness.yaml` (no hubo validación)
- [ ] **6.** Integración real con Jira (reemplaza el ticket local)
- [ ] **7.** Memoria: manejo de contexto (recorte + resumen), `.harness/memory.md` con
      `save_memory`, y `harness resume`
- [ ] **8.** Pulido: PR automático, comentario en Jira, logs bonitos, `ProveedorAnthropic`
      (preset `anthropic`, para quien elija Claude), `harness config` +
      `~/.harness/config.toml` con el orden de prioridad (necesario para que otros lo instalen
      y usen). `harness init --verificar` con bucle de corrección: si la instalación o los
      tests fallan, devolverle el error al modelo para que corrija el `harness.yaml`

> Con 1 y 2 ya hay algo funcional y demostrable; lo demás se suma encima.

### Pendientes conocidos

- **Archivos con dueño root en Linux:** el contenedor corre como root, así que lo que crea en
  el repo montado (`.pytest_cache/`, `*.egg-info/`, archivos que escribe un comando del modelo)
  queda con dueño root en la máquina del usuario. En Windows/macOS con Docker Desktop no pasa.
  Se resolvería con `docker run --user <uid>:<gid>` en Linux (ojo: `docker.instalar` con
  `pip install` necesita entonces un entorno virtual o `--user`, porque no podrá escribir en
  el Python del sistema de la imagen).
- **Imágenes sin `timeout`:** el límite de tiempo de cada comando usa `timeout -s KILL` dentro
  del contenedor. Las imágenes basadas en Debian o Alpine lo traen; una imagen mínima
  (distroless) no, y sus comandos fallarían con "timeout: not found".

### Proyectos de prueba

- **tareas-api** (FastAPI, sin base de datos): fases 1, 2, 4 y 5. Trae `AGENTS.md`,
  `harness.yaml` y tickets `TAREAS-1` a `TAREAS-5`.
- **notas-api** (FastAPI + SQLAlchemy + PostgreSQL, con `pip`): fase 3. Trae `AGENTS.md`,
  `harness.yaml` con un servicio `db` y tickets `NOTAS-1` a `NOTAS-4`.
- **Un proyecto en otro lenguaje** (Spring Boot o Node): para mostrar en la defensa que el
  harness es genérico, cambiando solo `AGENTS.md` y `harness.yaml`.
