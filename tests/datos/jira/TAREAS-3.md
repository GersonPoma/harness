Como usuario de la API quiero marcar tareas como completadas y poder listar solo las pendientes o solo las completadas.

Criterios de aceptación:

- [ ] PATCH /tareas/{id}/completar marca la tarea como completada y la devuelve con código 200.
- [ ] Si la tarea no existe, devuelve 404 con {"detail": "Tarea no encontrada"}.
- [ ] Completar una tarea que ya estaba completada no da error (devuelve 200).
- [ ] GET /tareas acepta el parámetro opcional completada: con completada=true solo las completadas, con completada=false solo las pendientes, y sin parámetro todas, como hasta ahora.
- [ ] El filtrado se hace en el repositorio, no en main.py.
- [x] Tests para cada caso.
