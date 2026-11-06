# Editor de formularios PDF

En Windows, descomprimí el ZIP y abrí **Iniciar.cmd**. Requiere Python 3.10 o posterior disponible como comando python. La primera ejecución necesita Internet para instalar PyMuPDF dentro de la carpeta de la app. El navegador abre el editor local. Para detenerlo, cerrá la ventana que ejecuta Iniciar.cmd.

Podés empezar con el formulario de ejemplo o elegir un PDF. Revisá cada página, seleccioná y mové los campos con el mouse o las flechas, o agregá campos manualmente. Descargá el PDF y probá completarlo en tu lector habitual. El archivo original se conserva.

Si el PDF ya trae campos rellenables, Planilla los cuenta y los muestra con un contorno violeta. Esos controles se conservan al exportar. Podés ocultar sus marcas mientras corregís los campos nuevos.

El motor busca líneas, guías punteadas, recuadros con o sin contorno y casillas. En páginas dominadas por una imagen y con poco texto digital, el OCR en español ayuda a descartar campos sobre texto impreso. El modelo viene incluido y se ejecuta en tu equipo. Los escaneos todavía pueden requerir más correcciones. Usá una copia sin cifrar y sin firma digital. El editor agrega texto y casillas, y conserva los campos interactivos que ya estaban en el archivo.

El servidor solo escucha en tu equipo. Mantiene hasta cuatro documentos en memoria mientras está abierto y descarta las sesiones inactivas después de 30 minutos al recibir la siguiente solicitud. El límite de carga es 20 MB y 50 páginas. Cerrá el servidor cuando termines. Los cambios de edición pertenecen a la sesión abierta en el navegador, así que descargá antes de cerrar o recargar la pestaña.

Este paquete corresponde al prototipo local. Las funciones de acceso por cuenta y cobro quedan para etapas posteriores.

## Revisar OCR y compartir correcciones

Desde la pantalla de inicio, abrí **Revisar OCR** y cargá un PDF. La página muestra lo que leyó Tesseract, una sugerencia cuando existe y el texto que podés corregir. Podés aceptar una sugerencia o escribir tu propia corrección.

Guardá cada página que hayas revisado. Si la lectura está bien, guardala sin cambios para registrarla como aprobada. Podés volver a abrir el mismo PDF y continuar desde el historial guardado. Las correcciones confirmadas ayudan a generar sugerencias en las próximas páginas.

Para pasárselo a otra persona, compartí el ZIP de Planilla y pedile que lo descomprima y abra **Iniciar.cmd**. Cuando termine de revisar, pedile que use **Descargar aprendizaje**. En tu copia de Planilla, usá **Importar ZIP** con el archivo que recibiste. Se incorporan las revisiones sin duplicarlas si importás dos veces el mismo archivo.

El ZIP de aprendizaje contiene el historial de revisiones y los pares de texto original y confirmado. No contiene los PDFs ni sus imágenes. Revisá si esas palabras incluyen datos que prefieras mantener en tu equipo antes de compartirlo. El modo de revisión guarda las correcciones para el OCR y deja el PDF como referencia visual.
