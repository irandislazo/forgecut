# ForgeCut

Editor de vídeo estilo **DaVinci Resolve** (páginas Media · Cut · Edit · Plantillas · Deliver) hecho 100 % en Python con **Flet**,
pensado para PC y Android. Su función estrella: **crear automáticamente el timeline inicial desde una plantilla**
(La Voz o Got Talent), dejar **huecos de lipsync** listos y **exportar el audio de cada hueco** para generarlos a mano;
después editas ese timeline como en cualquier editor.

## Ejecutar (escritorio)
```bash
pip install -r requirements.txt
# ffmpeg debe estar en el PATH (o: pip install imageio-ffmpeg, o variable FORGECUT_FFMPEG)
cd src && flet run main.py
```
Tests (sin Flet; usan ffmpeg real): `python -m unittest discover -s tests -v`

## Flujo de trabajo
1. **Plantillas** → crea una plantilla, elige estilo y sube tus clips (reacciones, jueces antes/girando/girado, lejanos).
2. Elige la canción → **Generar timeline**: V1 = clips + huecos `LIPSYNC nn`, A1 = canción, marcadores en cada hueco.
3. **Deliver → Exportar audio**: un archivo mp3/wav por hueco (o solo los clips seleccionados) + `manifest.json`.
4. Genera los lipsync fuera, impórtalos en **Media**, suéltalos en el hueco (Edit) y renderiza en **Deliver**.

## Edición estilo DaVinci (Hito 2, sin probar con Flet real)
| Gesto | Cómo |
|---|---|
| Multiselección | **Ctrl+clic** (suma/quita), **marquesina** arrastrando en zona vacía, **Ctrl+A** |
| Mover | arrastra un clip (o todo el grupo seleccionado) en 2 ejes; **imán** a bordes/marcadores/playhead con **línea amarilla**; sobrescribe lo de debajo |
| Recortar | arrastra el borde del clip (también con imán) |
| Cuchilla (B) | línea de corte que sigue al ratón, se pega a bordes (amarillo = imán); clic corta el clip bajo el cursor |
| Soltar medios | arrastra desde el Media Pool hasta el timeline y suéltalo donde quieras |
| Audio | botón **Exportar audio selección** (mp3/wav) → un archivo por clip seleccionado + `manifest.json` |
| Visual | miniaturas dentro de los clips de vídeo y **forma de onda** en los de audio (caché en `~/.forgecut/cache/visuals`) |
| Reproducción | un ffmpeg persistente por clip (`media/stream.py`); **sin audio todavía** |

## Reglas del timeline La Voz (v3 + nueva)
Intro solo de reacciones → **lipsync obligatorio justo después del intro** → los 4 jueces giran una vez cada uno
(nunca clips "girado" antes ni "antes" después de girar) · nunca el mismo juez ni archivo seguidos · sin dos lejanos seguidos ·
máx. 3 clips seguidos sin lipsync en la 1ª mitad · los archivos solo se repiten al agotarlos. Lejanos de duración configurable (2.5–3 s).

## Arquitectura
```
src/forgecut/
  core/        modelo (frames enteros), operaciones de edición, historial, guardado   ← sin UI ni ffmpeg
  automation/  plantillas, planificadores La Voz / Got Talent, construcción del timeline, export de audio
  media/       MediaBackend (contrato) · FFmpegBackend (escritorio) · renderer (filtergraph único)
  ui/          compat.py (TODO lo que cambia entre versiones de Flet) · state · widgets · pages
```
La UI solo habla con `AppState`; el motor no sabe que existe una UI. Cambiar de backend (p. ej. móvil) no toca nada más.

## Estado honesto (Hito 1)
| Área | Estado |
|---|---|
| Motor (edición, plantillas, planificadores, audio, render, escenas, edición de grupo, miniaturas, onda, stream) | ✅ 72 tests, ffmpeg real |
| UI Flet | ⚠️ escrita y probada con un Flet simulado; **falta primera ejecución con Flet real** (ajustes menores esperables, concentrados en `ui/compat.py`) |
| Vista previa | Frame bajo el playhead + reproducción aprox. 8–12 fps, **sin audio** |
| **Android** | ⚠️ modo editor: importar, Media Pool, timeline, plantillas y guardar funcionan sin ffmpeg; vista previa/exportación necesitan backend móvil (ver `docs/BUILD.md` y ROADMAP) |

Ver `docs/ROADMAP.md`.

## Novedades (v8)
- **Media Pool estilo DaVinci**: carpetas anidadas, **selección múltiple** (clic, Ctrl+clic, Shift+clic para rango, casilla en cada fila y botón ☑ para táctil), **mover varios medios y carpetas a la vez** (arrastrando, o «Mover a…»), renombrar, eliminar (avisa si el medio está en el timeline), **búsqueda** en todo el pool, ordenar, vista **lista/miniaturas**, y arrastrar varios medios al timeline (se colocan en secuencia). Corrige el panel en blanco (causa: `Row(wrap=True)` con un hijo `expand=True`; ahora ninguna fila puede vaciar el panel).
- **Importar sin ffmpeg**: lector de metadatos en Python puro (`media/pure_probe.py`: MP4/MOV, MKV/WebM, AVI, WAV, MP3, FLAC, OGG, imágenes) → Android puede importar y editar.
- **Compilación**: workflow de GitHub Actions para Android (APK por arquitectura) y Windows (con ffmpeg incluido) + Release automática; scripts en `scripts/` y guía en `docs/BUILD.md`.

## Novedades (v7)
- **Multiselección que ya no se pierde**: pulsar un clip que forma parte del grupo conserva el grupo (se arrastra entero); un clic sin arrastre lo deja solo (como DaVinci). Ctrl+clic, marquesina y el botón ☑ (Ctrl fijo) funcionan igual.
- **Carpetas en el Media Pool**: crear/renombrar/borrar, importar dentro de la carpeta abierta, mover arrastrando sobre la carpeta o con el botón «Mover».
- **Imán del playhead** al arrastrar la regla (bordes de clips y marcadores) + saltos de punto de edición (↑/↓) y botones pequeños de ±1/±10 frames (←/→, Mayús).
- **Inspector táctil**: deslizable con − / + y ↺ para Zoom, Posición X/Y, Rotación, Opacidad, Recorte y Volumen.
- **Diseño adaptable**: bajo 900 px de ancho (móvil) se muestra una zona a la vez (Medios · Visor · Inspector) y el visor se ajusta al ancho.
