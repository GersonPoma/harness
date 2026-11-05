Como usuario quiero que cada tarea tenga una prioridad para saber qué hacer primero.

Las prioridades posibles son:

| **Valor** | **¿Por defecto?** |
| --- | --- |
| alta | no |
| media | sí |
| baja | no |

Si al crear una tarea no se indica prioridad, debe quedar en media.

Criterios de aceptación:

- Toda tarea tiene el campo prioridad con uno de los valores: alta, media, baja.
- El valor por defecto es media.
- Un valor distinto a los permitidos se rechaza con 422.
- El campo aparece en todas las respuestas que devuelven tareas.
- Tests del valor por defecto y del valor inválido.
