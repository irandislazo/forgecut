"""Exporta tramos de audio del timeline (p. ej. los huecos de lipsync) a mp3/wav.

El audio se toma de las pistas de audio del timeline, respetando el recorte de cada clip, de modo
que el fragmento exportado es EXACTAMENTE el sonido que cae bajo el clip seleccionado.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Iterable, List, Optional

from ..core.models import Clip, Project


@dataclass
class AudioRange:
    name: str
    start_f: int
    dur_f: int
    clip_id: str = ""


def lipsync_ranges(project: Project) -> List[AudioRange]:
    tl = project.timeline
    slots = sorted((c for c in tl.all_clips() if c.kind == "placeholder"), key=lambda c: c.start)
    return [AudioRange(_slot_name(tl, c, i + 1), c.start, c.duration, c.id) for i, c in enumerate(slots)]


def selection_ranges(project: Project, clip_ids: Iterable[str]) -> List[AudioRange]:
    tl = project.timeline
    ids = set(clip_ids)
    chosen = sorted((c for c in tl.all_clips() if c.id in ids), key=lambda c: (c.start, c.track_id))
    return [AudioRange(_slot_name(tl, c, i + 1), c.start, c.duration, c.id) for i, c in enumerate(chosen)]


def _slot_name(tl, c: Clip, n: int) -> str:
    idx = c.meta.get("index", n)
    sec = int(c.start / tl.fps)
    base = "lipsync" if c.kind == "placeholder" else (c.name or c.kind).replace(" ", "_")[:24]
    return f"{base}_{int(idx):02d}_{sec // 60:02d}m{sec % 60:02d}s"


def _audio_source(project: Project, start_f: int, dur_f: int, prefer_clip_id: str = ""):
    """(ruta, segundo inicial en el medio). Si el rango es un clip de AUDIO concreto se usa su propio medio
    (respetando su recorte); si no, el audio de las pistas que cubre el inicio del rango (p. ej. huecos de lipsync)."""
    tl = project.timeline
    if prefer_clip_id:
        for c in tl.all_clips():
            if c.id == prefer_clip_id and c.kind == "audio" and c.asset_id and c.asset_id in project.assets:
                return project.assets[c.asset_id].path, c.source_in / tl.fps
    for t in tl.tracks:
        if t.kind != "audio" or t.muted:
            continue
        for c in t.clips:
            if c.enabled and c.asset_id and c.start <= start_f < c.end:
                asset = project.assets[c.asset_id]
                src_f = c.source_in + (start_f - c.start) * c.speed
                return asset.path, src_f / tl.fps
    return None, 0.0


def export_audio_ranges(project: Project, backend, ranges: List[AudioRange], out_dir: str,
                        fmt: str = "mp3", on_item=None) -> List[str]:
    if not ranges:
        raise ValueError("No hay nada que exportar (selecciona clips o crea huecos de lipsync).")
    os.makedirs(out_dir, exist_ok=True)
    fps = project.timeline.fps
    outs, manifest = [], []
    for i, r in enumerate(ranges):
        src, ss = _audio_source(project, r.start_f, r.dur_f, r.clip_id)
        if not src:
            raise ValueError(f"No hay audio en el timeline bajo «{r.name}». Coloca la canción en una pista de audio.")
        out = os.path.join(out_dir, f"{r.name}.{fmt}")
        backend.export_audio(src, ss, r.dur_f / fps, out, fmt)
        outs.append(out)
        manifest.append({"file": os.path.basename(out), "timeline_start_s": r.start_f / fps,
                         "duration_s": r.dur_f / fps, "clip_id": r.clip_id})
        if on_item:
            on_item(i + 1, len(ranges), out)
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    return outs
