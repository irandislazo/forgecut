"""Render del timeline a MP4 con un único ffmpeg (filtergraph generado).

- Vídeo: por segmentos de tiempo; en cada uno se apilan los clips visibles (capas V1<V2<…)
  sobre fondo negro con recorte, volteo, zoom, rotación, opacidad y posición.
- Audio: mezcla de las pistas de audio + audio propio de los clips de vídeo no silenciados.
- El filtergraph va a un archivo (no a la línea de comandos): evita el límite de Windows.

Robustez (un render NO debe fallar por una edición rara):
- Todos los parámetros del inspector se saneán (recortes que suman ≥ 1, zoom/velocidad/opacidad fuera de rango, NaN…).
- Audio sin tramos vacíos (clips fuera del medio) y con marcas de tiempo regeneradas (`asetpts=N/SR/TB`).
- Un medio que falta se pinta como «offline» (gris, sin audio) y se avisa; no se cae el render.
- Se escribe a un archivo temporal; si ffmpeg falla se reintenta solo en modo seguro (sin efectos del inspector y,
  como último recurso, solo el audio de las pistas de audio). Nunca queda un MP4 corrupto en la carpeta de salida.
- Si todo falla: mensaje legible (sin el ruido de estadísticas de x264) y registro completo en un archivo.
"""
from __future__ import annotations

import math
import os
import re
import subprocess
import tempfile
from typing import Callable, Dict, List, Optional, Tuple

from ..core.models import Clip, Project, Timeline, Transform
from .ffmpeg_backend import _NOWIN


def visible_stack_at(tl: Timeline, frame: int) -> List[Clip]:
    out = []
    for t in tl.tracks:
        if t.kind != "video" or t.hidden:
            continue
        for c in t.clips:
            if c.enabled and c.start <= frame < c.end:
                out.append(c)
    return out


def flatten_video(tl: Timeline) -> List[Tuple[int, int, List[Clip]]]:
    total = tl.duration
    bounds = {0, total}
    for t in tl.tracks:
        if t.kind == "video" and not t.hidden:
            for c in t.clips:
                if c.enabled:
                    bounds.update((c.start, c.end))
    pts = sorted(bounds)
    segs: List[Tuple[int, int, List[Clip]]] = []
    for a, b in zip(pts, pts[1:]):
        if b <= a:
            continue
        stack = visible_stack_at(tl, a)
        if segs and [c.id for c in segs[-1][2]] == [c.id for c in stack] and segs[-1][1] == a \
                and all(c.speed == 1.0 for c in stack):
            segs[-1] = (segs[-1][0], b, segs[-1][2])         # fusiona tramos idénticos
        else:
            segs.append((a, b, stack))
    return segs


def _atempo(speed: float) -> str:
    parts, s = [], speed
    while s > 2.0:
        parts.append("atempo=2.0"); s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5"); s /= 0.5
    parts.append(f"atempo={s:.6f}")
    return ",".join(parts)


def _f(v, default: float, lo: Optional[float] = None, hi: Optional[float] = None) -> float:
    """float finito y acotado (NaN/inf/None → default)."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(v):
        return default
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def _crop_pair(a, b, keep: float = 0.05) -> Tuple[float, float]:
    """Recortes opuestos (izq+der o arriba+abajo): nunca dejan menos de `keep` del cuadro (antes sumas ≥ 1 rompían ffmpeg)."""
    a, b = _f(a, 0.0, 0.0, 1.0), _f(b, 0.0, 0.0, 1.0)
    if a + b > 1 - keep:
        k = (1 - keep) / (a + b)
        a, b = a * k, b * k
    return a, b


_NOISE = re.compile(r"^\s*(\[(libx264|aac|libmp3lame)\b|frame=|size=|video:|Lsize=|kb/s|Stream mapping|Press \[q\]|"
                    r"Output #|Input #|  (Metadata|Duration|encoder|major_brand|minor_|compatible_|creation_time|handler_name|"
                    r"vendor_id|Stream #)|\s*cpb:|\s*Side data)")
_HINT = re.compile(r"error|invalid|cannot|can't|no such|failed|unable|unrecognized|not found|matches no|too many|"
                   r"non monoton|permission|denied|no space|not supported", re.I)


def summarize_ffmpeg_error(stderr: str, limit: int = 10) -> str:
    """Las líneas ÚTILES del error de ffmpeg (sin las estadísticas de x264/aac que tapaban la causa real)."""
    lines = [l.rstrip() for l in (stderr or "").splitlines() if l.strip() and not _NOISE.match(l)]
    hints = [l for l in lines if _HINT.search(l)]
    pick = hints[:limit] if hints else lines[-limit:]
    return "\n".join(pick) if pick else "ffmpeg terminó con error sin mensaje."


def _even(x: float) -> int:
    return max(2, int(x) // 2 * 2)


class _Inputs:
    def __init__(self, fps: int):
        self.fps, self.paths, self.args = fps, {}, []

    def idx(self, path: str, image: bool = False) -> int:
        if path not in self.paths:
            self.paths[path] = len(self.paths)
            self.args += (["-loop", "1", "-framerate", str(self.fps)] if image else []) + ["-i", path]
        return self.paths[path]


def build_filtergraph(project: Project, proxy: bool = False, short_side: Optional[int] = None,
                      level: int = 0, notes: Optional[List[str]] = None) -> Tuple[List[str], str, float]:
    """Devuelve (args de entradas, texto del filtergraph, duración total en s).

    level 0 = normal · 1 = sin efectos del inspector · 2 = además solo el audio de las pistas de audio.
    `notes` recoge avisos (p. ej. medios no encontrados).
    """
    notes = notes if notes is not None else []
    offline = set()
    tl = project.timeline
    fps = tl.fps
    total_f = tl.duration
    if total_f <= 0:
        raise ValueError("El timeline está vacío.")
    k = (short_side / max(1, min(tl.width, tl.height))) if short_side else (0.5 if proxy else 1.0)
    W, H = _even(tl.width * k), _even(tl.height * k)
    inp, lines = _Inputs(fps), []
    segs = flatten_video(tl)

    for i, (a, b, stack) in enumerate(segs):
        d = (b - a) / fps
        cur = f"bg{i}"
        lines.append(f"color=c=black:s={W}x{H}:r={fps}:d={d:.6f}[{cur}]")
        for j, c in enumerate(stack):
            nxt = f"o{i}_{j}"
            asset = project.assets.get(c.asset_id) if c.asset_id else None
            tr = c.transform if level == 0 else Transform()
            if asset is not None and asset.kind != "audio" and c.kind != "placeholder" and not os.path.exists(asset.path):
                if asset.path not in offline:
                    offline.add(asset.path)
                    notes.append(f"Medio no encontrado (se pintó en gris): {asset.name or asset.path}")
                asset = None
            if c.kind == "placeholder" or asset is None or asset.kind == "audio":
                lines.append(f"color=c=0x2a2a2a:s={W}x{H}:r={fps}:d={d:.6f}[l{i}_{j}]")
                lines.append(f"[{cur}][l{i}_{j}]overlay=0:0:eof_action=pass[{nxt}]")
                cur = nxt
                continue
            sp = _f(c.speed, 1.0, 0.05, 64.0)
            src_f = max(0, c.source_in) + int(round((a - c.start) * sp))
            ss, use = src_f / fps, d * sp
            n = inp.idx(asset.path, image=(asset.kind == "image"))
            ch = [f"trim=start={ss:.6f}:duration={use:.6f}", f"setpts=(PTS-STARTPTS)/{sp:.6f}", f"fps={fps}"]
            cl, cr = _crop_pair(tr.crop_l, tr.crop_r)
            ct, cb = _crop_pair(tr.crop_t, tr.crop_b)
            if cl or cr or ct or cb:
                ch.append(f"crop=iw*{1 - cl - cr:.6f}:ih*{1 - ct - cb:.6f}:iw*{cl:.6f}:ih*{ct:.6f}")
            if tr.flip_h: ch.append("hflip")
            if tr.flip_v: ch.append("vflip")
            ch.append(f"scale={W}:{H}:force_original_aspect_ratio=decrease")
            zoom = _f(tr.scale, 1.0, 0.05, 8.0)
            if abs(zoom - 1.0) > 1e-6:
                ch.append(f"scale=max(2\\,trunc(iw*{zoom:.6f}/2)*2):max(2\\,trunc(ih*{zoom:.6f}/2)*2)")
            rot = _f(tr.rotation, 0.0)
            if abs(rot) > 1e-6:
                r = f"{rot:.4f}*PI/180"
                ch += ["format=rgba", f"rotate={r}:ow=rotw({r}):oh=roth({r}):c=black@0"]
            opa = _f(tr.opacity, 1.0, 0.0, 1.0)
            if opa < 0.999:
                ch += ["format=rgba", f"colorchannelmixer=aa={opa:.4f}"]
            lines.append(f"[{n}:v]{','.join(ch)}[l{i}_{j}]")
            lines.append(f"[{cur}][l{i}_{j}]overlay=x=(W-w)/2+{_f(tr.x, 0.0) * k:.2f}:y=(H-h)/2+{_f(tr.y, 0.0) * k:.2f}:"
                         f"eof_action=pass:format=auto[{nxt}]")
            cur = nxt
        lines.append(f"[{cur}]trim=duration={d:.6f},setpts=PTS-STARTPTS[s{i}]")
    lines.append("".join(f"[s{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=0[vout]")

    # ── audio ──────────────────────────────────────────────────────
    labels = []
    for t in tl.tracks:
        if t.muted:
            continue
        for c in t.clips:
            asset = project.assets.get(c.asset_id) if c.asset_id else None
            if not asset or not c.enabled or c.mute or _f(c.volume, 1.0) <= 0 or not os.path.exists(asset.path):
                continue
            from_audio_track = t.kind == "audio"
            if from_audio_track or (level < 2 and t.kind == "video" and not t.hidden and asset.has_audio and asset.kind == "video"):
                sp = _f(c.speed, 1.0, 0.05, 64.0)
                ss, use = max(0, c.source_in) / fps, c.duration * sp / fps
                if asset.duration_s:                               # nunca mezclar un tramo fuera del medio (causaba timestamps corruptos)
                    if ss >= asset.duration_s - 0.02:
                        continue
                    use = min(use, asset.duration_s - ss)
                if use < 0.02:
                    continue
                n = inp.idx(asset.path)
                ms = max(0, int(round(c.start / fps * 1000)))
                chain = [f"atrim=start={ss:.6f}:duration={use:.6f}", "asetpts=PTS-STARTPTS"]
                if abs(sp - 1.0) > 1e-6:
                    chain.append(_atempo(sp))
                chain += ["aresample=44100", "aformat=channel_layouts=stereo", f"volume={_f(c.volume, 1.0, 0.0, 8.0):.4f}",
                          f"adelay=delays={ms}:all=1"]
                lab = f"a{len(labels)}"
                lines.append(f"[{n}:a]{','.join(chain)}[{lab}]")
                labels.append(lab)
    total_s = total_f / fps
    if labels:
        lines.append("".join(f"[{x}]" for x in labels) +
                     f"amix=inputs={len(labels)}:normalize=0:duration=longest,apad=whole_dur={total_s:.6f},asetpts=N/SR/TB[aout]")
    else:
        lines.append(f"anullsrc=r=44100:cl=stereo,atrim=duration={total_s:.6f}[aout]")
    return inp.args, ";\n".join(lines), total_s


def _write_log(out_path: str, body: str) -> Optional[str]:
    """Guarda el registro completo de ffmpeg junto al vídeo (o en la carpeta temporal si no se puede)."""
    base = os.path.splitext(os.path.abspath(out_path))[0] + "_render_error.log"
    for path in (base, os.path.join(tempfile.gettempdir(), os.path.basename(base))):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
            return path
        except OSError:
            continue
    return None


def _run_once(backend, project, tmp_out, progress, proxy, short_side, crf, level, notes):
    in_args, graph, total_s = build_filtergraph(project, proxy, short_side, level, notes)
    fd, script = tempfile.mkstemp(suffix=".ffscript", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(graph)
    err = tempfile.TemporaryFile()
    try:
        cmd = [backend.ffmpeg, "-hide_banner", "-y", "-nostats", "-progress", "pipe:1", *in_args,
               *backend.filter_script_args(script), "-map", "[vout]", "-map", "[aout]",
               "-c:v", "libx264", "-preset", "ultrafast" if proxy else "veryfast",
               "-crf", str(crf if crf is not None else (28 if proxy else 20)), "-pix_fmt", "yuv420p", "-r", str(project.timeline.fps),
               "-c:a", "aac", "-b:a", "192k", "-t", f"{total_s:.6f}", "-movflags", "+faststart", tmp_out]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, text=True, **_NOWIN)
        for line in p.stdout:
            if progress and line.startswith("out_time_us="):
                try:
                    progress(min(1.0, max(0.0, int(line.split("=")[1]) / 1e6 / total_s)))
                except ValueError:
                    pass
        p.stdout.close()
        p.wait()
        err.seek(0)
        log = err.read().decode("utf-8", "replace")
        if p.returncode != 0 or not os.path.exists(tmp_out) or os.path.getsize(tmp_out) < 1000:
            raise RuntimeError(log or "ffmpeg no generó el vídeo.")
    finally:
        err.close()
        try:
            os.remove(script)
        except OSError:
            pass


def render_project(backend, project: Project, out_path: str,
                   progress: Optional[Callable[[float], None]] = None, proxy: bool = False,
                   short_side: Optional[int] = None, crf: Optional[int] = None) -> str:
    backend._need()
    if project.timeline.duration <= 0:
        raise ValueError("El timeline está vacío.")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tmp_out = os.path.splitext(out_path)[0] + ".part.mp4"           # el MP4 final solo aparece si el render terminó bien
    levels = (("", 0), ("sin los efectos del inspector (zoom, recorte, rotación, opacidad, posición)", 1),
              ("solo con el audio de las pistas de audio y sin efectos", 2))
    notes: List[str] = []
    logs: List[str] = []
    try:
        for attempt, (what, level) in enumerate(levels):
            try:
                notes_try: List[str] = []
                _run_once(backend, project, tmp_out, progress, proxy, short_side, crf, level, notes_try)
                os.replace(tmp_out, out_path)
                backend.last_render_notes = (["Se renderizó " + what + " porque el modo normal falló."] if what else []) + notes_try
                if progress:
                    progress(1.0)
                return out_path
            except RuntimeError as ex:
                logs.append(f"── intento {attempt + 1} (nivel {level}) ──\n{ex}")
                if os.path.exists(tmp_out):
                    try:
                        os.remove(tmp_out)
                    except OSError:
                        pass
        path = _write_log(out_path, "\n\n".join(logs))
        msg = "Render falló:\n" + summarize_ffmpeg_error(logs[0])
        raise RuntimeError(msg + (f"\nRegistro completo: {path}" if path else ""))
    finally:
        if os.path.exists(tmp_out):
            try:
                os.remove(tmp_out)
            except OSError:
                pass
