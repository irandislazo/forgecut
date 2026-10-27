# Hoja de ruta

## Hito 1 (hecho) — motor + UI base
Modelo en frames, edición estilo DaVinci, plantillas, planificadores, audio por huecos, render, auto-corte de escenas, UI de 5 páginas.

## Hito 2 — UI real y vista previa
- Primera ejecución con Flet real; ajustar `ui/compat.py`.
- Vista previa en tiempo real: decodificador persistente (1 ffmpeg por clip, pipe rawvideo) + `ft.RawImage`; audio con `flet-audio`.
- ✅ (escrito, pendiente de probar con Flet real) Arrastre entre pistas, multiselección Ctrl+clic y marquesina, miniaturas y forma de onda, cuchilla con imán, soltar desde el Media Pool, decodificador persistente.
- ⏳ Audio en la reproducción (`flet-audio` o mezcla con ffplay), soltar archivos desde el explorador del sistema operativo (Flet no lo expone: hay que verificarlo en la versión real).
- Soltar un vídeo generado sobre un hueco `LIPSYNC` (reemplazo con un gesto).

## Hito 3 — Android (el punto difícil)
Android no permite lanzar procesos desde la app, así que `FFmpegBackend` no sirve allí. Opciones, de más a menos realista:
1. **ffmpeg-kit** (Flutter) como extensión de Flet (`flet-ffmpeg`): implementar `MediaBackend` llamando a ffmpeg-kit.
2. **PyAV** compilado para Android (si hay wheel/receta serious-python) para probe, frames y audio; render con OpenGL/MediaCodec.
3. Híbrido: en móvil solo editar/generar timeline y **render remoto** en el PC.
`get_backend()` ya devuelve `NullBackend` con mensaje claro mientras tanto, y la UI funciona sin él (editar timeline, plantillas).

## Hito 4 — pulido profesional
Curvas de volumen, transiciones básicas (fundido), texto/títulos, color básico, exportar XML/EDL a DaVinci, plantillas con vídeo de intro.
