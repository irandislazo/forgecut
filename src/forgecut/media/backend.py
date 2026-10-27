"""Contrato de medios. La UI y el motor solo hablan con esta interfaz.

Escritorio  → FFmpegBackend (binario ffmpeg/ffprobe).
Android     → hoy NO se pueden lanzar procesos desde la app: hará falta un backend propio
              (p. ej. ffmpeg-kit como extensión de Flet). Mientras tanto get_backend() devuelve
              NullBackend, que falla con un mensaje claro en vez de romper la app.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Protocol


class MediaBackend(Protocol):
    name: str

    def available(self) -> bool: ...
    def probe(self, path: str) -> Dict: ...
    def frame_jpeg(self, path: str, t_s: float, width: int = 640) -> bytes: ...
    def detect_scenes(self, path: str, threshold: float = 0.3) -> List[float]: ...
    def waveform(self, path: str, out_png: str, height: int = 64) -> str: ...
    def filmstrip(self, path: str, out_png: str, duration_s: float, height: int = 40) -> str: ...
    def open_stream(self, path: str, start_s: float, fps: float, width: int): ...
    def export_audio(self, src: str, start_s: float, dur_s: float, out_path: str,
                     fmt: str = "mp3") -> str: ...
    def render(self, project, out_path: str, progress: Optional[Callable[[float], None]] = None,
               proxy: bool = False, short_side: Optional[int] = None, crf: Optional[int] = None) -> str: ...


class BackendUnavailable(RuntimeError):
    pass


class NullBackend:
    name = "none"

    def available(self) -> bool:
        return False

    def _fail(self, *a, **k):
        raise BackendUnavailable(
            "No hay motor de medios disponible en esta plataforma (ffmpeg no encontrado). "
            "En escritorio instala ffmpeg; en Android falta el backend móvil.")

    frame_jpeg = detect_scenes = export_audio = render = _fail

    def probe(self, path: str) -> Dict:
        """Sin ffmpeg sí se puede IMPORTAR (metadatos en Python puro): editar y planificar timelines funciona."""
        from .pure_probe import probe_pure
        return probe_pure(path)
    waveform = filmstrip = open_stream = _fail
