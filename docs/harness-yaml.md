# Especificación de `harness.yaml` (versión 1)

`harness.yaml` es el archivo de configuración que el harness lee de la **raíz del repo
objetivo** (el proyecto sobre el que trabaja, no el repo del harness). Le dice al harness cómo
instalar el proyecto, cómo comprobar que el código está bien y qué límites respetar.

- Lo lee **el harness**, no el modelo. Lo que el modelo necesita saber (arquitectura,
  convenciones) va en `AGENTS.md`.
- Se sube a Git junto al código, así que **no lleva secretos**: solo valores de prueba o
  referencias `${VARIABLE}` (ver [Variables de entorno](#variables_entorno)).
- El harness lo valida completo **al arrancar**, antes de crear contenedores o llamar al
  modelo. Si tiene un error, se detiene sin gastar tokens.

---

## Ejemplo mínimo

Lo único obligatorio: versión, nombre, imagen y comando de tests.

```yaml
version: 1
proyecto: tareas-api

docker:
  imagen: python:3.12-slim

comandos:
  tests: pytest -q
```

## Ejemplo completo

```yaml
version: 1
proyecto: mi-api

docker:
  imagen: python:3.11-slim
  directorio_trabajo: /app
  instalar:
    - pip install -r requirements.txt
    - pip install -r requirements-dev.txt
  reinstalar_si_cambia:
    - requirements*.txt

comandos:
  tests: pytest -q
  lint: ruff check .

comandos_permitidos:
  - pytest
  - python
  - ls
  - cat

servicios:
  - nombre: db
    imagen: postgres:16
    variables:
      POSTGRES_PASSWORD: test
      POSTGRES_DB: app_test
    listo_cuando: pg_isready -h 127.0.0.1 -U postgres
    timeout_listo_segundos: 60

variables_entorno:
  DATABASE_URL: postgresql://postgres:test@db:5432/app_test
  SECRET_KEY: clave-de-prueba
  API_EXTERNA_KEY: ${MI_API_EXTERNA_KEY}

git:
  rama_base: main
  prefijo_rama: feature/
  remoto: origin

limites:
  max_pasos: 30
  max_intentos_validacion: 3
  timeout_comando_segundos: 120
  max_caracteres_salida: 4000

archivos_protegidos:
  - migrations/**
  - docs/**
```

---

## Referencia de claves

### Raíz

| Clave | Tipo | Obligatoria | Por defecto | Descripción |
|---|---|---|---|---|
| `version` | entero | sí | | Versión del formato. Hoy solo existe `1`. Permite cambiar el formato en el futuro sin romper proyectos viejos. |
| `proyecto` | texto | sí | | Nombre del proyecto. Solo minúsculas, números y guiones (`[a-z0-9-]+`). Se usa para nombrar la red y los contenedores: `harness-<proyecto>-<ticket>`. |
| `docker` | objeto | sí | | Contenedor donde corre el proyecto. Ver [`docker`](#docker). |
| `comandos` | objeto | sí | | Cómo se valida el código. Ver [`comandos`](#comandos). |
| `comandos_permitidos` | lista de texto | no | `[]` | Ver [`comandos_permitidos`](#comandos_permitidos). |
| `servicios` | lista | no | `[]` | Ver [`servicios`](#servicios). |
| `variables_entorno` | mapa texto→texto | no | `{}` | Ver [`variables_entorno`](#variables_entorno). |
| `git` | objeto | no | (valores por defecto) | Ver [`git`](#git). |
| `limites` | objeto | no | (valores por defecto) | Ver [`limites`](#limites). |
| `archivos_protegidos` | lista de globs | no | `[]` | Ver [`archivos_protegidos`](#archivos_protegidos). |

### `docker`

| Clave | Tipo | Obligatoria | Por defecto | Descripción |
|---|---|---|---|---|
| `imagen` | texto | sí | | Imagen de Docker Hub donde corre el proyecto (`python:3.12-slim`, `maven:3-eclipse-temurin-21`...). Docker la descarga la primera vez. |
| `directorio_trabajo` | texto | no | `/app` | Ruta dentro del contenedor donde se monta el repo. Todos los comandos corren ahí. |
| `instalar` | texto o lista de texto | no | `[]` | Comandos de instalación. Corren **una vez**, en orden, al crear el contenedor. Si uno falla, la ejecución se detiene con error. |
| `reinstalar_si_cambia` | lista de globs | no | `[]` | Si el modelo modifica alguno de estos archivos (por ejemplo `requirements.txt`), el harness vuelve a ejecutar `instalar` antes del siguiente comando. |

### `comandos`

| Clave | Tipo | Obligatoria | Por defecto | Descripción |
|---|---|---|---|---|
| `tests` | texto | sí | | Comando que corre los tests. Lo usa la herramienta `ejecutar_tests` y la fase de validación. |
| `lint` | texto o `null` | no | `null` | Comando del linter. Si es `null`, la herramienta `ejecutar_lint` no se le ofrece al modelo. |

- Se ejecutan con `sh -c "<comando>"` dentro de `directorio_trabajo`.
- **Código de salida 0 = pasó**; cualquier otro = falló, y la salida (recortada) vuelve al
  modelo.
- Los escribe una persona, así que no pasan por `comandos_permitidos`.

### `comandos_permitidos`

Programas que el modelo puede ejecutar con la herramienta `ejecutar_comando`, además de
`ejecutar_tests` y `ejecutar_lint`.

- Se compara la **primera palabra** del comando: con `[pytest, python]`, se permite
  `pytest -k crear`, pero no `rm -rf /app`.
- Se rechaza cualquier comando del modelo que contenga `;`, `&` (incluye `&&`), `|` (incluye
  `||`), `>`, `<`, `` ` ``, `$(` o un salto de línea (`\n`, `\r`), para que no pueda
  encadenar un programa no permitido detrás de uno permitido. Con `sh -c`, tanto `&` como el
  salto de línea separan comandos: `pytest & rm -rf /app`.
- Si la lista está vacía, el modelo no recibe la herramienta `ejecutar_comando`.
- No incluir instaladores de paquetes (`pip`, `npm`) salvo que el proyecto lo necesite: el
  modelo podría agregar dependencias por su cuenta.

### `servicios`

Contenedores extra que necesita el proyecto (base de datos, Redis...). Cada uno corre en la
red privada de la ejecución y se destruye al terminar.

| Clave | Tipo | Obligatoria | Por defecto | Descripción |
|---|---|---|---|---|
| `nombre` | texto | sí | | **Dirección del servicio dentro de la red** (`db` → `postgresql://...@db:5432/...`). Formato `[a-z0-9-]+`, único entre servicios. El contenedor se llama `harness-<proyecto>-<ticket>-<nombre>`: así dos ejecuciones pueden tener cada una su `db`. |
| `imagen` | texto | sí | | Imagen de Docker Hub (`postgres:16`, `redis:7`...). |
| `variables` | mapa texto→texto | no | `{}` | Variables de entorno **del servicio** (`POSTGRES_PASSWORD`...). Admiten `${VARIABLE}`. |
| `listo_cuando` | texto o `null` | no | `null` | Comando que se ejecuta **dentro del servicio** cada segundo hasta que devuelve 0. Si es `null`, no se espera. |
| `timeout_listo_segundos` | entero | no | `60` | Si `listo_cuando` no responde en ese tiempo, la ejecución se detiene con error. |

Los servicios se levantan antes que el contenedor del proyecto, y el harness espera a que
todos estén listos antes de ejecutar `instalar`. Si un servicio no queda listo a tiempo, el
error incluye la última salida de `listo_cuando` y las últimas 20 líneas de `docker logs` del
servicio, donde suele estar la causa (una variable mal puesta, una contraseña vacía...).

**PostgreSQL: usar `pg_isready -h 127.0.0.1`.** Al arrancar por primera vez, la imagen de
postgres levanta un servidor temporal (solo por socket local) para crear la base y después lo
reinicia. `pg_isready` sin `-h` pregunta por el socket y puede responder "listo" justo antes de
ese reinicio. Con `-h 127.0.0.1` pregunta por TCP, que el servidor temporal no escucha, así que
solo responde cuando la base está lista de verdad.

Cómo se arma, por ejecución:

- Una red `harness-<proyecto>-<ticket>` (solo si hay servicios).
- Cada servicio corre en esa red con su `nombre` como alias, sin puertos expuestos al host.
- El contenedor del proyecto se conecta a la misma red y recibe `variables_entorno`.
- Al terminar (bien, con error o con Ctrl+C) se borran los contenedores y la red. Si una
  ejecución anterior se cortó sin limpiar, sus restos se borran al empezar la siguiente.
- Las imágenes que falten se descargan antes de empezar (puede tardar la primera vez).

### `variables_entorno`

Variables que se pasan al **contenedor del proyecto** (no a los servicios).

- Valores de prueba van escritos directamente: `SECRET_KEY: clave-de-prueba`.
- Un valor secreto de verdad se escribe como `${NOMBRE}`. El harness lo reemplaza con la
  variable de entorno `NOMBRE` de la máquina del usuario (o del `.env` local en desarrollo).
- Si una variable `${NOMBRE}` no existe, la ejecución se detiene al arrancar con un mensaje
  que dice cuál falta.
- Una referencia puede ir dentro de un texto más largo:
  `DATABASE_URL: postgresql://app:${CLAVE_BD}@db:5432/app`.
- `${...}` también se puede usar en `servicios[].variables`.
- Los valores obtenidos de `${...}` **nunca se escriben en los logs** ni se le muestran al
  modelo; se reemplazan por `****`.
- Los valores se pasan a Docker por el entorno del proceso (`docker run -e NOMBRE`), no en la
  línea de comandos, donde cualquier proceso de la máquina podría leerlos.

### `git`

| Clave | Tipo | Obligatoria | Por defecto | Descripción |
|---|---|---|---|---|
| `rama_base` | texto | no | `main` | Rama desde la que se crea la rama de trabajo. El harness **nunca** hace push a esta rama. |
| `prefijo_rama` | texto | no | `feature/` | La rama de trabajo se llama `<prefijo_rama><ticket>`, por ejemplo `feature/TAREAS-1`. |
| `remoto` | texto | no | `origin` | Remoto de Git al que se hace push. |

### `limites`

| Clave | Tipo | Obligatoria | Por defecto | Descripción |
|---|---|---|---|---|
| `max_pasos` | entero | no | `30` | Máximo de vueltas del bucle (cada respuesta del modelo es un paso). Al llegar, la ejecución se detiene y se puede retomar con `harness resume`. |
| `max_intentos_validacion` | entero | no | `3` | Cuántas veces se vuelve de la validación (tests + lint) a la implementación antes de rendirse. |
| `timeout_comando_segundos` | entero | no | `120` | Tiempo máximo de cada comando dentro del contenedor. Si se pasa, se corta y el modelo recibe "tiempo agotado". |
| `max_caracteres_salida` | entero | no | `4000` | Largo máximo de la salida de una herramienta que se le devuelve al modelo. Se conserva el **final** (donde suelen estar los errores) y se avisa cuántos caracteres se recortaron. |

### `archivos_protegidos`

Rutas (globs, relativas a la raíz del repo) que el modelo **no puede crear, modificar ni
borrar**. Las herramientas `escribir_archivo` y `editar_archivo` las rechazan.

Además de las que se pongan aquí, **siempre** están protegidas, aunque no se escriban:

- `harness.yaml`: para que el modelo no pueda aflojar sus propios límites.
- `.git/**`
- `.harness/sessions/**` y `.harness/logs/**`

`.harness/memory.md` no está protegido: lo modifica la herramienta `guardar_memoria`.

Hay además archivos que el modelo **no puede ni leer**, igual que `.git/`:

- `.env` y `.env.*` (por ejemplo `.env.local`, `.env.production`), en cualquier carpeta del
  repo y sin importar mayúsculas: suelen tener secretos reales (API keys, contraseñas), y
  lo que lee el modelo se envía a la API del proveedor. `leer_archivo` y `escribir_archivo`
  los rechazan con un error que dice que el archivo puede contener secretos.
- Se permite `.env.example`: es la plantilla sin valores reales que se sube a Git.
- La comprobación se hace sobre la ruta ya resuelta, así que un symlink con otro nombre que
  apunta a un `.env` también se rechaza.
- `listar_directorio` sí muestra que el archivo existe (el nombre no es un secreto).
- **Dentro del contenedor los `.env` se ven vacíos.** Las herramientas no son el único
  camino: el repo se monta completo, y `cat .env` (o un test que lo imprima) le mostraría su
  contenido al modelo. Por eso, al crear el contenedor, el harness monta un archivo vacío de
  solo lectura encima de cada `.env` y `.env.*` del repo (también en subcarpetas, saltando
  `.git`, `.venv`, `node_modules` y las demás carpetas ignoradas). `.env.example` se ve normal.
  El `.env` del disco no se modifica.
- Por lo mismo, **los tests no pueden depender de un `.env`**: si un proyecto necesita
  variables para correr sus tests, se declaran en `variables_entorno` (con `${NOMBRE}` si
  son secretas).

Cómo se hace cumplir:

- `escribir_archivo` (y `editar_archivo`) rechazan escribir en cualquiera de esas rutas.
- Dentro del contenedor, `.git`, `harness.yaml` y `.harness/` se montan **de solo lectura**.
  Es importante para `.git`: un hook escrito desde el contenedor (`.git/hooks/pre-commit`) se
  ejecutaría en la máquina del usuario cuando el harness haga commit.
- Los globs de `archivos_protegidos` no se pueden montar de solo lectura (son patrones), y un
  comando permitido como `python` o un test escrito por el modelo podría modificarlos. Por eso,
  después de instalar las dependencias, el harness guarda una copia (y su hash) de cada archivo
  que coincide. Después de **cada comando** del contenedor (`ejecutar_comando`,
  `ejecutar_tests`, `ejecutar_lint` y la validación final) compara: si uno cambió o
  desapareció, lo restaura; si apareció uno nuevo en una ruta protegida, lo borra. En los dos
  casos agrega un aviso al resultado del comando para que el modelo lo sepa.

---

## Reglas de validación

- **Claves desconocidas = error.** Así se detectan errores de tipeo (`comando:` en vez de
  `comandos:`) en lugar de ignorarlos en silencio.
- Falta una clave obligatoria o un tipo no coincide → error con la ruta exacta
  (`docker.imagen: falta este campo`).
- `version` distinta de `1` → error: "versión de harness.yaml no soportada".
- Nombres de `proyecto` y `servicios[].nombre` fuera de `[a-z0-9-]+`, o servicios repetidos →
  error.
- Si no existe `harness.yaml`, el harness avisa y sugiere `harness init`.

## Implementación

- Leer con `yaml.safe_load` de `pyyaml` (**nunca** `yaml.load`, que puede ejecutar código).
- Validar con modelos de `pydantic` con `extra="forbid"` (eso da el error de claves
  desconocidas gratis) y valores por defecto declarados en los modelos.
- Dependencias: `uv add pyyaml pydantic`.
- Todo el resto del harness recibe el objeto ya validado, nunca el diccionario crudo.

---

## Lo que NO va en `harness.yaml`

| Qué | Dónde va | Por qué |
|---|---|---|
| Proveedor y modelo de IA | `~/.harness/config.toml` | Cada usuario elige su modelo y paga (o no) su API; el proyecto no lo decide. |
| API keys, tokens de GitHub y Jira | `~/.harness/config.toml` o variables de entorno | Son secretos personales. |
| Arquitectura, convenciones, ejemplos de código | `AGENTS.md` | Eso lo lee el modelo, no el harness. |
| El `Dockerfile` / `docker-compose.yml` del proyecto | Se quedan como están | El harness no los usa: son para correr la aplicación, no para los tests. |
