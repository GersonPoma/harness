Como usuario de la API quiero consultar una tarea específica por su id, para ver su detalle sin tener que traer la lista completa.

Agregar el endpoint `GET /tareas/{id}`.

## Criterios de aceptación

- [ ] `GET /tareas/{id}` devuelve la tarea con código **200** si existe.
- [ ] Si el id no existe, devuelve **404** con `{"detail": "Tarea no encontrada"}`.
- [ ] La búsqueda se implementa como método `obtener_por_id` de `RepositorioTareas`, que devuelve `None` si no encuentra la tarea.
- [ ] Tests para ambos casos (existe / no existe).

## Notas técnicas

- Respetar las capas descritas en `AGENTS.md`.
