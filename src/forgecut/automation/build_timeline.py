"""Convierte un Plan (segundos) en un Timeline editable (frames) dentro de un Project."""
from __future__ import annotations

import os
from typing import Optional

from ..core.models import Clip, MediaAsset, Project, Timeline, Track, new_id
from ..core import editing as ed
from .planners import Plan, plan_timeline
from .templates import Template

KIND_COLOR = {"reaction": "#3b82f6", "turn": "#f59e0b", "far": "#10b981", "lipsync": "#ef4444"}


def _asset_for(project: Project, tpl: Template, path: str) -> MediaAsset:
    for a in project.assets.values():
        if a.path == path:
            return a
    item = next((i for pool in tpl.pools.values() for i in pool if i.path == path), None)
    a = MediaAsset(id=new_id("m"), path=path, kind="video", name=os.path.basename(path),
                   duration_s=item.duration_s if item else 0.0, has_audio=bool(item and item.has_audio))
    return project.add_asset(a)


def apply_plan(project: Project, tpl: Template, plan: Plan, song: MediaAsset) -> Timeline:
    """Reemplaza el timeline del proyecto por el plan: V1 = vídeo y huecos de lipsync, A1 = canción."""
    old = project.timeline
    tl = Timeline.default(fps=old.fps, width=old.width, height=old.height)
    fps = tl.fps
    lip_n = 0
    for pc in plan.clips:
        a = int(round(pc.start_s * fps))
        b = int(round((pc.start_s + pc.dur_s) * fps))
        dur = max(1, b - a)
        if pc.kind == "lipsync":
            lip_n += 1
            ed.add_clip(tl, "V1", Clip(
                id=new_id("c"), kind="placeholder", track_id="V1", start=a, duration=dur,
                name=f"LIPSYNC {lip_n:02d}", color=KIND_COLOR["lipsync"],
                meta={"role": "lipsync", "index": lip_n, "shot": pc.meta.get("shot", "C")}), overwrite=False)
            ed.add_marker(tl, a, f"LS{lip_n:02d}", KIND_COLOR["lipsync"])
        else:
            asset = _asset_for(project, tpl, pc.path)
            ed.add_clip(tl, "V1", Clip(
                id=new_id("c"), kind="video", track_id="V1", start=a, duration=dur, asset_id=asset.id,
                name=asset.name, color=KIND_COLOR.get(pc.kind, "#64748b"), mute=True,   # el audio es la canción
                meta={"role": pc.kind, "judge": pc.judge, **{k: v for k, v in pc.meta.items()}}),
                overwrite=False)
    total = max((c.end for c in tl.track("V1").clips), default=0)
    ed.add_clip(tl, "A1", Clip(id=new_id("c"), kind="audio", track_id="A1", start=0, duration=total,
                               asset_id=song.id, name=song.name, color="#8b5cf6"), overwrite=False)
    project.timeline = tl
    return tl


def create_timeline_from_template(project: Project, tpl: Template, song: MediaAsset,
                                  seed: Optional[int] = None) -> Plan:
    """Genera el timeline completo. Lanza ValueError con un mensaje claro si la plantilla está incompleta."""
    if song.duration_s <= 5:
        raise ValueError("Audio de canción demasiado corto (o sin duración conocida).")
    song = project.add_asset(song)          # devuelve el medio ya registrado si la ruta existía
    plan = plan_timeline(song.duration_s, tpl, seed)
    apply_plan(project, tpl, plan, song)
    return plan
