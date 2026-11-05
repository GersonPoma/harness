## Descripción

Como usuario de la API quiero crear tareas nuevas enviando solo el título.

Agregar el endpoint `POST /tareas`.

## Criterios de aceptación

- Recibe un JSON `{"titulo": "..."}` y devuelve la tarea creada con código **201**.
- El id lo asigna el repositorio (autoincremental); la tarea nueva tiene `completada: false`.
- Validación del título:
  - obligatorio;
  - mínimo 1 carácter y máximo 100 caracteres;
  - si no cumple, la API responde **422**.
- El largo máximo se define en una constante `LARGO_MAXIMO_TITULO`.
- Se usa un modelo de entrada `TareaCrear` (no el modelo `Tarea`).
- Tests: creación correcta, título vacío, título de 101 caracteres, cuerpo sin título.
- Después de crear, la tarea aparece en `GET /tareas`.
