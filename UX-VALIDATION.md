# Validación del flujo comercial

Fecha: 6 de noviembre de 2026.

La interfaz conserva el editor de PDF y agrega el flujo de descarga, correo, pago, recuperación y reclamos. La vista pública evita herramientas del piloto local de OCR. El servidor decide permisos y consumo; la interfaz muestra ese estado.

## Método y límites

- Pruebas manuales automatizadas con CUA en el navegador integrado, contra Flask y Commerce reales en un servidor local de pruebas.
- PDFs sintéticos en blanco; correos y respuestas de Mercado Pago simulados en el proveedor. No se enviaron correos externos ni se cobraron o devolvieron fondos reales.
- Escritorio en el tamaño normal del navegador y viewport móvil de 390 × 844. Se inspeccionaron capturas, árbol accesible, ancho de los contenedores y ancho de desplazamiento.
- Descargas gratuitas y del original comprobaron el evento de descarga y un archivo PDF guardado por el navegador.
- Esta matriz cubre el flujo comercial. La calidad de detección/OCR de los formularios y la integración externa se validan por separado.
- No se probó un teléfono físico, gestos táctiles reales, lectores de pantalla ni todos los niveles de zoom. No constituye certificación WCAG.

## Matriz observada

| Caso | Resultado observado |
|---|---|
| Configuración inaccesible, respuesta 503 | Elegir PDF, ejemplo y exportar deshabilitados; aviso claro de que no se envió el PDF ni se consumió descarga. No cambia al modo local. |
| Recuperar conexión | Restaurar el servicio en otra pestaña y pulsar Volver a intentar habilita el modo comercial sin recargar. |
| Correo inicial | Solicita correo sin contraseña; informa que todavía no se consume el gratuito. |
| Correo enviado | Muestra confirmación, carpeta de correo no deseado, reenviar y comprobar estado. |
| Confirmación de correo | Abrir el enlace de prueba restaura el PDF y ofrece Descargar gratis. |
| Primera descarga gratuita | Un clic habilita y descarga un PDF real; muestra éxito y posibilidad de reintento. |
| Corregir el mismo trabajo | Cambiar el nombre de un campo, preparar nuevamente y volver a descargar conserva la habilitación sin otro cobro. |
| PDF de tres páginas | Explica el precio de ARS 2.990, límite gratuito de dos páginas y que el gratuito sigue disponible para otro formulario. |
| Pago pendiente | Informa que espere si ya pagó; permite comprobar el estado y continuar la orden existente. No se abrió un pago real. |
| Pago rechazado | Muestra el motivo seguro del proveedor: saldo insuficiente, además de opciones para reintentar. |
| Pago aprobado | Ofrece descarga y formulario de reclamo. |
| Reclamo | Registrar un motivo elimina el formulario duplicado y confirma el registro sin prometer un correo automático inexistente. |
| Administración | Clave enviada por encabezado; el campo se vacía. Se ven métricas, importes y reclamos del servidor. La clave se conserva solo en memoria de la pestaña. |
| Devolución administrativa | Un diálogo propio identifica orden y correo; al confirmar, el proveedor simulado devuelve éxito, actualiza importe devuelto y estado del reclamo, y elimina el botón duplicado. |
| Original ya rellenable | Muestra sus campos existentes y permite descargar el original sin conversión. El navegador guardó el archivo. |
| Agregar un campo al original | Agregar Texto con teclado cambia al flujo normal de conversión; no obtiene gratis campos nuevos por ser un original rellenable. |
| Trabajo vencido | Muestra aviso de vencimiento y permite subir un PDF nuevo. |
| Enlace inválido o vencido | Abre recuperación por correo y explica que abrir el enlace no consume descargas. |
| Recuperación sin coincidencias | Respuesta genérica que no revela si un correo tiene trabajos. |
| Lista de recuperación | Muestra enlaces con fecha, páginas y estado, sin nombres de PDF ni contenido. Permite buscar con otro correo. El vencimiento se toma del servidor. |
| Móvil | Editor con tres acciones superiores, descarga, reclamo, recuperación y administración legibles; sin desplazamiento horizontal de la página. Las tablas amplias desplazan dentro de su contenedor. |

## Impeccable

Se aplicó la habilidad local `C:/Users/Juan/.agents/skills/impeccable/SKILL.md`, con contexto de `static/index.html`, el modo Operate y el piso de calidad de `reference/craft-floor.md`. Se preservaron la identidad visual y los controles del editor. Los cambios priorizan estados claros, acciones concretas y recuperación de errores.

Comandos de contexto y análisis de fuente:

```powershell
& C:/Users/Juan/.agents/skills/impeccable/scripts/impeccable.cmd context --target static/index.html
& C:/Users/Juan/.agents/skills/impeccable/scripts/impeccable.cmd detect static/index.html static/admin.html --json
```

El análisis de fuente final detectó dos advertencias `cramped-padding`, una por tabla administrativa. Son falsos positivos del contenedor: cada celda tiene `padding: 10px 15px`; las capturas muestran separación suficiente. La pantalla principal no produjo hallazgos. No se ejecutó un análisis automático de contraste en navegador.

También pasaron `node --check` para `app.js`, `checkout.js` y `admin.js`, y `git diff --check` de los archivos de interfaz.

## Evidencia

Las capturas definitivas se guardaron en `work/commerce-ui-cua/` del espacio de trabajo, para entregar en `outputs/ux-validacion/`. Incluyen:

- `pago-tres-paginas-movil.jpg`
- `pago-aprobado-movil.jpg`
- `original-editor-movil.jpg`
- `recuperacion-listado-movil.jpg`
- `enlace-vencido-recuperacion.jpg`
- `servicio-sin-conexion-movil.jpg` y `servicio-recuperado-movil.jpg`
- `actividad-movil.jpg` y `actividad-escritorio.jpg`
- `devolucion-confirmacion-movil.jpg`, `devolucion-solicitada-movil.jpg` y `devolucion-resumen-movil.jpg`

Se descartaron las capturas móviles iniciales producidas con recorte: la herramienta provocaba un reflujo incorrecto durante la captura. Las definitivas se obtuvieron con `getScreenshot()`, verificando el diseño visible y los anchos antes de guardarlas. Una segunda revisión inspeccionó las capturas reemplazadas.

## Pendiente con credenciales reales

Verificar entrega del correo desde un dominio habilitado, vuelta del checkout y webhook de Mercado Pago, un cobro y una devolución de prueba del proveedor, y el flujo completo en el dominio desplegado de Railway. Estas verificaciones no se reemplazan por el proveedor simulado.
