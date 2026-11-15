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

import copy
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from typing import Callable, Dict, List, Optional, Tuple

from ..core.models import Clip, Project, Timeline, Track, Transform
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


def _same_layer_continues(p: Clip, n: Clip, b_prev: int, a_next: int) -> bool:
    """¿`n` (en el frame a_next) es la continuación exacta de `p` (que llegaba hasta b_prev)?
    Los cortes de escena parten un clip en trozos que se reproducen seguidos: para el render son UN solo tramo."""
    if p.id == n.id:
        return p.speed == 1.0
    if (p.track_id, p.kind, p.asset_id, p.enabled) != (n.track_id, n.kind, n.asset_id, n.enabled):
        return False
    sp = _f(p.speed, 1.0, 0.05, 64.0)
    if sp != _f(n.speed, 1.0, 0.05, 64.0) or p.transform != n.transform:
        return False
    if p.kind == "placeholder" or p.asset_id is None or p.kind == "image":
        return True
    end_prev = p.source_in + int(round((b_prev - p.start) * sp))
    start_next = n.source_in + int(round((a_next - n.start) * sp))
    return end_prev == start_next


def flatten_video(tl: Timeline, total: Optional[int] = None) -> List[Tuple[int, int, List[Clip]]]:
    total = tl.duration if total is None else total
    bounds = {0, total}
    for t in tl.tracks:
        if t.kind == "video" and not t.hidden:
            for c in t.clips:
                if c.enabled:
                    bounds.update((c.start, c.end))
    pts = sorted(x for x in bounds if 0 <= x <= total)
    segs: List[Tuple[int, int, List[Clip]]] = []
    for a, b in zip(pts, pts[1:]):
        if b <= a:
            continue
        stack = visible_stack_at(tl, a)
        if (segs and segs[-1][1] == a and len(segs[-1][2]) == len(stack)
                and all(_same_layer_continues(p, n, a, a) for p, n in zip(segs[-1][2], stack))):
            segs[-1] = (segs[-1][0], b, segs[-1][2])         # fusiona tramos idénticos o continuos
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

    def idx(self, path: str, image: bool = False, seek: Optional[Tuple[float, float]] = None) -> int:
        key = (path, image, seek)
        if key not in self.paths:
            self.paths[key] = len(self.paths)
            opts = ["-loop", "1", "-framerate", str(self.fps)] if image else []
            if seek is not None:                     # lee SOLO [ss, ss+t) del medio (rápido y con memoria acotada)
                opts += ["-ss", f"{seek[0]:.6f}", "-t", f"{seek[1]:.6f}"]
            self.args += opts + ["-i", path]
        return self.paths[key]


def build_filtergraph(project: Project, proxy: bool = False, short_side: Optional[int] = None,
                      level: int = 0, notes: Optional[List[str]] = None, video: bool = True, audio: bool = True,
                      seek: bool = False, total_frames: Optional[int] = None) -> Tuple[List[str], str, float]:
    """Devuelve (args de entradas, texto del filtergraph, duración total en s).

    level 0 = normal · 1 = sin efectos del inspector · 2 = además solo el audio de las pistas de audio.
    `notes` recoge avisos (p. ej. medios no encontrados).
    video/audio: generar solo una parte (render por tramos). seek: cada capa de vídeo se abre como entrada propia con
    -ss/-t acotados a su trozo (evita decodificar el archivo entero y acumular frames de ramas que aún no se consumen).
    """
    notes = notes if notes is not None else []
    offline = set()
    tl = project.timeline
    fps = tl.fps
    total_f = tl.duration if total_frames is None else total_frames
    if total_f <= 0:
        raise ValueError("El timeline está vacío.")
    k = (short_side / max(1, min(tl.width, tl.height))) if short_side else (0.5 if proxy else 1.0)
    W, H = _even(tl.width * k), _even(tl.height * k)
    inp, lines = _Inputs(fps), []
    segs = flatten_video(tl, total_f) if video else []

    def resolve(c: Clip):
        asset = project.assets.get(c.asset_id) if c.asset_id else None
        if asset is not None and asset.kind != "audio" and c.kind != "placeholder" and not os.path.exists(asset.path):
            if asset.path not in offline:
                offline.add(asset.path)
                notes.append(f"Medio no encontrado (se pintó en gris): {asset.name or asset.path}")
            return None
        return asset

    for i, (a, b, stack) in enumerate(segs):
        d = (b - a) / fps
        cur = f"bg{i}"
        lines.append(f"color=c=black:s={W}x{H}:r={fps}:d={d:.6f}[{cur}]")
        for j, c in enumerate(stack):
            nxt = f"o{i}_{j}"
            asset = resolve(c)
            tr = c.transform if level == 0 else Transform()
            if c.kind == "placeholder" or asset is None or asset.kind == "audio":
                lines.append(f"color=c=0x2a2a2a:s={W}x{H}:r={fps}:d={d:.6f}[l{i}_{j}]")
                lines.append(f"[{cur}][l{i}_{j}]overlay=0:0:eof_action=pass[{nxt}]")
                cur = nxt
                continue
            sp = _f(c.speed, 1.0, 0.05, 64.0)
            src_f = max(0, c.source_in) + int(round((a - c.start) * sp))
            ss, use = src_f / fps, d * sp
            if seek and asset.kind == "video":
                n = inp.idx(asset.path, False, (ss, use + 0.5))   # entrada propia: solo lee SU trozo (memoria acotada)
                ss = 0.0
            else:
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
    if video:
        lines.append("".join(f"[s{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=0[vout]")

    # ── audio ──────────────────────────────────────────────────────
    labels = []
    for t in (tl.tracks if audio else []):
        if t.muted:
            continue
        pieces = []                                    # [asset, start, dur, source_in, speed, volume]
        for c in sorted(t.clips, key=lambda x: x.start):
            asset = project.assets.get(c.asset_id) if c.asset_id else None
            if not asset or not c.enabled or c.mute or _f(c.volume, 1.0) <= 0 or not os.path.exists(asset.path):
                continue
            if not (t.kind == "audio" or (level < 2 and t.kind == "video" and not t.hidden and asset.has_audio and asset.kind == "video")):
                continue
            sp, vol = _f(c.speed, 1.0, 0.05, 64.0), _f(c.volume, 1.0, 0.0, 8.0)
            cur = pieces[-1] if pieces else None
            if (cur and cur[0] is asset and cur[4] == sp and cur[5] == vol and c.start == cur[1] + cur[2]
                    and max(0, c.source_in) == cur[3] + int(round(cur[2] * sp))):
                cur[2] += c.duration                    # corte continuo: un solo tramo de audio (no cientos de ramas)
            else:
                pieces.append([asset, c.start, c.duration, max(0, c.source_in), sp, vol])
        for asset, start, dur, src_in, sp, vol in pieces:
            ss, use = src_in / fps, dur * sp / fps
            if asset.duration_s:                       # nunca mezclar un tramo fuera del medio (causaba timestamps corruptos)
                if ss >= asset.duration_s - 0.02:
                    continue
                use = min(use, asset.duration_s - ss)
            if use < 0.02:
                continue
            n = inp.idx(asset.path)
            ms = max(0, int(round(start / fps * 1000)))
            chain = [f"atrim=start={ss:.6f}:duration={use:.6f}", "asetpts=PTS-STARTPTS"]
            if abs(sp - 1.0) > 1e-6:
                chain.append(_atempo(sp))
            chain += ["aresample=44100", "aformat=channel_layouts=stereo", f"volume={vol:.4f}", f"adelay=delays={ms}:all=1"]
            lab = f"a{len(labels)}"
            lines.append(f"[{n}:a]{','.join(chain)}[{lab}]")
            labels.append(lab)
    total_s = total_f / fps
    if not audio:
        pass
    elif labels:
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


def _vcodec(proxy: bool, crf: Optional[int], fps: int) -> List[str]:
    return ["-c:v", "libx264", "-preset", "ultrafast" if proxy else "veryfast",
            "-crf", str(crf if crf is not None else (28 if proxy else 20)), "-pix_fmt", "yuv420p", "-r", str(fps)]


def _exec(backend, in_args, graph, total_s, out_args, tmp_out, progress=None, p0=0.0, p1=1.0) -> None:
    """Ejecuta ffmpeg con un filtergraph en archivo. Progreso escalado a [p0, p1]. RuntimeError(log) si falla."""
    fd, script = tempfile.mkstemp(suffix=".ffscript", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(graph)
    err = tempfile.TemporaryFile()
    try:
        cmd = [backend.ffmpeg, "-hide_banner", "-y", "-nostats", "-progress", "pipe:1", *in_args,
               *backend.filter_script_args(script), *out_args, tmp_out]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, text=True, **_NOWIN)
        for line in p.stdout:
            if progress and line.startswith("out_time_us="):
                try:
                    progress(p0 + (p1 - p0) * min(1.0, max(0.0, int(line.split("=")[1]) / 1e6 / max(total_s, 1e-6))))
                except ValueError:
                    pass
        p.stdout.close()
        p.wait()
        err.seek(0)
        log = err.read().decode("utf-8", "replace")
        if p.returncode != 0 or not os.path.exists(tmp_out) or os.path.getsize(tmp_out) < 1000:
            raise RuntimeError(log or f"ffmpeg terminó con código {p.returncode} (¿sin memoria?).")
    finally:
        err.close()
        try:
            os.remove(script)
        except OSError:
            pass


def _run_once(backend, project, tmp_out, progress, proxy, short_side, crf, level, notes):
    """Render en una sola pasada (proyectos pequeños o medianos: es el camino de siempre)."""
    in_args, graph, total_s = build_filtergraph(project, proxy, short_side, level, notes)
    _exec(backend, in_args, graph, total_s,
          ["-map", "[vout]", "-map", "[aout]", *_vcodec(proxy, crf, project.timeline.fps), "-c:a", "aac", "-b:a", "192k",
           "-t", f"{total_s:.6f}", "-movflags", "+faststart"], tmp_out, progress)


# ── render por tramos (proyectos con muchísimos cortes: memoria acotada) ───────────────
CHUNK_LONG_SOURCE_S = 30.0   # un medio de vídeo más largo que esto reutilizado en ≥ CHUNK_REUSE tramos → por tramos
CHUNK_REUSE = 3
CHUNK_MAX_LAYERS = 60        # o un grafo con más capas de vídeo que esto
CHUNK_MAX_SEGMENTS = 16      # tamaño de cada tramo (nº de segmentos)
CHUNK_MAX_SECONDS = 12.0     # …o de duración


def _window_timeline(tl: Timeline, A: int, B: int) -> Timeline:
    """Copia del timeline con solo lo que cae en [A, B), en tiempos relativos a A."""
    nt = Timeline(fps=tl.fps, width=tl.width, height=tl.height, tracks=[], markers=[])
    for t in tl.tracks:
        clips = []
        for c in t.clips:
            if c.end <= A or c.start >= B:
                continue
            n = copy.copy(c)
            s0, e0 = max(c.start, A), min(c.end, B)
            n.source_in = c.source_in + int(round((s0 - c.start) * _f(c.speed, 1.0, 0.05, 64.0)))
            n.start, n.duration = s0 - A, e0 - s0
            clips.append(n)
        nt.tracks.append(Track(t.id, t.name, t.kind, clips, t.locked, t.muted, t.hidden))
    return nt


def _windows(segs, fps: int) -> List[Tuple[int, int]]:
    wins, cur_a, cur_n = [], None, 0
    for a, b, _stack in segs:
        if cur_a is None:
            cur_a, cur_n = a, 0
        cur_n += 1
        if cur_n >= CHUNK_MAX_SEGMENTS or (b - cur_a) / fps >= CHUNK_MAX_SECONDS:
            wins.append((cur_a, b))
            cur_a = None
    if cur_a is not None:
        wins.append((cur_a, segs[-1][1]))
    return wins


def needs_chunking(project: Project) -> bool:
    """¿Riesgo de memoria en una sola pasada? Sí si un vídeo LARGO se reparte entre varios tramos (típico: un vídeo de
    minutos muy cortado/reordenado: ffmpeg decodifica una vez y copia cada frame a todas las ramas, acumulando en RAM lo
    que aún no toca consumir) o si el grafo tiene demasiadas capas. Las plantillas (clips de 5 s) no entran: siguen igual."""
    segs = flatten_video(project.timeline)
    uses, layers = Counter(), 0
    for _a, _b, stack in segs:
        for c in stack:
            if c.kind == "video" and c.asset_id:
                layers += 1
                uses[c.asset_id] += 1
    long_reuse = any(n >= CHUNK_REUSE and (project.assets.get(k) is not None and project.assets[k].duration_s >= CHUNK_LONG_SOURCE_S)
                     for k, n in uses.items())
    return long_reuse or layers > CHUNK_MAX_LAYERS


def _run_chunked(backend, project, tmp_out, progress, proxy, short_side, crf, level, notes):
    tl = project.timeline
    segs = flatten_video(tl)
    wins = _windows(segs, tl.fps)
    work = tempfile.mkdtemp(prefix="fc_render_")
    try:
        files = []
        for i, (A, B) in enumerate(wins):
            wp = Project(name=project.name, assets=project.assets, timeline=_window_timeline(tl, A, B))
            in_args, graph, tot = build_filtergraph(wp, proxy, short_side, level, notes, video=True, audio=False,
                                                    seek=True, total_frames=B - A)
            f = os.path.join(work, f"v{i:05d}.mp4")
            _exec(backend, in_args, graph, tot, ["-map", "[vout]", "-an", *_vcodec(proxy, crf, tl.fps), "-t", f"{tot:.6f}"],
                  f, progress, 0.92 * i / len(wins), 0.92 * (i + 1) / len(wins))
            files.append(f)
        in_a, graph_a, tot_a = build_filtergraph(project, proxy, short_side, level, notes, video=False, audio=True)
        af = os.path.join(work, "a.m4a")
        _exec(backend, in_a, graph_a, tot_a, ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-t", f"{tot_a:.6f}"],
              af, progress, 0.92, 0.97)
        lst = os.path.join(work, "list.txt")
        with open(lst, "w", encoding="utf-8") as fh:
            for f in files:
                fh.write("file '" + f.replace("\\", "/").replace("'", "'\\''") + "'\n")
        r = subprocess.run([backend.ffmpeg, "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-i", af,
                            "-map", "0:v", "-map", "1:a", "-c", "copy", "-t", f"{tot_a:.6f}", "-movflags", "+faststart", tmp_out],
                           capture_output=True, text=True, **_NOWIN)
        if r.returncode != 0 or not os.path.exists(tmp_out) or os.path.getsize(tmp_out) < 1000:
            raise RuntimeError("Unión de tramos falló:\n" + (r.stderr or ""))
        notes[:] = list(dict.fromkeys(notes))                      # sin avisos repetidos por tramo
    finally:
        shutil.rmtree(work, ignore_errors=True)


def render_project(backend, project: Project, out_path: str,
                   progress: Optional[Callable[[float], None]] = None, proxy: bool = False,
                   short_side: Optional[int] = None, crf: Optional[int] = None,
                   chunked: Optional[bool] = None) -> str:
    backend._need()
    if project.timeline.duration <= 0:
        raise ValueError("El timeline está vacío.")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tmp_out = os.path.splitext(out_path)[0] + ".part.mp4"           # el MP4 final solo aparece si el render terminó bien
    by_chunks = needs_chunking(project) if chunked is None else chunked
    # intentos: normal (una pasada, o por tramos si el proyecto lo pide) → por tramos → sin efectos → solo audio de pistas
    attempts = [("", 0, by_chunks)] + ([("", 0, True)] if not by_chunks else []) + [
        ("sin los efectos del inspector (zoom, recorte, rotación, opacidad, posición)", 1, True),
        ("solo con el audio de las pistas de audio y sin efectos", 2, True)]
    logs: List[str] = []
    try:
        for n_try, (what, level, chunk) in enumerate(attempts):
            try:
                notes_try: List[str] = []
                (_run_chunked if chunk else _run_once)(backend, project, tmp_out, progress, proxy, short_side, crf, level, notes_try)
                os.replace(tmp_out, out_path)
                backend.last_render_notes = (["Se renderizó " + what + " porque el modo normal falló."] if what else []) + notes_try
                if progress:
                    progress(1.0)
                return out_path
            except RuntimeError as ex:
                logs.append(f"── intento {n_try + 1} (nivel {level}{', por tramos' if chunk else ''}) ──\n{ex}")
                if os.path.exists(tmp_out):
                    try:
                        os.remove(tmp_out)
                    except OSError:
                        pass
        path = _write_log(out_path, "\n\n".join(logs))
        msg = "Render falló:\n" + summarize_ffmpeg_error(logs[0])
        if any("allocate memory" in l.lower() for l in logs):
            msg += "\nMemoria insuficiente: cierra otros programas o renderiza a menor resolución."
        raise RuntimeError(msg + (f"\nRegistro completo: {path}" if path else ""))
    finally:
        if os.path.exists(tmp_out):
            try:
                os.remove(tmp_out)
            except OSError:
                pass
