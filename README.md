# Planilla PDF

Convierte formularios PDF en documentos rellenables. Detecta líneas, recuadros y casillas, ayuda con OCR en escaneos y permite agregar, mover y ajustar campos antes de exportar un AcroForm.

El motor hace sugerencias que deben revisarse. No garantiza encontrar todos los campos ni modifica el texto impreso del documento.

## Ejecutar localmente

En Windows: instalar Python 3.10 o posterior y abrir `Iniciar.cmd`. En otros sistemas:

```sh
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python server.py --open --port 0
```

La revisión de OCR y la exportación/importación de aprendizaje se usan en el modo local. [Guía de uso](LEEME.md).

## Versión web

Incluye correo verificado sin contraseña, primera conversión gratuita de hasta 2 páginas y pago único de ARS 2.990 para los siguientes trabajos de hasta 5 páginas. Se prepara el archivo antes del cobro. Los originales que ya tienen campos rellenables se pueden descargar sin consumir el gratuito.

Las autorizaciones y archivos privados se guardan en SQLite y un volumen persistente. El servidor comprueba las notificaciones de Mercado Pago; una URL de retorno no concede acceso. Un trabajo habilitado permite volver a exportar correcciones por 24 horas y descargar por 7 días. Hay recuperación por correo, reclamos y un panel administrativo privado en `/static/admin.html`.

[Requisitos y criterios](REQUIREMENTS.md) · [Publicación y credenciales](DEPLOY.md) · [Dominios y costes estimados](DOMAIN-HOSTING.md).

El modo web requiere configurar los proveedores antes de aceptar clientes. Las pruebas automatizadas usan proveedores simulados y no realizan cobros ni envían mensajes reales.

## Verificación

```sh
python -m unittest discover -s tests -v
```

## Licencia

GNU AGPL v3 o posterior. La aplicación ofrece el código correspondiente a la revisión desplegada desde `/source`. Ver [LICENSE](LICENSE) y [NOTICE](NOTICE). Los PDFs privados, secretos y bases de operación no forman parte del repositorio.
