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
| Correo que enviará mensajes | Cuenta de Gmail con verificación en dos pasos o dominio verificado de un proveedor transaccional | EMAIL_FROM y credenciales del transporte elegido. |
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

La documentación consultada el 6 de noviembre de 2026 indica que railway.json y railway.toml están deprecados para servicios nuevos. Esta entrega configura la construcción con Dockerfile y los ajustes del servicio en Railway. Para automatizar más adelante toda la infraestructura se puede usar el sistema IaC vigente. [Dockerfiles](https://docs.railway.com/builds/dockerfiles) · [Volúmenes](https://docs.railway.com/volumes) · [Healthchecks](https://docs.railway.com/deployments/healthchecks) · [Transición de Config as Code](https://docs.railway.com/config-as-code)

## Correo con Gmail para el piloto

Gmail puede servir como transporte inicial de un piloto pequeño. La cuenta debe tener verificación en dos pasos y permitir contraseñas de aplicación. El dueño crea una contraseña dedicada a Planilla y la configura como secreto. La contraseña habitual de la cuenta queda fuera de la aplicación.

1. Abrir la Cuenta de Google y entrar en **Seguridad**.
2. Activar **Verificación en dos pasos**.
3. Abrir **Contraseñas de aplicación**, crear una para Planilla y copiarla una sola vez a Railway Variables.
4. Configurar SMTP_HOST=smtp.gmail.com, SMTP_PORT=465, SMTP_USERNAME con la dirección de Gmail y SMTP_PASSWORD con la contraseña de aplicación. Este transporte usa SMTP sobre TLS. Configurar EMAIL_FROM con esa misma cuenta o un remitente autorizado por ella.
5. Enviar una verificación de prueba y abrir el enlace desde otro navegador. Comprobar también un enlace vencido y un reenvío.

La opción de contraseñas de aplicación puede faltar en algunas cuentas de organizaciones, Protección Avanzada o configuraciones basadas solo en llaves de seguridad. Para un dominio propio se puede usar Resend y verificar el remitente. [Ayuda oficial de Google](https://support.google.com/accounts/answer/185833?hl=es)

## Mercado Pago

1. Crear o seleccionar la aplicación del comercio en Mercado Pago Developers.
2. Configurar el checkout con la URL HTTPS pública de Planilla.
3. Configurar el endpoint de Webhooks que expone esta versión y copiar su clave secreta a Railway Variables.
4. Completar credenciales de producción y vendedor. El precio y la moneda se generan desde el servidor.
5. Hacer las compras de prueba con las cuentas y tarjetas que provee Mercado Pago. Recorrer aprobado, rechazado y pendiente.
6. Confirmar que visitar manualmente la URL de retorno, reenviar un webhook o cambiar el importe del navegador no entrega un archivo.
7. Habilitar el cobro real solo tras verificar la configuración completa y el flujo integrado.

Checkout Pro permite pagar como invitado. El servidor consulta la orden notificada y comprueba el resultado antes de conceder acceso. [Checkout Pro](https://www.mercadopago.com.ar/developers/es/docs/checkout-pro-orders/overview?scope=prod) · [Notificaciones](https://www.mercadopago.com.ar/developers/es/docs/checkout-pro-orders/notifications?scope=prod)

## AGPL y código correspondiente

El repositorio contiene el código y los archivos de construcción bajo AGPL v3 o posterior. Antes de cada despliegue, guardar el commit que se publicará y configurar SOURCE_URL con su enlace permanente. La web debe mostrar ese enlace para que los usuarios puedan obtener la misma versión del programa. Conservar LICENSE, NOTICE y los avisos de las dependencias y datos de OCR.

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
