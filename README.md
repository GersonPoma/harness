# harness

Harness de IA por consola para generar código a partir de un ticket. Recibe un ticket de Jira
(o un `.md` local), entiende el proyecto, planifica, implementa con un modelo de IA por API,
corre los tests en Docker, se autocorrige si fallan, revisa el diff y termina con commit, push
a una rama y un pull request en borrador.

El bucle de herramientas está hecho a mano (sin frameworks de agentes) y no está atado a
ningún proveedor: Gemini, Groq, OpenRouter, OpenAI, Anthropic o cualquier API compatible con
OpenAI.

- [Empezar desde cero](#empezar-desde-cero)
- [Comandos](#comandos)
- [Configuración](#configuración)
- [Qué deja el harness en tu proyecto](#qué-deja-el-harness-en-tu-proyecto)
- [Problemas comunes](#problemas-comunes)
- [Seguridad](#seguridad)
- [Desarrollo](#desarrollo)

## Empezar desde cero

### 1. Instalar uv, Git y Docker Desktop

- **uv** (instala también Python 3.12 o más nuevo si no lo tienes):

  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

  Cierra y vuelve a abrir la terminal para que encuentre el comando `uv`.
- **Git**, con tu nombre y email configurados (el harness hace el commit con ellos) y con
  permiso para hacer push al repo de tu proyecto:

  ```powershell
  git config --global user.name "Tu Nombre"
  git config --global user.email "tu@correo.com"
  ```

- **Docker Desktop** (en Windows y macOS). Todo el código que genera el modelo se ejecuta solo
  dentro de contenedores. Apaga **Resource Saver** (Docker Desktop > Settings > Resources >
  Resource Saver): con Docker en pausa la instalación y los tests tardan mucho más, y el
  harness lo avisa cuando Docker tarda en responder.

### 2. Instalar el harness

```powershell
uv tool install git+https://github.com/GersonPoma/harness
harness version
```

Para instalar exactamente la versión presentada:

```powershell
uv tool install git+https://github.com/GersonPoma/harness@v1.0-defensa
```

### 3. Configurar el modelo y las credenciales: `harness config`

```powershell
harness config
```

Es un asistente: pregunta en este orden y guarda todo en `~/.harness/config.toml` (en Windows,
`C:\Users\<usuario>\.harness\config.toml`), fuera de cualquier repo.

| Pregunta | Qué responder |
|---|---|
| `Proveedor (gemini, groq, openrouter, openai, anthropic, personalizado) [gemini]` | El proveedor de tu API key. Enter deja el que está entre corchetes. |
| `Modelo` | El id exacto del modelo en ese proveedor (por ejemplo, el que muestra AI Studio para Gemini). El modelo tiene que soportar tool calling. |
| `URL base (https://...)` y `Variable de la API key` | Solo con `personalizado`: la URL de una API compatible con OpenAI y el nombre de la variable que guarda su key (`MI_API_KEY`). |
| `GEMINI_API_KEY (no se muestra al escribir)` | La API key del proveedor (la variable cambia según el proveedor: `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`). Lo que escribes no se ve. |
| `Nombre del perfil [gemini]` | Un nombre corto para esta combinación de proveedor y modelo (minúsculas, números, `-` y `_`). Queda como perfil predeterminado. |
| `¿Guardar el token de GitHub para abrir el PR? [s/N]` | `s` para pegar el token fine-grained (ver abajo). Sin él, el harness hace commit y push, pero no el PR. |
| `¿Configurar Jira? [s/N]` | `s` si vas a usar tickets de Jira Cloud. Pregunta la `URL del sitio`, el `Email de tu cuenta de Jira` y `JIRA_API_TOKEN`. Con tickets `.md` locales no hace falta. |
| `¿Crear ~/.harness/mcp.json a partir del ejemplo? [s/N]` | `s` si quieres que el PR y el comentario de Jira vayan por los servidores MCP (necesitan Docker). Con `N` todo va por la API REST, que alcanza. Ver [mcp.json](#mcpjson). |

Si `config.toml` ya existe, cada pregunta muestra el valor actual y Enter lo deja
(`GEMINI_API_KEY (guardada: ****x9Q2, Enter para dejarla)`). Al final:

```
✓ Guardado. Perfil predeterminado: gemini (gemini / <modelo>).
  Comprueba la key con: harness perfil probar gemini
```

Sin terminal interactiva (en un script), usa `harness perfil agregar`, `harness config
--credencial NOMBRE` (el valor se lee de stdin) y `harness config --importar-env`.

#### Token de GitHub (fine-grained)

1. GitHub > Settings > Developer settings > Personal access tokens > **Fine-grained tokens** >
   Generate new token.
2. **Repository access:** Only select repositories, y elige solo los repos donde vas a correr
   el harness.
3. **Permissions** (Repository permissions):
   - **Contents: Read-only**
   - **Pull requests: Read and write**
   - Metadata: Read-only (GitHub lo agrega solo).
4. Guárdalo con `harness config` o con `harness config --credencial GITHUB_TOKEN`.

El token solo se usa para el pull request (y para las herramientas de lectura de GitHub que se
le dan al modelo por MCP). El push usa tus credenciales de Git, no el token.

#### API token de Atlassian (Jira Cloud)

1. Entra a <https://id.atlassian.com/manage-profile/security/api-tokens> con tu cuenta de
   Jira y crea un **API token** (el clásico).
2. En `harness config`, responde `s` a `¿Configurar Jira?`:
   - **URL del sitio** (`JIRA_URL`): la dirección de tu Jira, `https://<sitio>.atlassian.net`.
   - **Email** (`JIRA_EMAIL`): el email con el que entras a Jira (el usuario dueño del token).
   - **`JIRA_API_TOKEN`**: el token del paso 1.

La URL y el email no son secretos: van en la sección `[jira]` de `config.toml`. El token va en
`[credenciales]`.

### 4. Comprobar el modelo: `harness perfil probar`

```powershell
harness perfil probar
```

Hace un pedido real al modelo del perfil predeterminado (o al perfil que nombres) con una
herramienta de prueba que el modelo está obligado a llamar. Comprueba dos cosas: que la API
key funciona y que el modelo sabe llamar herramientas (el harness lo necesita). Si sale bien:

```
✓ gemini: la key funciona y el modelo llamó a la herramienta (1 s, 92 tokens).
```

Si la key no sirve, el modelo no existe o el proveedor falla, muestra el error y termina con
código 1. Si el modelo responde sin llamar a la herramienta, avisa que puede no soportar tool
calling.

### 5. Preparar el proyecto: `harness init --verificar`

```powershell
cd C:\ruta\a\tu-proyecto
harness init --verificar
```

- **Qué genera:** el modelo explora el repo (README, dependencias, carpetas, código y tests) y
  escribe en la raíz los archivos que falten:
  - `AGENTS.md`: lo que el modelo tiene que saber del proyecto (stack, estructura de carpetas,
    arquitectura, convenciones, cómo se escriben y corren los tests, lo que no se debe hacer).
  - `harness.yaml`: lo que el harness necesita para correr el proyecto en Docker (imagen,
    instalación, comando de tests y de lint, servicios, límites).
  - Agrega `.harness/logs/` y `.harness/sessions/` al `.gitignore` (o lo crea).
  Si los dos archivos ya existen, no genera nada.
- **`--verificar`:** después levanta el entorno con ese `harness.yaml`, instala las
  dependencias y corre los tests en Docker. Si un `harness.yaml` recién generado falla, le
  devuelve el error al modelo para que lo corrija (hasta 2 veces).
- **Revisa a mano** antes de usarlo:
  - En `AGENTS.md`: que la arquitectura y las convenciones sean las reales, y agrega las
    reglas que el modelo no puede deducir (qué no tocar, cómo nombrar las cosas). Es lo que el
    modelo sigue en cada ticket.
  - En `harness.yaml`: la imagen y su versión, que `docker.instalar` instale también las
    dependencias de desarrollo, que `comandos.tests` sea el comando con el que corres los
    tests, `comandos_permitidos` (sin instaladores ni shells) y los `servicios` si el
    proyecto necesita base de datos.
- **Commitea los dos archivos** (y el `.gitignore`) antes de correr un ticket: `harness run`
  exige el árbol de trabajo limpio.

```powershell
git add AGENTS.md harness.yaml .gitignore
git commit -m "chore: configuracion del harness"
git push
```

### 6. Ver el ticket y procesarlo

```powershell
harness ticket TAREAS-1          # o: harness ticket tickets\TAREAS-1.md
harness run TAREAS-1 --aprobar-plan
```

`harness ticket` muestra el ticket tal como lo va a recibir el modelo, sin ejecutar nada: sirve
para comprobar que el harness lee bien Jira (o el `.md`) antes de gastar pedidos. Un ticket
local es un `.md` cualquiera: su nombre de archivo es la clave (`TAREAS-1.md` → `TAREAS-1`) y
su primera línea, el título.

Lo que vas a ver durante `harness run`, en orden:

1. **Inicio:** el ticket, el repo y el modelo con su origen (`Perfil gemini: gemini / <modelo>
   (predeterminado)`), el respaldo y el revisor si los hay. Después, de dónde vino el ticket
   (Jira, con su link, o el archivo local), si cargó `AGENTS.md` y la memoria del proyecto.
2. **Verificación inicial,** antes de gastar un solo pedido al modelo:
   - que Docker esté corriendo;
   - que el token de GitHub vea el repo y sus pull requests (`✓ Token de GitHub con acceso a
     ...`); si no sirve, pregunta `¿Continuar sin PR (solo commit y push)? [s/N]`;
   - si el PR y el comentario de Jira van por MCP o por la API REST;
   - Git: árbol limpio, la rama base, que la rama del ticket no exista; y crea la rama
     (`Rama feature/TAREAS-1 creada desde main.`).
3. **Preparación de Docker:** descarga las imágenes que falten, levanta los servicios, crea el
   contenedor e instala las dependencias (`Dependencias instaladas en ...`).
4. **Exploración y plan:** el modelo lee el proyecto con herramientas de solo lectura. Cada
   paso se ve como `[N] herramienta(argumentos)` con su resultado debajo (`→ ...`). Termina
   con el panel **Plan**: resumen, archivos (`+` crear, `~` modificar, `-` eliminar), pasos y
   tests.
5. **`¿Aprobar el plan? [s/n, o escribe qué cambiar]`** (solo con `--aprobar-plan`; sin esa
   opción el plan se aprueba solo):
   - `s`: se implementa.
   - `n`, Enter o sin respuesta: se termina sin implementar nada.
   - Cualquier otro texto es una corrección (`Que también valide el título vacío`): el modelo
     vuelve a planificar con ese pedido y la pregunta se repite.
6. **Implementación:** más pasos `[N] ...` con `escribir_archivo`, `editar_archivo`,
   `ejecutar_tests`... y la respuesta final del modelo (`[N] Respuesta: ...`).
7. **Validación en Docker:** corre los tests (y el lint, si hay):
   `✓ Validación 1: tests OK (8 passed in 0.18s)`. Si fallan, el modelo recibe el error y
   vuelve a implementar (hasta `limites.max_intentos_validacion` veces).
8. **Revisión:** con los tests en verde, el modelo (o el revisor de `--revisor`) revisa el
   diff contra `AGENTS.md` y el ticket. Si encuentra problemas, se corrigen, se valida y se
   revisa de nuevo.
9. **Resultado:** el panel con el resumen del modelo y los archivos que cambiaron.
10. **Entrega:** `✓ Commit <hash> en feature/TAREAS-1`, `✓ Push de feature/TAREAS-1 a origin`
    y el link del **pull request en borrador**.
11. **Jira** (solo con un ticket de Jira y PR abierto): `✓ Comentario en TAREAS-1` con el link
    del PR y, si `harness.yaml` tiene `jira.estado_al_abrir_pr`, `✓ TAREAS-1 pasó a En
    revisión`.
12. **Tiempos:** `Tiempos: preparación 14 s · plan 38 s · esperando al usuario 1 min 5 s ·
    implementación 2 min 10 s · validación 9 s · revisión 21 s · entrega 6 s` y la ruta del
    resumen `.md` de la ejecución.

Si algo corta la ejecución (límite de pasos, el proveedor no responde, Ctrl+C), queda una
sesión: se sigue con `harness resume TAREAS-1`.

### 7. Deshacer una corrida

```powershell
git switch main
git branch -D feature/TAREAS-1                 # la rama local
git push origin --delete feature/TAREAS-1      # la rama en GitHub
```

El PR se cierra solo al borrar su rama en GitHub; si no, ciérralo desde la página del PR
(Close pull request). En Jira, el comentario con el link y el cambio de estado se deshacen a
mano. Si quieres volver a correr el mismo ticket desde cero y quedó una sesión sin terminar,
usa `harness run TAREAS-1 --desde-cero`.

## Comandos

Todos los comandos que trabajan sobre un proyecto usan la carpeta actual; `--repo <ruta>`
apunta a otra.

### `harness run`

Procesa un ticket: rama, plan, código, tests, revisión, commit, push y PR. Es el comando
principal.

```powershell
harness run <TICKET> [opciones]
```

`<TICKET>` es una clave de Jira (`TAREAS-1`) o la ruta a un `.md` local.

| Opción | Qué hace |
|---|---|
| `--repo <ruta>` | Repo objetivo; por defecto, la carpeta actual. |
| `--max-pasos <n>` | Máximo de pasos de implementación (por defecto, `limites.max_pasos` de `harness.yaml`). El plan y la revisión tienen su propio presupuesto. |
| `--sin-plan` | Implementar directamente, sin ronda de plan. |
| `--aprobar-plan` | Mostrar el plan y preguntar antes de implementar. Necesita terminal; no se usa junto con `--sin-plan`. |
| `--sin-push` | Hacer el commit en la rama, pero no el push ni el PR. |
| `--sin-pr` | Hacer commit y push, pero no el pull request. |
| `--sin-herramientas-github` | No darle al modelo las herramientas de GitHub por MCP (ahorra tokens). El PR puede seguir por MCP. |
| `--sin-cache` | No usar la caché de dependencias: se descarga todo de nuevo. El volumen de caché no se toca. |
| `--sin-revision` | No hacer el paso de revisión del diff en esta ejecución. |
| `--conservar` | No borrar los contenedores ni la red al terminar, para entrar a depurar (`docker exec`). Se borran después con `harness limpiar`. |
| `--perfil <nombre>` | Usar ese perfil de `config.toml` en esta ejecución. |
| `--proveedor <p>` | Proveedor para esta ejecución (con `--modelo`); gana sobre el perfil y el `.env`. |
| `--modelo <id>` | Modelo para esta ejecución; sin `--proveedor`, del proveedor que se usaría. |
| `--revisor <perfil o proveedor:modelo>` | Otro modelo para el paso de revisión, para que no se revise a sí mismo. Si falla, la revisión queda como fallida y la ejecución sigue. |
| `--desde-cero` | Si hay una sesión sin terminar del ticket, descartarla (se renombra, no se borra) y empezar de nuevo. Sin esta opción, `run` se detiene y sugiere `harness resume`. Si la rama del ticket ya existe, hay que borrarla a mano. |

```powershell
harness run TAREAS-1 --aprobar-plan --revisor openrouter:qwen/qwen3-coder:free
harness run tickets\TAREAS-2.md --sin-push --perfil rapido
```

### `harness resume`

Continúa una ejecución que se cortó (límite de pasos, proveedor caído, error, Ctrl+C), desde el
paso donde quedó, en la misma rama y con la misma conversación. Úsalo cuando `run` terminó a
medias y te sugiere `harness resume <TICKET>`.

| Opción | Qué hace |
|---|---|
| `--repo <ruta>` | Repo objetivo; por defecto, la carpeta actual. |
| `--max-pasos <n>` | Pasos para esta parte (por defecto, el de `harness.yaml`). |
| `--proveedor <p>` | Retomar con otro proveedor (con `--modelo`); después, el respaldo. |
| `--modelo <id>` | Retomar con otro modelo. |
| `--perfil <nombre>` | Retomar con este perfil de `config.toml`. |
| `--revisor <perfil o proveedor:modelo>` | Otro modelo para el paso de revisión. |
| `--sin-cache` | Sin caché de dependencias. |
| `--sin-revision` | Sin paso de revisión. |
| `--sin-herramientas-github` | Sin las herramientas de GitHub para el modelo. |
| `--conservar` | No borrar los contenedores ni la red al terminar. |

```powershell
harness resume TAREAS-1 --perfil fuerte --max-pasos 15
```

### `harness ticket`

Muestra el ticket tal como lo recibe el modelo (con los tickets enlazados y subtareas de Jira),
sin ejecutar nada ni llamar al modelo. No tiene opciones.

```powershell
harness ticket TAREAS-1
harness ticket tickets\TAREAS-2.md
```

### `harness init`

Genera `AGENTS.md` y `harness.yaml` si faltan (el modelo analiza el repo) y agrega los logs y
las sesiones del harness al `.gitignore`. Úsalo una vez por proyecto, antes del primer ticket.

| Opción | Qué hace |
|---|---|
| `--repo <ruta>` | Repo objetivo; por defecto, la carpeta actual. |
| `--verificar` | Después, levantar el entorno con `harness.yaml`, instalar y correr los tests. |
| `--sin-cache` | Con `--verificar`: sin caché de dependencias. |
| `--conservar` | Con `--verificar`: no borrar los contenedores ni la red al terminar. |
| `--perfil <nombre>` | Perfil de `config.toml` para esta ejecución. |
| `--proveedor <p>` | Proveedor para esta ejecución (con `--modelo`). |
| `--modelo <id>` | Modelo para esta ejecución. |

```powershell
harness init --verificar --perfil fuerte
```

### `harness limpiar`

Borra lo que dejó el harness en Docker: contenedores y redes (y, si se pide, la caché y las
imágenes). Solo toca lo que tiene la etiqueta `harness=1` y no es de una ejecución viva; muestra
qué va a borrar y cuánto ocupa, y pregunta antes. Úsalo después de `--conservar`, de un corte
brusco, o para recuperar espacio.

| Opción | Qué hace |
|---|---|
| `--cache` | También los volúmenes de la caché de dependencias. |
| `--proyecto <nombre>` | Con `--cache`: solo el volumen de caché de ese proyecto. |
| `--imagenes` | También las imágenes del `harness.yaml` del repo, si ningún contenedor las usa. |
| `--repo <ruta>` | Con `--imagenes`: el repo del `harness.yaml`; por defecto, la carpeta actual. |
| `--si` | No preguntar: borrar directamente (para scripts). |

```powershell
harness limpiar
harness limpiar --cache --proyecto tareas-api
```

### `harness config`

Sin opciones, el asistente del [paso 3](#3-configurar-el-modelo-y-las-credenciales-harness-config)
(necesita terminal). Las opciones hacen una sola cosa:

| Opción | Qué hace |
|---|---|
| `--mostrar` | Muestra la ruta, los perfiles, el respaldo, el revisor, cada credencial enmascarada (`****` y, si es larga, sus últimos 4 caracteres) con su origen, y la URL y el email de Jira. |
| `--credencial NOMBRE` | Guarda una credencial (`GITHUB_TOKEN`, `JIRA_API_TOKEN`...): con terminal se pide oculta; sin terminal se lee de stdin. Nunca se pasa el valor por argumento. |
| `--importar-env` | Copia el modelo (como perfil) y las credenciales del `.env` de desarrollo del harness. |
| `--perfil <nombre>` | Con `--importar-env`: el nombre del perfil que se crea. |

```powershell
harness config --mostrar
Get-Content token.txt | harness config --credencial GITHUB_TOKEN
```

### `harness perfiles`

Lista los perfiles de `config.toml`: `*` marca el predeterminado, y cada uno muestra su
proveedor y modelo, la variable de su API key y si está (`✓`) o falta (`✗ falta`). Debajo, el
respaldo, el revisor y el modelo que se usaría ahora (y de dónde sale). No tiene opciones.

```powershell
harness perfiles
```

### `harness perfil usar`

Cambia el perfil predeterminado (queda guardado). Sin nombre, abre un menú: ↑/↓ y Enter (o el
número del perfil), Esc cancela.

```powershell
harness perfil usar fuerte
harness perfil usar
```

### `harness perfil agregar`

Agrega un perfil. Con terminal pregunta lo que falte, y la API key si su variable no está en
ningún lado; sin terminal necesita `--proveedor` y `--modelo`.

| Opción | Qué hace |
|---|---|
| `--proveedor <p>` | `gemini`, `groq`, `openrouter`, `openai`, `anthropic` o `personalizado`. |
| `--modelo <id>` | Id del modelo. |
| `--base-url <url>` | Solo para `personalizado`: la URL base de la API. |
| `--api-key-env <VARIABLE>` | Solo para `personalizado`: la variable con la API key. |

```powershell
harness perfil agregar fuerte --proveedor openrouter --modelo qwen/qwen3-coder:free
```

### `harness perfil editar`

Cambia un perfil. Con terminal pregunta, con los valores actuales por defecto; sin terminal
aplica solo las opciones que vengan. Tiene las mismas opciones que `agregar`.

```powershell
harness perfil editar fuerte --modelo nvidia/nemotron-3-ultra-550b-a55b:free
```

### `harness perfil borrar`

Borra un perfil. Las credenciales quedan, porque las pueden usar otros perfiles. Si era el
predeterminado, avisa que ya no hay uno; si estaba en el respaldo o era el revisor, lo saca de
ahí.

| Opción | Qué hace |
|---|---|
| `--si` | No preguntar (para scripts). |

```powershell
harness perfil borrar fuerte
```

### `harness perfil probar`

Hace un pedido de prueba al perfil (sin nombre, al predeterminado): comprueba la key y que el
modelo llame herramientas. Ver el [paso 4](#4-comprobar-el-modelo-harness-perfil-probar).

```powershell
harness perfil probar fuerte
```

### `harness version`

Muestra la versión instalada del harness.

```powershell
harness version
```

## Configuración

Hay dos lugares: lo de **cada usuario** (modelo, credenciales, servidores MCP) en `~/.harness/`,
y lo de **cada proyecto** (`AGENTS.md` y `harness.yaml`) en la raíz del repo, que se sube a Git.

### `~/.harness/config.toml`

Lo escriben `harness config` y `harness perfil`. Se puede editar a mano, pero el archivo se
regenera entero al guardar: los comentarios no se conservan. Ejemplo completo (valores falsos):

```toml
perfil = "rapido"                       # el predeterminado
respaldo = ["fuerte", "groq:openai/gpt-oss-120b"]   # si el proveedor se cae, en orden
revisor = "fuerte"                      # quién hace el paso de revisión (opcional)

[perfiles.rapido]
proveedor = "gemini"
modelo = "gemini-modelo-de-ejemplo"

[perfiles.fuerte]
proveedor = "openrouter"
modelo = "qwen/qwen3-coder:free"

[perfiles.mio]                          # solo proveedor = "personalizado" lleva estas dos
proveedor = "personalizado"
modelo = "mi-modelo"
base_url = "https://api.ejemplo.com/v1"
api_key_env = "MI_API_KEY"

[credenciales]                          # una por proveedor: la comparten sus perfiles
GEMINI_API_KEY = "clave-falsa-gemini-0000"
OPENROUTER_API_KEY = "clave-falsa-openrouter-0000"
GROQ_API_KEY = "clave-falsa-groq-0000"
MI_API_KEY = "clave-falsa-propia-0000"
GITHUB_TOKEN = "github_pat_falso_0000"
JIRA_API_TOKEN = "token-falso-jira-0000"

[jira]                                  # no son secretos
url = "https://mi-sitio.atlassian.net"
email = "yo@ejemplo.com"
```

- Los nombres de perfil son minúsculas, números, `-` y `_`. Un perfil inexistente en `perfil`,
  `respaldo` o `revisor`, una clave desconocida o un `personalizado` sin `base_url` o
  `api_key_env` dan un error que dice qué campo falla.
- `respaldo` y `revisor` aceptan un nombre de perfil o `proveedor:modelo`.
- `[credenciales]` acepta cualquier nombre de variable: se cargan en el entorno del proceso,
  así que también sirven para las referencias `${NOMBRE}` de `mcp.json` y `harness.yaml`.
- En Linux y macOS el archivo queda con permisos `600` (solo tu usuario). En Windows hereda los
  permisos de tu carpeta de usuario.

**Orden de prioridad del modelo.** Gana el primero que tenga valor:

1. `--proveedor` y `--modelo` (solo `--modelo` cambia el modelo del proveedor que salga de
   abajo).
2. `--perfil <nombre>`.
3. Las variables de entorno (`HARNESS_PROVEEDOR`, `HARNESS_MODELO`...).
4. El perfil predeterminado (`perfil`) de `config.toml`.
5. Si no hay nada: error, "ejecuta 'harness config'".

Cada API key se busca primero en el entorno y después en `[credenciales]`. El respaldo y el
revisor siguen el mismo orden: la opción, después `HARNESS_RESPALDO` / `HARNESS_REVISOR` y por
último `respaldo` / `revisor` de `config.toml`.

### Variables de entorno

Todas son opcionales si usas `config.toml`; una variable del entorno gana sobre `config.toml`.

| Variable | Para qué |
|---|---|
| `HARNESS_PROVEEDOR` | Proveedor: `gemini`, `groq`, `openrouter`, `openai`, `anthropic` o `personalizado`. |
| `HARNESS_MODELO` | Id del modelo. |
| `HARNESS_BASE_URL`, `HARNESS_API_KEY_ENV` | Solo con `HARNESS_PROVEEDOR=personalizado`: la URL base y el nombre de la variable de la key. |
| `HARNESS_RESPALDO` | Respaldo, separado por comas: perfiles o `proveedor:modelo` (`fuerte,groq:openai/gpt-oss-120b`). |
| `HARNESS_REVISOR` | Quién revisa el diff: un perfil o `proveedor:modelo`. |
| `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | La key de cada proveedor. |
| `GITHUB_TOKEN` | Token fine-grained para el pull request. |
| `JIRA_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` | Jira Cloud: el sitio, tu email y el API token. |
| `JIRA_MCP_TOKEN` | Solo para el servidor `jira-remoto` de `mcp.json` (un token con scopes de Rovo MCP). |
| `HARNESS_MCP_GITHUB` | Qué servidor de `mcp.json` hace de GitHub (por defecto `github`); `rest` fuerza la API REST. |
| `HARNESS_MCP_JIRA` | Qué servidor de `mcp.json` hace de Jira (por defecto `jira`); `rest` fuerza la API REST. |
| `HARNESS_MCP_HERRAMIENTAS_MODELO` | `0` para no darle al modelo las herramientas de GitHub (como `--sin-herramientas-github`). |
| `HARNESS_CONSOLA_CRUDA` | Ruta de un archivo: copia ahí, byte a byte, todo lo que el harness escribe en la consola (para diagnosticar líneas que no se ven). Bórralo al terminar. |

### `mcp.json`

`~/.harness/mcp.json` lista servidores MCP con el formato `mcpServers` de otros clientes MCP.
El harness usa dos:

- **`github`:** le da al modelo herramientas de lectura de GitHub y crea el pull request.
- **`jira`:** comenta el link del PR en el ticket. El ticket siempre se lee por la API REST.

Cada servidor puede correr de dos formas:

- **Docker (stdio):** `"command": "docker"` con `"args": ["run", "-i", "--rm", ...]`. El harness
  lanza el contenedor y le habla por stdin/stdout. Necesita Docker, pero no cuentas externas.
- **HTTP:** `"type": "http"` con `"url"` y `"headers"` (o `"auth": {"basic": {...}}`), para
  servidores remotos como el de GitHub Copilot o el de Atlassian.

Los secretos nunca se escriben en el archivo: se referencian como `${GITHUB_TOKEN}` y se toman
del entorno o de `[credenciales]`. `harness config` ofrece crear `mcp.json` a partir del ejemplo
del paquete, con `github` y `jira` por Docker y `github-remoto` y `jira-remoto` por HTTP. Para
usar un remoto: `HARNESS_MCP_GITHUB=github-remoto`.

**Sin `mcp.json`, o con `HARNESS_MCP_GITHUB=rest` / `HARNESS_MCP_JIRA=rest`,** el PR y el
comentario van por la API REST. Si un servidor MCP no conecta, el harness avisa y sigue por la
API REST.

### `harness.yaml`

Va en la raíz del proyecto y se sube a Git, así que **no lleva secretos**: solo valores de
prueba o referencias `${VARIABLE}`. Lo lee el harness (no el modelo) y lo valida completo al
arrancar, antes de crear contenedores o llamar al modelo. Ejemplo con todas las claves:

```yaml
version: 1                          # obligatoria; hoy solo existe 1
proyecto: mi-api                    # obligatoria; minúsculas, números y guiones
requiere_agents_md: false           # true = harness run se detiene si falta AGENTS.md

docker:
  imagen: python:3.12-slim          # obligatoria; imagen de Docker Hub
  directorio_trabajo: /app          # donde se monta el repo (por defecto /app)
  instalar:                         # corren una vez, al crear el contenedor
    - pip install -r requirements.txt
    - pip install -r requirements-dev.txt
  reinstalar_si_cambia:             # si el modelo toca estos, se vuelve a instalar
    - requirements*.txt
  cache: true                       # caché de dependencias entre ejecuciones
  timeout_descarga_segundos: 600    # tiempo máximo de cada docker pull
  usuario_host: false               # solo Linux: correr con tu usuario

comandos:
  tests: pytest -q                  # obligatoria; código de salida 0 = pasó
  lint: ruff check .                # opcional; null = sin lint

comandos_permitidos:                # lo que el modelo puede ejecutar (primera palabra)
  - pytest
  - python

servicios:                          # contenedores extra, en una red privada por ejecución
  - nombre: db                      # la dirección dentro de la red
    imagen: postgres:16
    variables:
      POSTGRES_PASSWORD: test
      POSTGRES_DB: app_test
    listo_cuando: pg_isready -h 127.0.0.1 -U postgres
    timeout_listo_segundos: 60

variables_entorno:                  # para el contenedor del proyecto
  DATABASE_URL: postgresql://postgres:test@db:5432/app_test
  API_EXTERNA_KEY: ${MI_API_EXTERNA_KEY}   # secreto real: se toma de tu entorno

git:
  rama_base: main
  prefijo_rama: feature/            # la rama es feature/<TICKET>
  remoto: origin

limites:
  max_pasos: 30                     # pasos de implementación
  max_pasos_plan: 10                # cada ronda de plan; no descuenta de max_pasos
  max_intentos_validacion: 3
  timeout_comando_segundos: 120
  max_caracteres_salida: 4000
  max_caracteres_conversacion: 200000
  compactacion: marca               # o resumen

revision:
  activa: true
  max_vueltas: 2
  max_pasos: 6

jira:
  estado_al_abrir_pr: En revisión   # null (por defecto): el ticket no se mueve

archivos_protegidos:                # el modelo no los puede crear, cambiar ni borrar
  - migrations/**
```

El detalle de cada clave, sus valores por defecto y las reglas de validación están en
[docs/harness-yaml.md](docs/harness-yaml.md).

### `AGENTS.md`

Va en la raíz del proyecto y se sube a Git. Es lo que el modelo lee al empezar cada ticket (los
primeros 12.000 caracteres): sin él, el modelo trabaja sin las convenciones del proyecto y el
harness lo avisa al empezar y al terminar. Conviene que diga, en pocas líneas:

- qué es el proyecto y su stack;
- la estructura de carpetas;
- la arquitectura: las capas y qué puede usar a qué;
- las convenciones de código (nombres, idioma, estilo);
- dónde van los tests, cómo se nombran y cómo se corren;
- lo que no se debe hacer (agregar dependencias, tocar ciertas carpetas).

La revisión también compara el diff contra `AGENTS.md`, así que una regla escrita ahí se
controla dos veces. Para que la validación la haga cumplir de verdad, ver
[Reglas de arquitectura](docs/harness-yaml.md#reglas-de-arquitectura).

## Qué deja el harness en tu proyecto

| Ruta | Qué es | ¿A Git? |
|---|---|---|
| `.harness/logs/<TICKET>-<fecha>.jsonl` | El log de cada ejecución: cada herramienta con su resultado, validaciones, revisión, entrega y tiempos. Sin secretos (se reemplazan por `****`). | No |
| `.harness/logs/<TICKET>-<fecha>.md` | El resumen legible de esa ejecución: resultado, modelos, pasos y tokens, tiempos por etapa, validaciones, revisión, entrega y avisos. | No |
| `.harness/logs/<TICKET>-mcp-<servidor>.log` | Lo que escribió un servidor MCP por stdio, para diagnosticarlo. | No |
| `.harness/logs/<TICKET>-pr.md` | Solo si el PR no se pudo crear: el título y la descripción para pegarlos a mano. | No |
| `.harness/sessions/<TICKET>.json` | La sesión para `harness resume`. Una descartada con `--desde-cero` queda como `<TICKET>.descartada-<fecha>.json`. | No |
| `.harness/memory.md` | La memoria del proyecto: notas cortas que el modelo guarda con `guardar_memoria` para los próximos tickets. | **Sí** |

- **La memoria** se le pasa al modelo al empezar cada ticket, como datos (no como
  instrucciones). Solo guarda lo que no se deduce leyendo el proyecto (una variable que
  necesitan los tests, una trampa de una herramienta); el harness rechaza notas con secretos
  o que repiten lo que hizo el ticket. Las notas nuevas entran en el commit del ticket. Si pasa
  de 8.000 caracteres, el modelo recibe solo el principio y el harness avisa que conviene
  depurarla.
- **`.gitignore`:** `harness init` agrega `.harness/logs/` y `.harness/sessions/`. No ignores
  `.harness/` entero, porque `memory.md` sí se sube.

**En Docker,** cada ejecución crea contenedores (el del proyecto y los servicios) y, si hay
servicios, una red; todo lleva la etiqueta `harness=1` y se borra al terminar, también con error
o Ctrl+C (salvo con `--conservar`). Lo único que queda entre ejecuciones es el volumen de caché
`harness-cache-<proyecto>`, con lo que descargan pip, uv, npm, Maven y Gradle, y las imágenes
descargadas.

```powershell
docker ps -a --filter label=harness=1      # ver lo que creó el harness
harness limpiar                            # contenedores y redes que hayan quedado
harness limpiar --cache                    # también los volúmenes de caché
harness limpiar --imagenes                 # también las imágenes del harness.yaml
```

## Problemas comunes

**Docker cerrado.** `Docker no está corriendo: abre Docker Desktop y vuelve a intentar.` El
harness lo comprueba antes de crear la rama o llamar al modelo, así que no se pierde nada: abre
Docker Desktop, espera a que arranque y vuelve a correr. Si Docker tardó en responder, el aviso
habla de Resource Saver: apágalo en Settings > Resources.

**Gemini 503 o 429.** Con el servicio saturado (503), sin conexión o con límite de pedidos
(429), el harness reintenta solo, con esperas que van de 5 s a 60 s, hasta 5 veces, y lo avisa
(`Gemini saturado (503), reintento 1/5 en 5 s...`). Si se agotan los reintentos (o la cuota
diaria), la misma conversación sigue con el siguiente modelo del respaldo:
`... se sigue con openrouter/... desde el paso N.` Sin respaldo, la ejecución se corta y se
retoma con `harness resume <TICKET>` (con `--perfil` o `--proveedor`/`--modelo` para cambiar
de modelo). Configura el respaldo en `config.toml` (`respaldo = [...]`) o con
`HARNESS_RESPALDO`.

**PR 422 o el token sin permisos.** Al empezar, el harness comprueba que el token vea el repo,
la rama base y los pull requests, y si no, dice qué permiso falta y pregunta si sigues sin PR.
El permiso de escritura en pull requests solo se ve al crearlo: si GitHub lo rechaza (por
ejemplo con un 422), el commit y el push ya están hechos y el harness muestra
`No se pudo crear el pull request (el push sí se hizo): ...`, el link `Créalo a mano: ...` y
deja el título y la descripción en `.harness/logs/<TICKET>-pr.md`. Revisa que el token tenga
**Pull requests: Read and write** y el repo en Repository access, y abre el PR con ese link.

**Árbol sucio.** `Hay N archivo(s) sin commitear (...): commitéalos o guárdalos (git stash)
antes de correr el harness`. El harness no empieza con cambios sin commitear, para que no se
mezclen con los del modelo. Commitea (por ejemplo `AGENTS.md` y `harness.yaml` recién
generados) o usa `git stash`.

**La rama ya existe.** `La rama 'feature/TAREAS-1' ya existe. Bórrala (git branch -D
feature/TAREAS-1) para empezar el ticket de nuevo.` o `... ya existe en 'origin'. Bórrala
allá ...`. Viene de una corrida anterior: si quieres continuarla, usa `harness resume`; si no,
bórrala como en [Deshacer una corrida](#7-deshacer-una-corrida).

**El comentario de Jira no aparece.** Solo se comenta con un ticket de Jira (no con un `.md`
local) y con un PR abierto (no con `--sin-push` ni `--sin-pr`, ni si el PR falló). Mira el final
de la salida:
- `No se pudo comentar en TAREAS-1: ... Agrégalo a mano: <link del PR>`: Jira lo rechazó
  (revisa `JIRA_URL`, `JIRA_EMAIL` y `JIRA_API_TOKEN` con `harness config --mostrar`).
- `El ticket TAREAS-1 ya tiene el link del PR: no se comenta otra vez.`
- Si el servidor MCP de Jira no conectó, el harness lo avisa al empezar y comenta por la API
  REST.

Para el estado de Jira, si no hay una transición al estado de `jira.estado_al_abrir_pr`, el
aviso dice a qué estados se puede pasar desde el actual.

## Seguridad

- El código generado corre solo en Docker, con timeout y una lista de comandos permitidos; las
  bases de datos van en contenedores propios de cada ejecución.
- Push solo a la rama del ticket (`feature/<TICKET>`), nunca a `main`; el PR es borrador.
- Las credenciales nunca van en `harness.yaml` ni en el repo; se ocultan en el log, en lo que
  ve el modelo y en el PR. El modelo no puede leer `.env`, `.env.*` ni `.envrc`, y dentro del
  contenedor se ven vacíos.

## Desarrollo

```powershell
git clone https://github.com/GersonPoma/harness
cd harness
uv sync
uv run harness ...              # o: uv tool install -e .
uv run pytest -q                # los tests con Docker real: uv run pytest -m docker
```

En desarrollo se puede usar un `.env` en la raíz del repo del harness (ver `.env.example`);
`harness config --importar-env` lo pasa a `~/.harness/config.toml`. El plan completo del
proyecto está en [docs/plan-harness-ia.md](docs/plan-harness-ia.md).
