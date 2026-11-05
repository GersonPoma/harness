Como usuario de la API quiero eliminar tareas que ya no necesito.

> **Info:** Agregar el endpoint DELETE /tareas/{id}.

Criterios de aceptación:

1. Si la tarea existe, se elimina y la API responde 204 sin cuerpo.
2. Si no existe, responde 404 con el detalle de abajo.
3. Después de eliminarla, ya no aparece en GET /tareas.
4. Los ids de las tareas que quedan no cambian.
5. Tests para cada caso.

Respuesta cuando no existe:

```json
{"detail": "Tarea no encontrada"}
```

Referencia:

[códigos de estado en FastAPI](https://fastapi.tiangolo.com/tutorial/response-status-code/)

Revisar con @Persona de Prueba
