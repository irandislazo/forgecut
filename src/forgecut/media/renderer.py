"""Render del timeline a MP4 con un único ffmpeg (filtergraph generado).

- Vídeo: por segmentos de tiempo; en cada uno se apilan los clips visibles (capas V1<V2<…)
  sobre fondo negro con recorte, volteo, zoom, rotación, opacidad y posición.
- Audio: mezcla de las pistas de audio + audio propio de los clips de vídeo no silenciados.
- El filtergraph va a un archivo (no a la línea de comandos): evita el límite de Windows.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Callable, Dict, List, Optional, Tuple

from ..core.models import Clip, Project, Timeline
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


def build_filtergraph(project: Project, proxy: bool = False, short_side: Optional[int] = None) -> Tuple[List[str], str, float]:
    """Devuelve (args de entradas, texto del filtergraph, duración total en s)."""
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
            tr = c.transform
            if c.kind == "placeholder" or asset is None:
                lines.append(f"color=c=0x2a2a2a:s={W}x{H}:r={fps}:d={d:.6f}[l{i}_{j}]")
                lines.append(f"[{cur}][l{i}_{j}]overlay=0:0:eof_action=pass[{nxt}]")
                cur = nxt
                continue
            src_f = c.source_in + int(round((a - c.start) * c.speed))
            ss, use = src_f / fps, d * c.speed
            n = inp.idx(asset.path, image=(asset.kind == "image"))
            ch = [f"trim=start={ss:.6f}:duration={use:.6f}", f"setpts=(PTS-STARTPTS)/{c.speed:.6f}", f"fps={fps}"]
            if tr.crop_l or tr.crop_r or tr.crop_t or tr.crop_b:
                ch.append(f"crop=iw*{1 - tr.crop_l - tr.crop_r:.6f}:ih*{1 - tr.crop_t - tr.crop_b:.6f}:"
                          f"iw*{tr.crop_l:.6f}:ih*{tr.crop_t:.6f}")
            if tr.flip_h: ch.append("hflip")
            if tr.flip_v: ch.append("vflip")
            ch.append(f"scale={W}:{H}:force_original_aspect_ratio=decrease")
            if abs(tr.scale - 1.0) > 1e-6:
                ch.append(f"scale=trunc(iw*{tr.scale:.6f}/2)*2:trunc(ih*{tr.scale:.6f}/2)*2")
            if abs(tr.rotation) > 1e-6:
                r = f"{tr.rotation:.4f}*PI/180"
                ch += ["format=rgba", f"rotate={r}:ow=rotw({r}):oh=roth({r}):c=black@0"]
            if tr.opacity < 0.999:
                ch += ["format=rgba", f"colorchannelmixer=aa={max(0.0, tr.opacity):.4f}"]
            lines.append(f"[{n}:v]{','.join(ch)}[l{i}_{j}]")
            lines.append(f"[{cur}][l{i}_{j}]overlay=x=(W-w)/2+{tr.x * k:.2f}:y=(H-h)/2+{tr.y * k:.2f}:"
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
            if not asset or not c.enabled or c.mute or c.volume <= 0:
                continue
            if t.kind == "audio" or (t.kind == "video" and not t.hidden and asset.has_audio and asset.kind == "video"):
                n = inp.idx(asset.path)
                ss, use = c.source_in / fps, c.duration * c.speed / fps
                ms = int(round(c.start / fps * 1000))
                chain = [f"atrim=start={ss:.6f}:duration={use:.6f}", "asetpts=PTS-STARTPTS"]
                if abs(c.speed - 1.0) > 1e-6:
                    chain.append(_atempo(c.speed))
                chain += ["aresample=44100", "aformat=channel_layouts=stereo", f"volume={c.volume:.4f}",
                          f"adelay=delays={ms}:all=1"]
                lab = f"a{len(labels)}"
                lines.append(f"[{n}:a]{','.join(chain)}[{lab}]")
                labels.append(lab)
    total_s = total_f / fps
    if labels:
        lines.append("".join(f"[{x}]" for x in labels) +
                     f"amix=inputs={len(labels)}:normalize=0:duration=longest,apad=whole_dur={total_s:.6f}[aout]")
    else:
        lines.append(f"anullsrc=r=44100:cl=stereo,atrim=duration={total_s:.6f}[aout]")
    return inp.args, ";\n".join(lines), total_s


def render_project(backend, project: Project, out_path: str,
                   progress: Optional[Callable[[float], None]] = None, proxy: bool = False,
                   short_side: Optional[int] = None, crf: Optional[int] = None) -> str:
    backend._need()
    in_args, graph, total_s = build_filtergraph(project, proxy, short_side)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    fd, script = tempfile.mkstemp(suffix=".ffscript", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(graph)
    err = tempfile.TemporaryFile()
    try:
        cmd = [backend.ffmpeg, "-hide_banner", "-y", "-nostats", "-progress", "pipe:1", *in_args,
               *backend.filter_script_args(script), "-map", "[vout]", "-map", "[aout]",
               "-c:v", "libx264", "-preset", "ultrafast" if proxy else "veryfast",
               "-crf", str(crf if crf is not None else (28 if proxy else 20)), "-pix_fmt", "yuv420p", "-r", str(project.timeline.fps),
               "-c:a", "aac", "-b:a", "192k", "-t", f"{total_s:.6f}", "-movflags", "+faststart", out_path]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, text=True, **_NOWIN)
        for line in p.stdout:
            if progress and line.startswith("out_time_us="):
                try:
                    progress(min(1.0, max(0.0, int(line.split("=")[1]) / 1e6 / total_s)))
                except ValueError:
                    pass
        p.stdout.close()
        p.wait()
        if p.returncode != 0:
            err.seek(0)
            raise RuntimeError("Render falló:\n" + err.read().decode("utf-8", "replace")[-1500:])
        if progress:
            progress(1.0)
        return out_path
    finally:
        err.close()
        try:
            os.remove(script)
        except OSError:
            pass
