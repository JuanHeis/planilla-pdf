# Medición del costo de los PDF

La medición distingue trabajo observado, costo parcial estimado y costo mensual asignado por el dueño. No consulta la cuenta de Railway, no cobra y no cambia el precio de venta.

## Qué se registra

`usage.sqlite3`, en `PLANILLA_DATA_DIR`, conserva por operación: fecha, clase (`detect`, `render`, `prepare`, `download`), éxito o fallo, páginas, campos, bytes de entrada/salida, segundos de CPU del proceso y tiempo transcurrido. Los identificadores de sesión y trabajo se guardan como SHA-256 para agrupar la actividad. No se guardan PDF, nombres de archivo, etiquetas, correos, mensajes de excepción ni texto reconocido.

Los reintentos se registran porque consumen recursos, pero el denominador de costo por PDF cuenta trabajos distintos, no clics. Un trabajo puede tener actividad en más de un mes. Los fallos sin trabajo asignado también contribuyen al tiempo medido; se muestran como intentos/fallos, sin inventar un PDF identificado.

Los registros detallados vencen a los 90 días mediante `cleanup()`. Los importes mensuales ingresados se conservan 24 meses. Un mes antiguo que perdió detalle no sirve para comparar su factura con el número incompleto de documentos.

## Tres cifras diferentes

1. **CPU estimada:** segundos de CPU observados por la tarifa configurada. Es solo una parte del costo: excluye RAM, almacenamiento, salida de red, correo, comisiones e impuestos. Incluye CPU de los hilos del mismo proceso que corran durante la operación; no es una medición exacta del facturador del proveedor.
2. **Escenario mínimo Hobby por PDF:** USD 5 divididos por los trabajos observados del mes. Es una referencia para presupuestar el mínimo, no una factura ni una predicción del excedente. Hobby no suma USD 5 de consumo a otros USD 5 del plan: el mínimo incluye esos primeros USD 5 de recursos.
3. **Costo mensual asignado por PDF:** el dueño ingresa el costo de infraestructura que corresponde a ese mes y se divide por los trabajos distintos con actividad medida. El denominador incluye gratuitos y originales, además de pagos; el gasto incluye reintentos y fallos. Este promedio ayuda a fijar precio, pero no afirma cuánto costó cada archivo concreto.

Sin un costo mensual ingresado, el costo completo queda como no disponible. La app no deduce RAM consumida a partir del máximo de 1 GB del servicio, ni presenta tamaño del PDF como facturación de almacenamiento.

Los períodos son meses calendario UTC. Railway puede facturar por un ciclo con otras fechas: el dueño debe asignar el costo a un período comparable. La cuenta de Railway debe corresponder a Planilla o repartirse entre proyectos antes de cargar un importe. Descuentos, créditos, impuestos y otros proveedores deben tratarse consistentemente; no se convierte USD a ARS automáticamente.

## Integración del servidor

```python
from usage_meter import UsageMeter

meter = UsageMeter(data_dir=data_dir)
with meter.measure("detect", session=session_id, input_bytes=len(data)) as sample:
    result = detect_pdf(data)
    sample.job = job_id
    sample.pages = len(result["pages"])
    sample.fields = len(result["fields"])

report = meter.report()                 # mes actual
report = meter.report("2026-11")       # mes elegido
report = meter.set_monthly_actual("2026-11", "5.00")
meter.cleanup()
```

`sample` permite actualizar `job`, `session`, `pages`, `fields`, `input_bytes`, `output_bytes` y `success`. Una excepción marca fallo automáticamente y se vuelve a lanzar. Si una operación devuelve una respuesta de error sin lanzar excepción, el servidor debe marcar `sample.success = False`. Una falla al guardar telemetría se registra sin datos privados y no rompe una operación de PDF ni oculta su excepción original.

`set_monthly_actual()` y `report()` deben exponerse únicamente detrás de la autenticación administrativa. El importe debe ser el costo asignado al mes, no dinero pagado por un cliente. Las rutas y permisos pertenecen al servidor; el módulo no implementa HTTP.

En respuestas de descarga, `output_bytes` representa el tamaño preparado para enviar. No demuestra que el navegador recibió todos los bytes. Tampoco incluye necesariamente HTML, CSS, JavaScript, cabeceras y todo el tráfico de Railway.

## Tarifas usadas

Consulta del 6 de noviembre de 2026: Railway publica para servicios CPU USD 0,000463 por vCPU/minuto (aproximadamente USD 20 por vCPU/mes), RAM USD 10/GB/mes, volumen USD 0,15/GB/mes y egreso USD 0,05/GB. La app usa únicamente la tarifa de CPU para el cálculo automático; `PLANILLA_CPU_USD_PER_MINUTE` permite actualizarla. No se usan las tarifas distintas de las máquinas virtuales de Railway.

[Planes y recursos](https://docs.railway.com/pricing/plans) · [Cómo interpretar la factura](https://docs.railway.com/pricing/understanding-your-bill).
