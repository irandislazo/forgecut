"""Lectura de metadatos SIN ffmpeg (Python puro): duración, tamaño, fps y si hay audio.

Sirve para que ForgeCut pueda importar medios y construir/editar timelines en plataformas donde no se puede lanzar
ffmpeg (Android) o cuando solo hay ffmpeg sin ffprobe. Cubre MP4/MOV/M4A/M4V, MKV/WebM, AVI, WAV, MP3, FLAC, OGG/Opus
y las cabeceras de PNG/JPEG/GIF/BMP. Si un formato no se entiende devuelve duración 0 (nunca lanza por eso).
"""
from __future__ import annotations

import os
import struct
from typing import Dict, Optional, Tuple

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}


def _u32(b, o): return struct.unpack(">I", b[o:o + 4])[0]
def _u64(b, o): return struct.unpack(">Q", b[o:o + 8])[0]


# ── MP4 / MOV / M4A ────────────────────────────────────────────────
def _children(buf: bytes, start: int = 0, end: Optional[int] = None):
    end = len(buf) if end is None else end
    o = start
    while o + 8 <= end:
        size, typ = _u32(buf, o), buf[o + 4:o + 8]
        hdr = 8
        if size == 1 and o + 16 <= end:
            size, hdr = _u64(buf, o + 8), 16
        elif size == 0:
            size = end - o
        if size < hdr or o + size > end:
            size = max(hdr, min(size, end - o))
        yield typ, o + hdr, o + size
        o += size


def _find(buf, path, start=0, end=None):
    """Primer cuadro anidado siguiendo `path` (lista de tipos) → (inicio, fin) del payload, o None."""
    cur = (start, len(buf) if end is None else end)
    for t in path:
        for typ, s, e in _children(buf, *cur):
            if typ == t:
                cur = (s, e)
                break
        else:
            return None
    return cur


def _time_header(buf, s):
    ver = buf[s]
    if ver == 1:
        return _u32(buf, s + 20), _u64(buf, s + 24)
    return _u32(buf, s + 12), _u32(buf, s + 16)


def _probe_mp4(path: str) -> Dict:
    out = {"duration_s": 0.0, "width": 0, "height": 0, "fps": 0.0, "has_audio": False, "has_video": False}
    moov = None
    with open(path, "rb") as f:
        size = os.path.getsize(path)
        o = 0
        while o + 8 <= size:
            f.seek(o)
            h = f.read(16)
            sz, typ = _u32(h, 0), h[4:8]
            if sz == 1:
                sz = _u64(h, 8)
            elif sz == 0:
                sz = size - o
            if typ == b"moov":
                f.seek(o)
                moov = f.read(min(sz, 64 * 1024 * 1024))
                break
            if sz < 8:
                break
            o += sz
    if not moov:
        return out
    body = _find(moov, [b"moov"])
    mv = _find(moov, [b"moov", b"mvhd"])
    if mv:
        ts, dur = _time_header(moov, mv[0])
        out["duration_s"] = dur / ts if ts else 0.0
    best_track_dur = 0.0
    for typ, s, e in _children(moov, *body):
        if typ != b"trak":
            continue
        hd = _find(moov, [b"mdia", b"hdlr"], s, e)
        handler = moov[hd[0] + 8:hd[0] + 12] if hd else b""
        if handler == b"soun":
            out["has_audio"] = True
        elif handler == b"vide" and not out["has_video"]:
            out["has_video"] = True
            tk = _find(moov, [b"tkhd"], s, e)
            if tk:
                out["width"], out["height"] = _u32(moov, tk[1] - 8) >> 16, _u32(moov, tk[1] - 4) >> 16
            md = _find(moov, [b"mdia", b"mdhd"], s, e)
            st = _find(moov, [b"mdia", b"minf", b"stbl", b"stts"], s, e)
            if md and st:
                ts, dur = _time_header(moov, md[0])
                n = _u32(moov, st[0] + 4)
                samples = total = 0
                for i in range(min(n, 100000)):
                    c, d = _u32(moov, st[0] + 8 + i * 8), _u32(moov, st[0] + 12 + i * 8)
                    samples, total = samples + c, total + c * d
                if total and ts:
                    out["fps"] = round(samples / (total / ts), 3)
                best_track_dur = max(best_track_dur, dur / ts if ts else 0.0)
    if not out["duration_s"]:
        out["duration_s"] = best_track_dur
    return out


# ── Matroska / WebM (EBML) ─────────────────────────────────────────
_MASTER = {0x18538067, 0x1549A966, 0x1654AE6B, 0xAE, 0xE0}


def _vint(b, o, keep_marker=False):
    first = b[o]
    n = 1
    while n <= 8 and not first & (0x80 >> (n - 1)):
        n += 1
    if n > 8:
        return None, o + 1
    v = first if keep_marker else first & (0xFF >> n)
    for i in range(1, n):
        v = (v << 8) | b[o + i]
    return v, o + n


def _probe_mkv(path: str) -> Dict:
    out = {"duration_s": 0.0, "width": 0, "height": 0, "fps": 0.0, "has_audio": False, "has_video": False}
    with open(path, "rb") as f:
        b = f.read(4 * 1024 * 1024)
    state = {"scale": 1_000_000, "dur": 0.0, "tt": 0}

    def walk(o, end):
        while o < end - 2:
            try:
                eid, o2 = _vint(b, o, True)
                size, o3 = _vint(b, o2)
            except IndexError:
                return
            if eid is None or size is None:
                return
            unknown = size == (1 << (7 * (o3 - o2))) - 1
            e = end if unknown else min(end, o3 + size)
            if eid in _MASTER:
                if eid == 0xAE:
                    state["tt"] = 0
                walk(o3, e)
            elif eid == 0x2AD7B1:
                state["scale"] = int.from_bytes(b[o3:e], "big")
            elif eid == 0x4489:
                state["dur"] = struct.unpack(">d" if e - o3 == 8 else ">f", b[o3:e])[0]
            elif eid == 0x83:
                state["tt"] = int.from_bytes(b[o3:e], "big")
                if state["tt"] == 1:
                    out["has_video"] = True
                elif state["tt"] == 2:
                    out["has_audio"] = True
            elif eid == 0xB0 and not out["width"]:
                out["width"] = int.from_bytes(b[o3:e], "big")
            elif eid == 0xBA and not out["height"]:
                out["height"] = int.from_bytes(b[o3:e], "big")
            elif eid == 0x23E383 and not out["fps"] and state["tt"] == 1:
                ns = int.from_bytes(b[o3:e], "big")
                out["fps"] = round(1e9 / ns, 3) if ns else 0.0
            o = e
    walk(0, len(b))
    out["duration_s"] = state["dur"] * state["scale"] / 1e9
    return out


# ── AVI ────────────────────────────────────────────────────────────
def _probe_avi(path: str) -> Dict:
    out = {"duration_s": 0.0, "width": 0, "height": 0, "fps": 0.0, "has_audio": False, "has_video": False}
    with open(path, "rb") as f:
        b = f.read(256 * 1024)
    i = b.find(b"avih")
    if i >= 0:
        us, _mb, _pad, _fl, frames = struct.unpack("<IIIII", b[i + 8:i + 28])
        w, h = struct.unpack("<II", b[i + 40:i + 48])
        if us:
            out["fps"] = round(1e6 / us, 3)
            out["duration_s"] = frames * us / 1e6
        out["width"], out["height"], out["has_video"] = w, h, True
    out["has_audio"] = b"auds" in b
    return out


# ── audio ──────────────────────────────────────────────────────────
def _probe_wav(path: str) -> Dict:
    out = {"duration_s": 0.0, "has_audio": True}
    with open(path, "rb") as f:
        h = f.read(12)
        if h[:4] != b"RIFF":
            return out
        byte_rate = data = 0
        while True:
            ch = f.read(8)
            if len(ch) < 8:
                break
            cid, sz = ch[:4], struct.unpack("<I", ch[4:])[0]
            if cid == b"fmt ":
                p = f.read(sz + (sz & 1))
                byte_rate = struct.unpack("<I", p[8:12])[0]
            elif cid == b"data":
                data = sz if sz != 0xFFFFFFFF else os.path.getsize(path) - f.tell()
                break
            else:
                f.seek(sz + (sz & 1), 1)
        if byte_rate:
            out["duration_s"] = data / byte_rate
    return out


_BR_V1_L3 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
_BR_V2_L3 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]
_SR = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}


def _probe_mp3(path: str) -> Dict:
    out = {"duration_s": 0.0, "has_audio": True}
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(10)
        start = 0
        if head[:3] == b"ID3":
            start = 10 + ((head[6] & 0x7F) << 21 | (head[7] & 0x7F) << 14 | (head[8] & 0x7F) << 7 | (head[9] & 0x7F))
        f.seek(start)
        buf = f.read(64 * 1024)
    i = 0
    while i + 4 <= len(buf):
        if buf[i] == 0xFF and (buf[i + 1] & 0xE0) == 0xE0:
            ver, layer = (buf[i + 1] >> 3) & 3, (buf[i + 1] >> 1) & 3
            bri, sri = (buf[i + 2] >> 4) & 15, (buf[i + 2] >> 2) & 3
            if ver != 1 and layer == 1 and 0 < bri < 15 and sri < 3:
                sr = _SR[ver][sri]
                kbps = (_BR_V1_L3 if ver == 3 else _BR_V2_L3)[bri]
                spf = 1152 if ver == 3 else 576
                for tag in (b"Xing", b"Info"):                       # VBR: nº de frames en la cabecera Xing
                    j = buf.find(tag, i, i + 60)
                    if j >= 0 and buf[j + 7] & 1:
                        out["duration_s"] = _u32(buf, j + 8) * spf / sr
                        return out
                out["duration_s"] = max(0, size - start - (128 if size > 128 else 0)) / (kbps * 125.0)
                return out
        i += 1
    return out


def _probe_flac(path: str) -> Dict:
    out = {"duration_s": 0.0, "has_audio": True}
    with open(path, "rb") as f:
        b = f.read(64)
    if b[:4] == b"fLaC" and len(b) >= 26:
        v = int.from_bytes(b[18:26], "big")
        sr, total = (v >> 44) & 0xFFFFF, v & ((1 << 36) - 1)
        out["duration_s"] = total / sr if sr else 0.0
    return out


def _probe_ogg(path: str) -> Dict:
    out = {"duration_s": 0.0, "has_audio": True}
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        first = f.read(512)
        f.seek(max(0, size - 65536))
        tail = f.read()
    rate, pre = 0, 0
    j = first.find(b"OpusHead")
    if j >= 0:
        rate, pre = 48000, struct.unpack("<H", first[j + 10:j + 12])[0]
    else:
        j = first.find(b"\x01vorbis")
        if j >= 0:
            rate = struct.unpack("<I", first[j + 12:j + 16])[0]
    k = tail.rfind(b"OggS")
    if rate and k >= 0 and len(tail) >= k + 14:
        gp = struct.unpack("<q", tail[k + 6:k + 14])[0]
        if gp > 0:
            out["duration_s"] = max(0.0, (gp - pre) / rate)
    return out


# ── imágenes ───────────────────────────────────────────────────────
def _probe_image(path: str) -> Tuple[int, int]:
    try:
        with open(path, "rb") as f:
            h = f.read(32)
            if h[:8] == b"\x89PNG\r\n\x1a\n":
                return struct.unpack(">II", h[16:24])
            if h[:6] in (b"GIF87a", b"GIF89a"):
                return struct.unpack("<HH", h[6:10])
            if h[:2] == b"BM":
                w, hh = struct.unpack("<ii", h[18:26])
                return w, abs(hh)
            if h[:2] == b"\xff\xd8":                                  # JPEG: buscar un marcador SOFn
                f.seek(2)
                while True:
                    m = f.read(4)
                    if len(m) < 4 or m[0] != 0xFF:
                        break
                    marker, ln = m[1], struct.unpack(">H", m[2:4])[0]
                    if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                        d = f.read(5)
                        return struct.unpack(">HH", d[1:5])[::-1]
                    f.seek(ln - 2, 1)
    except Exception:
        pass
    return 0, 0


# ── API ────────────────────────────────────────────────────────────
def probe_pure(path: str) -> Dict:
    """Mismo formato que FFmpegBackend.probe: path, name, kind, duration_s, width, height, fps, has_audio."""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    ext = os.path.splitext(path)[1].lower()
    info = {"path": path, "name": os.path.basename(path), "kind": "video", "duration_s": 0.0,
            "width": 0, "height": 0, "fps": 0.0, "has_audio": False}
    try:
        if ext in IMAGE_EXT:
            info["kind"] = "image"
            info["width"], info["height"] = _probe_image(path)
        elif ext in (".mp4", ".mov", ".m4v", ".m4a", ".3gp"):
            r = _probe_mp4(path)
            info.update({k: r[k] for k in ("duration_s", "width", "height", "fps", "has_audio")})
            if ext == ".m4a" or not r["has_video"]:
                info.update(kind="audio", width=0, height=0, fps=0.0, has_audio=True)
        elif ext in (".mkv", ".webm"):
            r = _probe_mkv(path)
            info.update({k: r[k] for k in ("duration_s", "width", "height", "fps", "has_audio")})
            if not r["has_video"]:
                info.update(kind="audio", has_audio=True)
        elif ext == ".avi":
            r = _probe_avi(path)
            info.update({k: r[k] for k in ("duration_s", "width", "height", "fps", "has_audio")})
        elif ext in AUDIO_EXT:
            fn = {".wav": _probe_wav, ".mp3": _probe_mp3, ".flac": _probe_flac, ".ogg": _probe_ogg, ".opus": _probe_ogg}.get(ext)
            r = fn(path) if fn else {"duration_s": 0.0}
            info.update(kind="audio", has_audio=True, duration_s=r.get("duration_s", 0.0))
    except Exception:                                   # formato raro/corrupto: se importa igualmente con duración 0
        pass
    return info
