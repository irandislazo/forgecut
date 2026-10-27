from .backend import BackendUnavailable, MediaBackend, NullBackend


def get_backend():
    """Devuelve el mejor backend disponible en esta plataforma."""
    from .ffmpeg_backend import FFmpegBackend
    b = FFmpegBackend()
    return b if b.available() else NullBackend()
