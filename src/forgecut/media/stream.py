"""Decodificador persistente para la reproducción: UN ffmpeg por clip que entrega JPEG por pipe.

En vez de lanzar un ffmpeg por fotograma (lento: ~8–12 fps), se arranca uno en el punto de inicio y se van
leyendo los frames a ritmo de reproducción desde un hilo. `get(n)` devuelve el frame n-ésimo desde el inicio
(descartando los anteriores si la UI va por delante/atrás de lo decodificado).
"""
from __future__ import annotations

import subprocess
import sys
import threading
from collections import deque
from typing import Optional

_NOWIN = {"creationflags": 0x08000000} if sys.platform.startswith("win") else {}
_SOI, _EOI = b"\xff\xd8", b"\xff\xd9"


class FrameStream:
    def __init__(self, ffmpeg: str, path: str, start_s: float, fps: float, width: int, buffer: int = 48):
        self.start_s, self.fps = max(0.0, start_s), float(fps)
        self._buf = deque()                    # (índice, bytes)
        self._ahead = int(buffer)              # cuántos frames por delante de lo pedido se decodifican
        self._want = 0                         # último frame pedido por la UI (contrapresión)
        self._next_idx = 0                     # índice del siguiente frame decodificado
        self._lock = threading.Lock()
        self._alive = True
        self._eof = False
        vf = f"fps={fps:.6f},scale={int(width)}:-2"
        self._p = subprocess.Popen(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{self.start_s:.3f}", "-i", path, "-an",
             "-vf", vf, "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "6", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **_NOWIN)
        self._t = threading.Thread(target=self._reader, daemon=True)
        self._t.start()

    def _reader(self) -> None:
        data = b""
        try:
            while self._alive:
                chunk = self._p.stdout.read(65536)
                if not chunk:
                    break
                data += chunk
                while True:
                    a = data.find(_SOI)
                    if a < 0:
                        data = b""
                        break
                    b = data.find(_EOI, a + 2)
                    if b < 0:
                        data = data[a:]
                        break
                    frame, data = data[a:b + 2], data[b + 2:]
                    with self._lock:
                        self._buf.append((self._next_idx, frame))
                        self._next_idx += 1
                        while self._buf and self._buf[0][0] < self._want - 8:     # descarta lo ya mostrado
                            self._buf.popleft()
                    import time
                    while self._alive and self._next_idx > self._want + self._ahead:   # contrapresión
                        time.sleep(0.004)
        finally:
            self._eof = True

    def get(self, idx: int, wait_s: float = 0.25) -> Optional[bytes]:
        """Frame `idx` (0 = el de start_s). Espera un poco si aún no está decodificado."""
        import time
        end = time.perf_counter() + wait_s
        self._want = max(self._want, idx) if idx >= self._want - 8 else idx      # permite retroceder
        while True:
            with self._lock:
                last = None
                for i, b in self._buf:
                    if i == idx:
                        return b
                    if i < idx:
                        last = b
                newest = self._buf[-1][0] if self._buf else -1
            if newest >= idx or self._eof or time.perf_counter() >= end:
                return last if (last is not None and idx >= self.earliest()) else None
            time.sleep(0.004)

    def earliest(self) -> int:
        """Índice del frame más antiguo aún disponible (si lo pedido es menor, hay que reabrir el stream)."""
        with self._lock:
            return self._buf[0][0] if self._buf else self._next_idx

    def close(self) -> None:
        self._alive = False
        try:
            self._p.kill()
            self._p.wait(timeout=2)
        except Exception:
            pass
        try:
            self._p.stdout.close()
        except Exception:
            pass
