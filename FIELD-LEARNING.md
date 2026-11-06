# Aprendizaje de patrones de campos

Este piloto aprende ajustes geométricos de revisiones explícitas. No cambia Tesseract ni aprende valores, nombres de personas o textos de los formularios. No utiliza un LLM ni añade una tarifa de API.

## Qué aprende

Compara la propuesta automática inicial con los campos que el usuario confirmó como revisados. Registra campos conservados, movidos, redimensionados, eliminados y añadidos. Guarda tipos, origen del detector, proporciones, relaciones con campos vecinos y cercanía al borde. No guarda el PDF, sus imágenes, sus etiquetas, sus valores ni el correo en la base de aprendizaje.

Los patrones se describen por estructura relativa, no por organismo, nombre de archivo, versión del formulario o posición absoluta. Por eso un ajuste aprendido puede aplicarse a otra distribución y a páginas de otro tamaño si la estructura coincide. Una forma distinta con geometría demasiado diferente no recibe esa regla.

## Conservación y controles

- Cada trabajo tiene una revisión reemplazable; volver a confirmar el mismo trabajo no suma otra evidencia independiente.
- También se agrupan subidas por huella de la distribución geométrica. Cambiar el nombre o los metadatos de un PDF no fabrica nuevos ejemplos de aprendizaje.
- Para mover o redimensionar hacen falta al menos **cinco distribuciones distintas**, sin aprobaciones o eliminaciones contradictorias, y ajustes consistentes.
- Para suprimir un falso positivo hacen falta **ocho distribuciones distintas**, todas con eliminación, y confianza original menor a 0,8. Los candidatos de confianza alta se conservan.
- Los ajustes están limitados a un 20 % del tamaño y a pocos puntos PDF: movimiento hasta 6 puntos, ancho hasta 12 y alto hasta 6. Se rechazan salidas de página, tamaños pequeños y aumentos de superposición con otros campos o controles originales.
- Los campos originales y los campos manuales no se modifican. Los cambios del usuario siguen prevaleciendo sobre una sugerencia aprendida.
- Cualquier contradicción detiene esa regla. Una revisión posterior reemplaza la anterior; también se puede retirar una revisión con `forget_review`.

La base `field-learning.sqlite3` persiste en `PLANILLA_DATA_DIR`. Contiene hashes de trabajo/distribución, fecha y observaciones numéricas. Es información seudonimizada vinculable por el operador, no una garantía jurídica de anonimato. No tiene borrado automático porque el objetivo es conservar el aprendizaje; su política y controles deben declararse antes de uso comercial.

## Integración

```python
learner = FieldLearner(data_dir)

# Conservar estos campos iniciales ANTES de aplicar el aprendizaje.
proposal = detect_pdf(pdf_bytes)
learned = learner.suggest(
    proposal["fields"], proposal["pages"],
    auto_apply=False,  # Observación: devuelve los campos originales y posibles cambios.
    existing_fields=proposal.get("existingFields", []),
)

# Solo después de una acción explícita de revisión, nunca por abandonar la página.
learner.record_review(job_id, proposal["fields"], confirmed_fields, proposal["pages"])
```

`suggest` devuelve `fields`, `changes`, `modelVersion`, `mode` y `stats`. Con `auto_apply=True`, aplica únicamente reglas que superan los controles anteriores. Con `False`, devuelve los campos originales y posibles cambios con `applied=False`; sirve para evaluar el modelo antes de activarlo. El servidor debe mantener el detector original como alternativa si el aprendizaje falla.

`stats()` devuelve revisiones, distribuciones distintas, patrones y reglas elegibles, campos propuestos revisados, aprobados, corregidos, eliminados y añadidos. Esas cantidades permiten medir trabajo manual sobre geometría. **No constituyen precisión, cobertura real ni una promesa del 80–90 %:** sin una referencia humana no se conoce cuántos campos faltaban.

## Exportar y recuperar lo aprendido

`export_bundle()` devuelve un ZIP con `field-learning.json`. Solo incluye estadísticas de patrones, correcciones relativas agregadas y un identificador aleatorio de instalación. No exporta trabajos, huellas de distribuciones, etiquetas, fechas de revisión, coordenadas absolutas, PDFs o correos.

`import_bundle(bytes)` valida el formato y reemplaza cada fuente importada, evitando duplicar un ZIP o inflar evidencia al enviarlo de vuelta al origen. Como no exporta huellas de distribuciones, no puede demostrar que dos instalaciones revisaron formularios diferentes: para los umbrales se usa el mayor soporte de una fuente, no la suma de copias. La importación debe limitarse al operador o a la aplicación local.

## Límites que permanecen

El piloto puede ajustar candidatos existentes y filtrar algunos falsos positivos. **Todavía no aprende a proponer un campo que el detector nunca produjo**, ni a unir arbitrariamente una celda fragmentada. Los añadidos manuales se cuentan para medir ese problema; no se convierten en posiciones globales reutilizadas sin contexto.

La coincidencia es geométrica y deliberadamente conservadora. Puede dejar pasar oportunidades de mejora y no distingue dos regiones que tengan la misma estructura numérica pero distinto significado. La evidencia de usuarios anónimos puede ser manipulada: se recomienda iniciar la web en observación y habilitar aplicación automática solamente después de revisar ejemplos y controles de regresión. Una casilla de revisión técnica no sustituye por sí sola las decisiones legales sobre uso de correcciones.

## Verificación

Las pruebas cubren transferencia a otra escala y posición, cinco PDFs sintéticos con estilos distintos y un PDF reservado, controles negativos, contradicciones, reenvíos, cambios de revisión, protección de originales, exportación sin texto e importación idempotente. Es un piloto verificado con casos controlados, no un benchmark representativo de todos los formularios estatales.
