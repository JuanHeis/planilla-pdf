# Revisión de integración de Planilla

Fecha: 6 de noviembre de 2026. Revisión independiente del código, sus llamadas y las pruebas de integración. Alcance: versión alojada con una sola réplica y un trabajador de solicitudes, procesando un PDF a la vez; la limpieza de archivos usa una tarea separada. El piloto local de aprendizaje sigue separado.

## Estado de la revisión

El servidor conserva el PDF preparado en almacenamiento privado y verifica en cada descarga la sesión o el correo confirmado, el gratuito o la orden que corresponde. Preparar un archivo, confirmar el correo y detectar un original rellenable no consumen el gratuito. Las credenciales reales y la publicación en la nueva cuenta de Railway siguen pendientes.

La revisión del candidato técnico está cerrada: **32 pruebas aprobadas, sin hallazgos P0/P1 abiertos dentro del alcance revisado**. Se comprobaron los arreglos de recuperación y autorización en `186ae46`, además del servidor y sus pruebas de integración. El resultado permite integrar el candidato y preparar su publicación. La activación de cobros reales requiere completar las comprobaciones externas del final de este documento.

## Hallazgos encontrados y correcciones comprobadas

| Prioridad inicial | Caso concreto | Corrección integrada |
|---|---|---|
| P1 | Preparar dos veces los mismos campos generaba bytes con un identificador PDF diferente. El servidor interpretaba un reintento como una modificación y lo bloqueaba durante un pago pendiente o al terminar las 24 horas. | Se compara una huella de los campos y se reutilizan los bytes preparados cuando no cambian. La comprobación conserva la autorización del trabajo. |
| P1 | Un primer enlace de correo vencido llevaba a una recuperación que buscaba únicamente sesiones ya verificadas. No podía reenviar el acceso solicitado por primera vez. | La recuperación también encuentra la solicitud de correo del trabajo vigente, conserva los límites y responde de forma genérica. Hay pruebas para vencimiento previo a la primera verificación. |
| P1 | Un fallo de `/api/config` dejaba la interfaz en modo local. En una instalación alojada se podía guardar la respuesta JSON de preparación con extensión PDF y anunciar una descarga correcta. | Los controles esperan una configuración válida. El fallo muestra una acción de reintento y evita iniciar cargas o descargas. |
| P1 | El registro de acceso por defecto de Gunicorn incluía los enlaces de verificación en sus URLs completas. | Se retiró ese registro de acceso del comando del contenedor. Los mensajes propios de error no incluyen tokens ni texto del PDF. |
| P1 | Con Access Token y vendedor configurados, pero sin secreto de Webhooks, se podía abrir un cobro que después no habilitaría la descarga. | El checkout exige el secreto de Webhooks. Producción también exige una URL pública HTTPS. |
| P1 | Reabrir un enlace original ya verificado podía recuperar la sesión completa de un navegador que luego había usado otro correo. Un enlace antiguo no utilizado tampoco debía adquirir un trabajo ya asignado a otro correo. | Todo enlace reutilizado crea una sesión nueva limitada a su correo. Se comprueba el propietario fijo del trabajo antes de verificar o modificar una identidad. Los casos originales y de recuperación tienen pruebas separadas. |
| P2 | Cada notificación del proveedor creaba una sesión y una visita, inflando el embudo. | El endpoint de Webhooks no crea una sesión de visitante. |
| P2 | Un reclamo con saltos de línea se rechazaba aunque cumpliera el largo pedido por el formulario. | Se aceptan párrafos y tabulaciones, se mantienen los límites y se rechazan controles inapropiados. Hay prueba de regresión. |
| P2 | Los fallos de análisis y preparación no quedaban reflejados en los contadores. | Se registran eventos de fallo y de trabajos corregidos sin guardar contenido del formulario en las métricas. |
| P2 | Recuperar solamente el último trabajo dejaba sin acceso visible a compras anteriores todavía vigentes. | El correo verificado puede listar hasta 100 trabajos vigentes y abrirlos desde recuperación. El propietario queda guardado en cada trabajo; no cambia al modificar la identidad de otra sesión. |
| P2 | Una caída de Mercado Pago impedía consultar la descarga aunque ya tuviera un permiso confirmado y un PDF preparado. | Los permisos confirmados y estados finales usan el estado guardado. Un fallo temporal de consulta conserva un pago pendiente y ofrece reintento sin aprobarlo. |
| P2 | Los archivos vencidos se limpiaban únicamente cuando llegaba una visita. | El contenedor activa una tarea de limpieza cada cinco minutos mientras el proceso esté funcionando. Al volver a iniciar el servicio también se retoma la limpieza. |

También se revisaron cambios del agente de UX: mensajes de gratuito según el estado real, distinción entre original gratuito y conversión de un PDF que ya tenía algunos campos, reanudación de la misma orden y ausencia de promesas de correo automático que el servidor no envía.

## Comprobaciones del servidor y del cobro

La ejecución independiente final de `python -m unittest discover -s outputs/app/tests -v` aprobó **32 de 32 pruebas**. Usan PDFs sintéticos, Flask y proveedores inyectados sin llamadas a Mercado Pago ni envío de correos reales. Cubren:

- Rechazo de trabajos ajenos; rutas de imagen, metadatos y descarga protegidas.
- Verificación de correo y enlaces vencidos, reenvíos limitados y recuperación genérica.
- Reutilización del enlace original, solicitudes de correo anteriores, recuperación con una sesión nueva, propietario fijo y listado limitado al correo verificado.
- Gratuito consumido al descargar, reintentos del mismo trabajo y competencia entre dos descargas gratuitas.
- Original rellenable gratuito; agregar campos nuevos vuelve a exigir permiso de conversión.
- Precio generado por el servidor; idempotencia del intento de checkout ante respuestas inciertas.
- Firma HMAC de Webhooks y consulta autenticada de la orden. Se contrastan orden, vendedor, entorno, moneda, importe, referencia y acreditación completa.
- Una URL de retorno, una consulta del navegador o un estado parcial no otorgan por sí solos la descarga paga.
- Notificaciones repetidas y fuera de orden; reclamo y devolución ligados a la orden que otorgó acceso.
- Plazo de edición y conservación de la descarga ya autorizada.
- Reintento sin cambios después de preparar el PDF, conservación de permisos al reabrir el servidor y conteo único de un trabajo después de reintentos.
- Rechazo de origen cruzado; administración con credencial separada; revisión OCR global deshabilitada en la versión alojada.

La verificación de integración final también aprobó las **32 pruebas dentro de la imagen Linux de Docker**. La imagen final construyó correctamente y Gunicorn respondió por HTTP a `/health` y `/api/config` en modo comercial, con el precio y límites esperados. Una prueba de OCR en el contenedor reconoció 28 palabras y detectó 8 campos en el escaneo sintético del formulario de ejemplo. Esto comprueba la disponibilidad del OCR en Linux; no estima su precisión sobre todos los formularios.

Las regresiones locales de carga, vista previa, movimiento y agregado de campos, exportación y lectura de valores guardados también pasaron. La revisión OCR conservó aprobaciones, exportación de aprendizaje, importación idempotente y sugerencias importadas. El motor mantuvo las comprobaciones de rotación y apariencias de campos, y los controles AUH sobre 22 PDFs existentes. Los contenedores y el servidor de proveedores simulados se detuvieron al terminar la verificación.

La referencia actual de Orders respalda el checkout por `checkout_url` y la consulta del recurso notificado. La validación de la firma sigue el manifiesto documentado por Mercado Pago. [Crear orden](https://www.mercadopago.com.ar/developers/es/reference/online-payments/checkout-pro/create-order/post) · [Consultar orden](https://www.mercadopago.com.ar/developers/es/reference/online-payments/checkout-pro/get-order/get) · [Webhooks](https://www.mercadopago.com.ar/developers/es/docs/checkout-pro-orders/notifications)

## UX e Impeccable

Se leyeron las instrucciones de Impeccable y sus referencias de auditoría, resistencia a errores y calidad. Se evaluó la interfaz como herramienta para completar una tarea: acciones concretas, estado visible, teclado, errores, reintentos y diseño adaptable. El agente de UX ejecuta las interacciones en el navegador con proveedores de prueba.

Se inspeccionaron capturas de escritorio de correo, pago pendiente y administración: acciones principales identificables, texto legible y estado de pago visible. También se revisaron las capturas móviles finales de original rellenable, pago, administración, correo vencido, recuperación con listado y servicio desconectado. El contenido conserva un ancho legible y los controles quedan dentro de la pantalla. Las primeras capturas con recorte tenían un render defectuoso de la herramienta; se descartaron y se reemplazaron por imágenes directas verificadas visualmente.

El detector de Impeccable se ejecutó sobre `static/index.html` y `static/admin.html`. Emitió dos advertencias de espacio interior en los contenedores de tablas administrativas. La inspección del CSS y de las capturas confirmó que las celdas tienen 10 × 15 píxeles de espacio interior; no se añadió un segundo relleno al contenedor. No aparecieron hallazgos en la pantalla principal. Las capturas definitivas se entregan en `outputs/ux-validacion`; el registro local de la comprobación está en `work/commerce-ui-cua` del espacio de trabajo.

Esta comprobación corresponde a escritorio y tamaño móvil emulado. No equivale a una prueba en dispositivos físicos ni a una certificación exhaustiva de accesibilidad.

La matriz detallada de navegador, los comandos y las limitaciones están en `UX-VALIDATION.md`. Incluye configuración inaccesible y recuperación del servicio, correo, gratuito, tres páginas, pago pendiente/rechazado/aprobado, original gratuito, reclamo, confirmación de devolución, trabajo vencido y listado de recuperación.

## Configuración pendiente del dueño

- Nueva cuenta y proyecto de Railway, volumen persistente, URL HTTPS, copia de seguridad y presupuesto.
- Dominio de envío verificado y Resend para Railway Hobby. Gmail puede recibir soporte; SMTP saliente está bloqueado en Hobby según la documentación oficial de Railway.
- Aplicación del dueño en Mercado Pago, credenciales del entorno correcto y URL/firma de Webhooks.
- Prueba integrada con cuentas de prueba del proveedor: retorno al sitio, notificación real, descarga, rechazo y devolución.
- Verificar que el enlace al código fuente resuelva al commit exacto desplegado.

Estas son condiciones externas de activación. Las pruebas con proveedores simulados no acreditan entregabilidad del correo, cobros o devoluciones reales, ni disponibilidad del alojamiento.
