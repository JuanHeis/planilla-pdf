# Publicar Planilla

La versión alojada usa una aplicación Python con Flask y Gunicorn. Los archivos privados y la base de órdenes viven en un volumen persistente. La publicación corresponde al contenido del repositorio Git y la variable SOURCE_URL identifica la revisión exacta ofrecida a los usuarios.

## Datos que debe aportar el dueño

| Dato | Dónde encontrarlo | Dónde configurarlo |
|---|---|---|
| Cuenta y proyecto de Railway | Perfil y lista de proyectos de Railway | Conectar la cuenta correcta al conector o abrir el proyecto del dueño. |
| Repositorio de GitHub | Cuenta del dueño y nombre elegido para el repositorio | Conectar el repositorio como origen del servicio de Railway. |
| Access Token de Mercado Pago | Developers, Tus integraciones, aplicación, Credenciales de producción | Variable secreta MERCADOPAGO_ACCESS_TOKEN. |
| Identificador de vendedor | Datos de la cuenta o respuesta de usuario de Mercado Pago | MERCADOPAGO_COLLECTOR_ID. Debe ser la cuenta que cobra. |
| Clave de Webhooks | Developers, Tus integraciones, aplicación, Webhooks | Variable secreta MERCADOPAGO_WEBHOOK_SECRET. |
| Correo que enviará mensajes | Dominio propio verificado en Resend, sección Domains; clave en API Keys | EMAIL_FROM y RESEND_API_KEY. En Railway Hobby el envío debe usar la API HTTPS. |
| Correo de soporte | Gmail que cree el dueño | SUPPORT_EMAIL. Puede recibir consultas sin ser el remitente de verificaciones. |
| Acceso administrativo | Valor aleatorio generado para esta instalación | Variable secreta ADMIN_TOKEN, distinta de las credenciales de clientes. |

Las claves se ingresan en Railway Variables. No se pegan en el chat, el código ni el repositorio.

## Railway

1. Crear el servicio desde el repositorio de Planilla. La raíz del servicio debe ser la carpeta que contiene Dockerfile y requirements.txt.
2. Seleccionar construcción mediante el Dockerfile incluido. El contenedor escucha en el PORT que asigna Railway.
3. Adjuntar un volumen en **/data**. Mantener una sola réplica para esta versión basada en SQLite y procesamiento PDF secuencial.
4. Configurar las variables de .env.example. PLANILLA_MODE debe ser web, APP_ENV debe ser production y PLANILLA_DATA_DIR debe apuntar a /data.
5. Crear un dominio de Railway o conectar el dominio del dueño. Configurar PUBLIC_BASE_URL con la URL HTTPS final antes de enviar correos o crear órdenes.
6. Configurar la comprobación de salud en **/health**. El proceso de Gunicorn usa un trabajador y un hilo para evitar acceso concurrente al motor PDF.
7. Activar copias de seguridad del volumen y definir alertas y límite de gasto del proyecto en el panel de Railway.
8. Comprobar la persistencia con un reinicio antes de habilitar pagos reales. Revisar un trabajo y una orden después del reinicio.

El contenedor habilita `PLANILLA_BACKGROUND_CLEANUP=1`: revisa los archivos vencidos cada cinco minutos aun sin visitas. Los trabajos sin habilitar vencen a los siete días de la carga; una descarga gratuita o pago confirmado inicia siete días de conservación desde esa habilitación. Si el servicio está apagado, la limpieza se reanuda al arrancar. Las métricas no conservan textos del documento. Los eventos del embudo cuentan trabajos únicos en preparación y descarga, para que los reintentos no inflen el resultado.

Gunicorn no registra las URLs de las solicitudes: los enlaces de correo contienen credenciales privadas. Al agregar trazas o logs de proxy, evitar guardar parámetros de `/verify`, cookies y encabezados de autorización.

La documentación consultada el 6 de noviembre de 2026 indica que railway.json y railway.toml están deprecados para servicios nuevos. Esta entrega configura la construcción con Dockerfile y los ajustes del servicio en Railway. Para automatizar más adelante toda la infraestructura se puede usar el sistema IaC vigente. [Dockerfiles](https://docs.railway.com/builds/dockerfiles) · [Volúmenes](https://docs.railway.com/volumes) · [Healthchecks](https://docs.railway.com/deployments/healthchecks) · [Transición de Config as Code](https://docs.railway.com/config-as-code)

## Correo en Railway Hobby

Usar Resend mediante HTTPS y un dominio propio verificado. **Railway bloquea SMTP saliente en Free, Trial y Hobby**; Gmail SMTP solo funcionará desde un entorno con SMTP permitido, como Railway Pro. No conviene pasar de USD 5 a USD 20 de mínimo mensual únicamente para usar Gmail. [Restricción oficial de Railway](https://docs.railway.com/networking/outbound-networking)

1. Registrar el dominio elegido. El dominio web y el de envío pueden compartir la misma zona DNS.
2. En Resend, abrir **Domains**, agregar el dominio o un subdominio de envío y publicar los registros DNS solicitados. Esperar la verificación.
3. En **API Keys**, crear una clave para Planilla y copiarla a RESEND_API_KEY en Railway Variables.
4. Configurar EMAIL_FROM con un remitente del dominio verificado, por ejemplo `Planilla <acceso@correo.tudominio.com.ar>`. Configurar SUPPORT_EMAIL con el Gmail del dueño para soporte. Dejar vacías las credenciales SMTP en Hobby.
5. Enviar una verificación de prueba y abrir el enlace desde otro navegador. Comprobar también un enlace vencido y un reenvío.

Resend requiere un dominio controlado por el dueño para enviar a usuarios reales; una dirección `@gmail.com` no reemplaza esa verificación. Su plan gratuito publica 3.000 mensajes mensuales con límite de 100 diarios. [Dominios verificados](https://resend.com/docs/dashboard/domains/introduction) · [Precios de Resend](https://resend.com/pricing)

La alternativa SMTP del código queda disponible para ejecución local o un alojamiento que lo permita. En Gmail requiere verificación en dos pasos y contraseña de aplicación dedicada; no se usa la contraseña habitual. Si se elige Railway Pro, redeplegar luego de cambiar de plan para habilitar SMTP. [Ayuda oficial de Google](https://support.google.com/accounts/answer/185833?hl=es)

## Mercado Pago

1. Crear o seleccionar la aplicación del comercio en Mercado Pago Developers.
2. Configurar el checkout con la URL HTTPS pública de Planilla.
3. Configurar el endpoint de Webhooks `https://TU-DOMINIO/api/webhooks/mercadopago`, seleccionar el evento de órdenes de Checkout Pro y copiar su clave secreta a Railway Variables.
4. Completar credenciales de producción y vendedor. El precio y la moneda se generan desde el servidor.
5. Hacer las compras de prueba con las cuentas y tarjetas que provee Mercado Pago. Recorrer aprobado, rechazado y pendiente.
6. Confirmar que visitar manualmente la URL de retorno, reenviar un webhook o cambiar el importe del navegador no entrega un archivo.
7. Habilitar el cobro real solo tras verificar la configuración completa y el flujo integrado.

Checkout Pro permite pagar como invitado. El servidor consulta la orden notificada y comprueba el resultado antes de conceder acceso. [Checkout Pro](https://www.mercadopago.com.ar/developers/es/docs/checkout-pro-orders/overview?scope=prod) · [Notificaciones](https://www.mercadopago.com.ar/developers/es/docs/checkout-pro-orders/notifications?scope=prod)

## AGPL y código correspondiente

El repositorio contiene el código y los archivos de construcción bajo AGPL v3 o posterior. En Railway, `/source` usa automáticamente `RAILWAY_GIT_COMMIT_SHA` para enlazar la revisión desplegada. En otro alojamiento, o para sobrescribir ese enlace, configurar `SOURCE_URL` con el commit exacto. Comprobar el enlace después de publicar. Conservar LICENSE, NOTICE y los avisos de las dependencias y datos de OCR.

Un ejemplo de SOURCE_URL es https://github.com/OWNER/REPOSITORY/tree/COMMIT. Reemplazar OWNER, REPOSITORY y COMMIT por valores reales. La revisión debe incluir los archivos necesarios para construir y ejecutar la aplicación. Los secretos, documentos privados y bases de operación quedan en el entorno del servicio.

## Verificación de publicación

- La ruta de salud responde y la web usa HTTPS.
- Cargar y corregir un PDF funciona sin correo previo.
- Un original ya rellenable muestra el aviso y conserva el gratuito.
- Un correo verificado habilita la primera descarga elegible de hasta dos páginas.
- Repetir el mismo clic o usar dos pestañas no duplica consumos ni cobros.
- Un trabajo pago solo se descarga tras confirmar la orden correcta.
- Los estados pendiente, aprobado, rechazado, cancelado y vencido tienen mensajes y acciones concretas.
- Un enlace de otro usuario, una orden ajena y una ruta de archivo directo carecen de acceso.
- Reiniciar el servicio conserva órdenes y autorizaciones vigentes.
- Las métricas administrativas requieren autenticación y reflejan pagos confirmados una sola vez.
- La aplicación alojada protege o deshabilita el historial global de aprendizaje OCR del piloto local.
- El enlace de código fuente abre la revisión desplegada.
