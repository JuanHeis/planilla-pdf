# Dominios y alojamiento de Planilla

Investigación del 6 de noviembre de 2026. Precios publicados, sujetos a cambios. USD y ARS se muestran separados; no se suman impuestos argentinos, percepciones ni un tipo de cambio supuesto. No se compró ningún dominio ni se modificó una cuenta de hosting.

## Recomendación

Para probar este sideproject: **Planilla PDF**, dominio candidato **planillapdf.com.ar**, Railway Hobby con una sola aplicación y volumen, Resend gratuito por HTTPS y Gmail para soporte. Presupuestar inicialmente **USD 5–10 mensuales más ARS 8.500 anuales de dominio**, sujeto al consumo medido. El dominio de Railway sirve para pruebas; el dominio propio también permite autenticar el correo de acceso.

El `.com` puede convenir por marca y expansión a otros países si cuesta la tarifa normal. No hace falta pagarlo para conseguir una ventaja de ranking. No pagaría un dominio premium para validar esta idea.

## Precios de dominios

Tarifas para nombres estándar; un nombre premium puede tener otro precio. Confirmar el importe final y la disponibilidad antes de pagar. Una promoción de alta no fija la renovación del año siguiente.

| Extensión / registrador | Alta por un año | Renovación anual publicada | Evaluación para este producto |
|---|---:|---:|---|
| `.com.ar`, NIC Argentina | ARS 8.500 | ARS 8.500 | Primera opción para el foco argentino. |
| `.ar`, NIC Argentina | ARS 25.500 | ARS 25.500 | Más corto; cuesta tres veces el `.com.ar`. Lo elegiría solo por un nombre mucho mejor. |
| `.com`, Cloudflare Registrar | USD 11,17 | USD 11,17 | Buena opción si se quiere una marca internacional. |
| `.net`, Cloudflare Registrar | USD 11,86 | USD 11,86 | No aporta una mejora clara frente al `.com`. |
| `.app`, Cloudflare Registrar | USD 8,20 promocional; tarifa normal USD 14,20 | USD 14,20 | Presentación de producto, con renovación mayor. |
| `.online`, Cloudflare Registrar | USD 4,99 promocional; tarifa normal USD 27,70 | USD 27,70 | El alta barata oculta un coste recurrente alto para este caso. |

Fuentes: [NIC Argentina, aranceles](https://nic.ar/dominios/aranceles) y [Cloudflare, tabla de alta y renovación](https://pricing.registrar.cloudflare.com/). Cloudflare publica venta al costo y DNS incluido; para un dominio registrado allí se usan sus servidores DNS. [Condiciones de Cloudflare Registrar](https://developers.cloudflare.com/registrar/get-started/register-domain/)

## Nombre y SEO

Google indica que las palabras clave del dominio por sí solas tienen muy poco efecto y que el TLD importa principalmente como señal geográfica, normalmente de impacto bajo. El `.com` no recibe un premio general frente a otras extensiones. Un dominio argentino puede ayudar a comunicar el país al usuario y al buscador. [Guía oficial de SEO de Google](https://developers.google.com/search/docs/fundamentals/seo-starter-guide#things-we-believe-you-shouldnt-focus-on) · [Preguntas de ranking de Google](https://developers.google.com/search/help/site-position-in-search-faq#top-level-domains)

Estas son **propuestas de nombre; su disponibilidad registral no fue verificada**. Tampoco representan una medición de volumen de búsquedas.

| Candidato | Razón para considerarlo | Límite |
|---|---|---|
| `planillapdf.com.ar` | Conserva Planilla y aclara el tipo de archivo. Preferido para marca. | Necesita explicar que crea campos rellenables. |
| `pdfrellenable.com.ar` | Describe directamente el resultado. | “Rellenable” puede ser menos familiar que “editable”. |
| `formularioseditables.com.ar` | Fácil de entender para el público de formularios. | Es largo y “editable” también puede significar modificar el texto del PDF. |
| `formulariofacil.com.ar` | Fácil de pronunciar y recordar. | Es menos específico y debe acompañarse de una descripción clara. |
| `planillapdf.com` | Misma marca para ampliar el público. | Revisar disponibilidad y tarifa estándar; no asumir que está libre. |

Usaría el nombre visible **Planilla PDF** y el título **“Convertí formularios PDF en rellenables”**. La página debe explicar con un ejemplo visual que agrega campos y conserva el documento original. Eso resuelve mejor la intención que llamar al servicio “OCR” o prometer edición completa del contenido.

Para captación, priorizar una página principal útil y ejemplos reales de formularios compatibles, con enlaces a sus originales oficiales. Evitar que el nombre o el diseño sugieran que el sitio pertenece a ANSES, ARCA u otro organismo. Si más adelante se compra un segundo dominio, redirigirlo al principal y concentrar allí el contenido.

## Railway: tarifas y fórmula

Hobby tiene un **mínimo de USD 5 al mes**, que incluye USD 5 de consumo; no se pagan USD 5 adicionales sobre el uso. Pro tiene mínimo USD 20. Las tarifas aproximadas publicadas son RAM USD 10/GB-mes, CPU USD 20/vCPU-mes, volumen USD 0,15/GB-mes y salida de red USD 0,05/GB. Los máximos de recursos del plan no son recursos gratis. [Planes y tarifas oficiales](https://docs.railway.com/pricing/plans)

Para un mes de 30 días, un modelo simple es:

`USD/mes ≈ max(5, RAM_media_GB × 10 + CPU_segundos × 20 / 2.592.000 + volumen_GB × 0,15 + salida_GB × 0,05)`

Es una aproximación para servicios; no usa la tarifa distinta de Sandboxes/VMs. El cálculo real sigue la medición del proveedor. La RAM que permanece ocupada todo el día suele pesar más que unos segundos de CPU por documento. [Desglose de la factura](https://docs.railway.com/pricing/understanding-your-bill) · [Precios públicos](https://railway.com/pricing)

## Escenarios ilustrativos

**Estos consumos son supuestos para presupuestar, no benchmarks del motor actual.** En todos se suponen 60 segundos de CPU total por archivo, incluyendo el trabajo adicional de renderizar/reintentar. El número de formularios no determina por sí solo la RAM ni el tráfico: dependen del tamaño, páginas, OCR y cantidad de visitas a la vista previa.

| Escenario / mes | Archivos supuestos | RAM media supuesta | CPU total | Volumen medio | Salida | Consumo calculado | Factura Hobby aproximada |
|---|---:|---:|---:|---:|---:|---:|---:|
| Piloto pequeño | 100 | 0,25 GB | 6.000 s | 0,5 GB | 1 GB | USD 2,67 | **USD 5** |
| Uso moderado | 1.000 | 0,50 GB | 60.000 s | 0,5 GB | 10 GB | USD 6,04 | **USD 6,04** |
| Más actividad | 5.000 | 1 GB | 300.000 s | 2 GB | 50 GB | USD 15,11 | **USD 15,11** |

Como presupuesto prudente: USD 5–10 al empezar y USD 10–20 con más actividad, ajustándolo con una semana de métricas reales. No es un techo garantizado ni una promesa de capacidad: un solo trabajador procesa secuencialmente y puede crear esperas aunque el coste siga bajo. PDFs escaneados grandes pueden tener picos de memoria muy superiores al promedio. La retención y las copias de seguridad deben entrar en la medición de almacenamiento.

Empezaría con un único servicio y SQLite en volumen; no agregaría Postgres o Redis solo para este piloto. Mantener 1 réplica y los límites de páginas/tamaño ya definidos. El precio de cada conversión paga trabajo del servicio; Tesseract no añade una tarifa de API y no se presupuestó revisión por LLM.

## Correo y costes adicionales

**Gmail SMTP no funciona en Railway Hobby:** Railway lo bloquea en Free, Trial y Hobby, y lo habilita desde Pro. No conviene subir al mínimo de USD 20 únicamente para eso. Usar la API HTTPS de Resend con un dominio propio verificado; Gmail puede seguir recibiendo los reclamos. [Restricción oficial de SMTP](https://docs.railway.com/networking/outbound-networking) · [Verificación de dominio en Resend](https://resend.com/docs/dashboard/domains/introduction)

Resend publica un plan gratuito de 3.000 correos mensuales y 100 diarios. Ese límite debe contar verificaciones, reenvíos y recuperación, no solo clientes. Si se supera, revisar el plan vigente antes de activar sobrecostes. [Precios oficiales de Resend](https://resend.com/pricing)

Mercado Pago, impuestos, devoluciones, tiempo de soporte y eventualmente un proveedor de correo pago quedan fuera de los números del servidor. No convertir automáticamente USD a ARS sin el tipo de cambio y la forma real de pago del dueño.

Con la venta propuesta de ARS 2.990, calcular la viabilidad así:

`ventas necesarias = techo((hosting_USD × cambio_efectivo + dominio_ARS / 12 + otros_costes_ARS) / margen_neto_por_venta_ARS)`

El margen neto descuenta comisión real de Mercado Pago, procesamiento e incidencia de devoluciones. El dominio `.com.ar` representa ARS 708,33 mensuales si se reparte su costo anual; no es un cobro mensual de NIC.

## Control con poco mantenimiento

Durante la primera semana medir RAM media y pico, segundos de CPU por página, egress de previsualizaciones, crecimiento del volumen y errores por timeout. Correlacionar con archivos procesados, descargas gratis, ventas confirmadas y reclamos. Después reemplazar los supuestos de la tabla.

Como configuración inicial propuesta: alerta de consumo a USD 7 y límite duro a USD 15, revisados por el dueño. El límite duro **apaga el servicio** cuando se alcanza; no equivale a limitar velocidad y puede interrumpir pagos/descargas, así que debe elegirse conscientemente. Se configura en **Workspace Usage → Set Usage Limits**; los límites de Compute y Railway Agent son separados. [Controles oficiales de costes](https://docs.railway.com/pricing/cost-control)

No activaría reposo automático antes de probar enlaces de correo y retorno de Mercado Pago con arranque en frío. Primero preservar una experiencia de descarga confiable y medir si el ahorro justifica ese cambio.

La nueva cuenta de Railway y la elección del dominio siguen pendientes del dueño. Esta investigación no habilita pagos ni compra recursos.
