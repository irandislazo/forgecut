# Media Pool: uso y estado de arrastrar archivos

- ForgeCut abre ahora en la página **Media**, para que los controles de importar y crear carpetas estén visibles al iniciar.
- Usa **Nueva carpeta** para crear un bin en la raíz o dentro de la carpeta abierta.
- Usa **Importar archivos** para elegir varios vídeos, audios e imágenes. Los nuevos medios se registran en la carpeta que esté abierta.
- Las carpetas existentes se abren haciendo clic; desde dentro puedes volver con **Subir un nivel**.
- Las casillas permiten seleccionar varios medios; **Mover selección** permite moverlos a otra carpeta.
- El arrastre interno de medios a carpetas y al timeline es distinto del arrastre desde el Explorador de Windows.

## Arrastrar archivos desde el Explorador

El `DragTarget` integrado de Flet acepta elementos `Draggable` de la propia interfaz; no recibe de forma nativa archivos externos del Explorador de Windows. Para habilitarlo en escritorio se necesita integrar y probar un complemento de file-drop basado en `desktop_drop` (o una extensión Flet compatible). Esto debe ser opcional y específico de escritorio para no afectar Android. En Android debe mantenerse **Importar archivos** mediante el selector nativo.
