# Requisitos de Planilla web

Versión de alcance del 6 de noviembre de 2026. Este documento define el resultado que debe verificarse antes de habilitar cobros reales. Los estados de implementación y las pruebas se registran en la revisión final del repositorio.

## Producto y límites iniciales

Planilla convierte formularios PDF del usuario en una copia rellenable. El usuario revisa y corrige la propuesta antes de decidir la descarga. El servicio identifica los controles originales para evitar cobros por una conversión que ya estaba resuelta.

| ID | Requisito | Criterio de aceptación |
|---|---|---|
| PDF-01 | Cargar un PDF y mostrar su resultado con campos superpuestos. | Una carga válida muestra páginas, campos nuevos y controles originales diferenciados. Una carga inválida explica cómo continuar. |
| PDF-02 | Editar posiciones, tamaños y nombres, agregar texto o casillas y quitar propuestas. | La descarga contiene las posiciones y tipos que confirmó el usuario y conserva el contenido del PDF. |
| PDF-03 | Avisar si el original ya es rellenable. | Los controles existentes se cuentan y muestran con otra marca. La descarga del original o la indicación de que ya se puede completar no gasta la promoción. |
| PDF-04 | Preparar y validar el archivo final antes de cobrar o consumir la promoción. | Una falla de preparación conserva el derecho gratuito y permite reintentar. Nunca se entrega el PDF final por una ruta pública. |
| PDF-05 | Límite comercial inicial de cinco páginas por documento. | La interfaz muestra el límite antes del pago y el servidor lo valida. Los archivos fuera de alcance reciben una explicación. |
| PDF-06 | Mostrar que la detección requiere revisión. | El editor indica que pueden faltar o sobrar campos y mantiene visible la acción para corregirlos. |
| PDF-07 | Firmas, cifrado y escaneos difíciles reciben un resultado comprensible. | El usuario obtiene el motivo del bloqueo o una advertencia de revisión antes de pagar. |

## Correo, sesión y primera descarga

| ID | Requisito | Criterio de aceptación |
|---|---|---|
| ACC-01 | Permitir cargar y revisar con una sesión anónima. | El servidor crea una identidad opaca en una cookie HttpOnly. Un visitante carece de acceso a los trabajos de otra sesión. |
| ACC-02 | Pedir correo al solicitar la descarga. | No se requiere contraseña. El correo se verifica mediante enlace temporal de un solo uso. |
| ACC-03 | Conservar el trabajo al pasar por correo y pago. | Volver desde un enlace válido recupera el trabajo y su estado. Un enlace vencido permite pedir otro sin perder el archivo preparado mientras siga vigente. |
| ACC-04 | Reenviar correo con límites. | Los reintentos normales funcionan. Pedidos repetidos demasiado rápido devuelven una espera concreta y conservan el trabajo. |
| FREE-01 | Primera conversión gratuita de hasta dos páginas por correo verificado. | El servidor decide la elegibilidad y la interfaz explica antes de confirmar si la descarga será gratuita o paga. |
| FREE-02 | Consumir el gratuito cuando el resultado preparado esté disponible. | Dos solicitudes concurrentes o repetidas no crean dos usos gratuitos. Un fallo previo deja el beneficio disponible. |
| FREE-03 | Avisar el efecto de cada acción sobre la promoción. | Se muestran estados como “Tu descarga gratis sigue disponible”, “Esta descarga usa tu gratuito” y “El gratuito ya se usó”. |
| FREE-04 | Conservar reintentos del mismo trabajo. | Repetir la descarga de un trabajo autorizado no consume otro beneficio ni cobra otra vez. |
| FREE-05 | Medir abuso mediante señales limitadas. | Se registran intentos y resultados de la promoción sin afirmar que un navegador o correo representa una persona única. |

## Precio y Mercado Pago

El precio inicial es **ARS 2.990 por PDF de hasta cinco páginas**. El trabajo permite reexportar durante 24 horas. Un pack de cinco conversiones por ARS 9.990 queda documentado como experimento posterior, sujeto a validar uso recurrente. La versión inicial presenta un solo precio de conversión.

| ID | Requisito | Criterio de aceptación |
|---|---|---|
| PAY-01 | Mostrar importe, moneda, páginas y alcance antes de salir al checkout. | El cliente sabe qué compra y ve el precio que creó el servidor. |
| PAY-02 | Crear una orden de Checkout Pro vinculada a un trabajo y comprador. | El servidor fija ARS, importe y referencia. La entrada del navegador no puede reducir el precio ni cambiar la propiedad del archivo. |
| PAY-03 | Permitir pago como invitado cuando el proveedor lo ofrezca. | El usuario puede continuar sin una cuenta propia de Planilla ni una contraseña. |
| PAY-04 | Validar el webhook y consultar la orden al proveedor. | Solo se autoriza un pago real cuyo vendedor, entorno, importe, moneda, referencia y estado correspondan al trabajo. |
| PAY-05 | Procesar notificaciones de forma idempotente. | Duplicados y eventos fuera de orden no duplican permisos ni revierten un pago confirmado por un evento antiguo. |
| PAY-06 | Tratar la URL de retorno como información de navegación. | Visitar una URL de éxito o modificar parámetros del navegador no habilita una descarga. |
| PAY-07 | Autorizar cada descarga desde el servidor. | Un archivo preparado sin permiso devuelve un estado que explica el siguiente paso. Los enlaces requieren posesión de un secreto de acceso vigente. |
| PAY-08 | Recuperar estados pendientes y errores transitorios. | Se puede actualizar el estado y reintentar una consulta. Una interrupción del navegador no pierde un pago aprobado. |
| PAY-09 | Explicar rechazo o cancelación con el motivo disponible. | La interfaz traduce los estados conocidos y ofrece un próximo paso. Evita atribuir una causa que el proveedor no informó. |
| PAY-10 | Separar configuración de prueba y producción. | Producción rechaza una configuración incompleta y no acepta pagos de pruebas como ingresos reales. |

## UX para una persona no técnica

El mensaje de cada paso debe decir qué está pasando, si se gastó el gratuito o se cobró y qué puede hacer la persona a continuación. Los nombres internos de errores se reservan para los registros técnicos.

| Momento | Información y acción requeridas |
|---|---|
| Subida | Progreso o actividad visible, límites de archivo y explicación si falla. |
| Detección | “Estamos buscando dónde completar el formulario”. Resultado con cantidad y revisión sugerida. |
| Original rellenable | Aviso visible del número de controles originales y confirmación de que la promoción sigue disponible. |
| Preparación | Actividad visible. Si falla, se indica que el gratuito sigue disponible y se ofrece reintentar. |
| Correo | Destino que eligió el usuario, instrucciones para abrir el enlace y opción para corregir correo o reenviar. |
| Correo vencido | Motivo, acción para pedir otro enlace y confirmación de que no se consumió una descarga. |
| Gratuito disponible | Alcance de dos páginas, confirmación antes de consumirlo y ausencia de cobro. |
| Pago por iniciar | Total y contenido de la compra junto al botón de continuar. |
| Pago pendiente | Aviso de que el proveedor todavía procesa el pago y acción para consultar nuevamente. |
| Pago aprobado | Confirmación y acceso a descarga o recuperación. |
| Pago rechazado o cancelado | Motivo disponible y acción de volver a intentar o elegir otro medio. |
| Descarga repetida | Confirmación de que es el mismo trabajo autorizado y evita un segundo cobro. |
| Trabajo vencido | Explicación de vencimiento y acción para abrir otro archivo. |
| Reclamo | Enlace accesible con referencia de la compra y campos breves para describir el problema. |

## Reclamos y calidad

| ID | Requisito | Criterio de aceptación |
|---|---|---|
| QUA-01 | Revisión manual antes de pagar. | El usuario puede corregir una propuesta incompleta y ver el resultado. |
| QUA-02 | Registrar reclamos de calidad vinculados a la compra. | El usuario puede indicar campos faltantes o resultado inutilizable y conserva una referencia del reclamo. |
| QUA-03 | Devolver desde un estado verificable y autorizado. | Una devolución se vincula a la orden, es idempotente y queda registrada con su resultado. El navegador no puede marcar un pago como devuelto. |
| QUA-04 | Medir la necesidad de una revisión por IA. | Se registra la frecuencia de reclamos y correcciones. Un detector de IA requiere una evaluación separada de costo y beneficio antes de incorporarse. |
| QUA-05 | Tratar el aprendizaje OCR como dato del usuario. | La exportación global del piloto local no queda públicamente accesible en la versión alojada. |

## Estadísticas y operación

| ID | Requisito | Criterio de aceptación |
|---|---|---|
| MET-01 | Registrar eventos del embudo en el servidor. | Existen eventos para carga, detección, original rellenable, preparación, correo, gratuito, checkout, pago, descarga y reclamo. |
| MET-02 | Distinguir ingresos de intentos. | Los ingresos provienen de pagos verificados. Los duplicados de webhook cuentan una vez. |
| MET-03 | Ofrecer resumen administrativo protegido. | El dueño puede consultar visitas o sesiones, conversiones, uso gratuito, pagos, ingresos, fallos y reclamos. |
| MET-04 | Minimizar lo que guardan las métricas. | Los eventos no contienen el texto extraído del PDF, el archivo ni credenciales de pago. |
| OPS-01 | Una aplicación Python y un volumen persistente en Railway. | Reiniciar o desplegar no pierde órdenes, beneficios, revisiones y permisos vigentes. |
| OPS-02 | Limpiar archivos y enlaces vencidos. | La aplicación aplica la retención declarada y conserva la información transaccional que necesita para resolver pagos y reclamos. |
| OPS-03 | Limitar trabajo y gasto. | Existen límites de tamaño, páginas y frecuencia. Railway tiene alertas y un límite de gasto definido por el dueño. |
| OPS-04 | Salud y registros sin secretos. | Una comprobación de salud responde sin exponer configuración. Las excepciones se registran sin Access Token, correo completo ni contenido del PDF. |
| OPS-05 | Proteger la administración. | Las métricas y operaciones sensibles requieren credenciales administrativas distintas de los tokens de usuarios. |

## Código, licencia y entrega

| ID | Requisito | Criterio de aceptación |
|---|---|---|
| SRC-01 | Mantener historial Git incremental. | Cada área se integra mediante commits revisables y los cambios de datos privados quedan ignorados. |
| SRC-02 | Publicar Planilla bajo AGPL v3 o posterior. | El repositorio incluye LICENSE y NOTICE, y ofrece el código correspondiente a la versión alojada. |
| SRC-03 | Mostrar un enlace de código fuente en la web. | El enlace apunta al código exacto de la versión desplegada y sus instrucciones de reconstrucción. |
| SRC-04 | Subir el proyecto a GitHub del dueño. | El repositorio remoto coincide con la versión desplegada y excluye claves, PDFs de trabajo, bases de datos y archivos de usuarios. |
| SRC-05 | Revisar el flujo integrado con un agente independiente. | La revisión cubre seguridad de descarga, correo, promoción, pago, reintentos, métricas y UX. Se registran hallazgos y correcciones antes del lanzamiento. |

## Comprobación de salida

La revisión final debe recorrer una conversión gratuita, una paga, un original ya rellenable, un correo vencido, un pago pendiente, uno rechazado, un webhook repetido, un enlace de otro usuario, un reinicio y un reclamo. Los pagos reales requieren las credenciales del dueño y una configuración de producción validada.
