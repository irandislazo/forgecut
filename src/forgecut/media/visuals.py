"""Imágenes de apoyo para el timeline: tira de miniaturas (vídeo) y forma de onda (audio).

Ambas se generan UNA vez por medio y se guardan en caché (PNG). El timeline las muestra como UNA sola
imagen por clip, desplazada/recortada según source_in y duración: así un clip de 10 minutos sigue siendo
un solo control de interfaz, no cientos. Sin dependencias extra (PNG escrito a mano con zlib).
"""
from __future__ import annotations

import array
import os
import struct
import subprocess
import sys
import zlib
from typing import List, Optional

_NOWIN = {"creationflags": 0x08000000} if sys.platform.startswith("win") else {}


# ── forma de onda ──────────────────────────────────────────────────
def compute_peaks(ffmpeg: str, path: str, buckets: int = 2400, rate: int = 8000) -> List[float]:
    """Picos normalizados 0..1 (valor absoluto máximo por tramo) de TODO el audio del archivo."""
    r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", path, "-vn", "-ac", "1",
                        "-ar", str(rate), "-f", "s16le", "-"], capture_output=True, **_NOWIN)
    if r.returncode != 0 or not r.stdout:
        return []
    samples = array.array("h")
    raw = r.stdout[: len(r.stdout) // 2 * 2]
    samples.frombytes(raw)
    if sys.byteorder == "big":
        samples.byteswap()
    n = len(samples)
    if n == 0:
        return []
    buckets = max(1, min(buckets, n))
    step = n / buckets
    peaks = []
    for i in range(buckets):
        seg = samples[int(i * step): max(int(i * step) + 1, int((i + 1) * step))]
        peaks.append(max(max(seg), -min(seg)) / 32768.0 if seg else 0.0)
    top = max(peaks) or 1.0
    return [min(1.0, p / top * 0.98) for p in peaks]          # normaliza para que se vea aunque sea suave


def _png(width: int, height: int, rgba: bytearray) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    stride = width * 4
    raw = b"".join(b"\x00" + bytes(rgba[y * stride:(y + 1) * stride]) for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def waveform_png(peaks: List[float], out_path: str, height: int = 64, color=(235, 225, 255)) -> str:
    """Escribe la forma de onda (barras simétricas, fondo transparente). 1 px de ancho por pico."""
    w = max(1, len(peaks))
    buf = bytearray(w * height * 4)
    mid = height / 2.0
    for x, p in enumerate(peaks):
        half = max(1.0, p * (height / 2.0 - 1))
        y0, y1 = int(mid - half), int(mid + half)
        for y in range(max(0, y0), min(height, y1 + 1)):
            o = (y * w + x) * 4
            buf[o:o + 4] = bytes((color[0], color[1], color[2], 235))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(_png(w, height, buf))
    return out_path


# ── tira de miniaturas ─────────────────────────────────────────────
def thumbs_count(duration_s: float) -> int:
    """Una miniatura cada ~2 s, entre 8 y 100 (la tira se estira sobre la duración total del medio)."""
    return int(max(8, min(100, round((duration_s or 8.0) / 2.0))))


def filmstrip_png(ffmpeg: str, path: str, out_path: str, duration_s: float, height: int = 40) -> str:
    """Una sola imagen con N fotogramas repartidos uniformemente por TODO el medio."""
    n = thumbs_count(duration_s)
    dur = max(0.1, duration_s or 1.0)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    vf = f"fps={n}/{dur:.6f},scale=-2:{height},tile={n}x1"
    pre = ["-skip_frame", "nokey"] if dur > 90 else []          # vídeos largos: solo fotogramas clave (mucho más rápido)
    post = ["-pix_fmt", "yuvj420p", "-q:v", "5"] if out_path.lower().endswith((".jpg", ".jpeg")) else []
    r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *pre, "-i", path, "-an", "-vf", vf,
                        "-frames:v", "1", *post, out_path], capture_output=True, **_NOWIN)
    if r.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError("No se pudo generar la tira de miniaturas:\n" + (r.stderr or b"").decode("utf-8", "ignore")[-400:])
    return out_path
