"""Operaciones de edición sobre un Timeline (puras: sin UI, sin ffmpeg).

Semántica estilo DaVinci:
  - colocar / mover = SOBRESCRIBIR (recorta o elimina lo que queda debajo)
  - cuchilla (split), recorte de bordes, borrar (con o sin ripple), duplicar
  - snap a bordes de clips, marcadores y playhead
  - auto-corte por cambios de escena (frame exacto)
"""
from __future__ import annotations

import bisect

import dataclasses
from typing import Iterable, List, Optional, Tuple

from .models import Clip, Marker, Timeline, Track, new_id


# ── búsqueda ───────────────────────────────────────────────────────
def find_clip(tl: Timeline, clip_id: str) -> Tuple[Track, Clip]:
    for t in tl.tracks:
        for c in t.clips:
            if c.id == clip_id:
                return t, c
    raise KeyError(f"Clip inexistente: {clip_id}")


def clips_at(tl: Timeline, frame: int) -> List[Clip]:
    return [c for t in tl.tracks for c in t.clips if c.start <= frame < c.end]


def top_video_clip_at(tl: Timeline, frame: int) -> Optional[Clip]:
    """Clip visible más alto (pistas de vídeo posteriores en la lista = capas superiores)."""
    best = None
    for t in tl.tracks:
        if t.kind != "video" or t.hidden:
            continue
        for c in t.clips:
            if c.enabled and c.start <= frame < c.end:
                best = c
    return best


def _sort(track: Track) -> None:
    track.clips.sort(key=lambda c: (c.start, c.id))


# ── sobrescritura ──────────────────────────────────────────────────
def make_room(track: Track, start: int, end: int, exclude: Iterable[str] = ()) -> None:
    """Libera [start, end) en la pista recortando / partiendo / eliminando lo que solape."""
    exclude = set(exclude)
    out: List[Clip] = []
    for c in track.clips:
        if c.id in exclude or c.end <= start or c.start >= end:
            out.append(c)
            continue
        if c.start < start and c.end > end:                 # el hueco cae dentro: partir en dos
            right = _clone(c)
            _set_start_keep_source(right, end)
            c.duration = start - c.start
            out += [c, right]
        elif c.start < start:                               # solape por la derecha del clip
            c.duration = start - c.start
            out.append(c)
        elif c.end > end:                                   # solape por la izquierda del clip
            _set_start_keep_source(c, end)
            out.append(c)
        # si queda totalmente cubierto: se elimina
    track.clips = out
    _sort(track)


def _clone(c: Clip) -> Clip:
    n = Clip.from_dict(dataclasses.asdict(c))
    n.id = new_id("c")
    return n


def _set_start_keep_source(c: Clip, new_start: int) -> None:
    """Mueve el borde izquierdo sin mover el contenido (recorte de cabeza)."""
    delta = new_start - c.start
    c.source_in += int(round(delta * c.speed))
    c.duration -= delta
    c.start = new_start


# ── colocar / mover ────────────────────────────────────────────────
def add_clip(tl: Timeline, track_id: str, clip: Clip, overwrite: bool = True) -> Clip:
    tr = tl.track(track_id)
    if tr.locked:
        raise PermissionError(f"Pista {tr.name} bloqueada")
    clip.track_id = track_id
    clip.duration = max(1, int(clip.duration))
    clip.start = max(0, int(clip.start))
    if overwrite:
        make_room(tr, clip.start, clip.end)
    tr.clips.append(clip)
    _sort(tr)
    return clip


def move_clip(tl: Timeline, clip_id: str, new_start: int, new_track_id: Optional[str] = None,
              overwrite: bool = True) -> Clip:
    src, c = find_clip(tl, clip_id)
    dst = tl.track(new_track_id) if new_track_id else src
    if src.locked or dst.locked:
        raise PermissionError("Pista bloqueada")
    if dst.kind != src.kind and not (c.kind == "placeholder" and dst.kind == "video"):
        raise ValueError("No se puede mover entre pistas de distinto tipo")
    src.clips = [x for x in src.clips if x.id != clip_id]
    c.start = max(0, int(new_start))
    c.track_id = dst.id
    if overwrite:
        make_room(dst, c.start, c.end)
    dst.clips.append(c)
    _sort(dst)
    return c


# ── cuchilla / recortes ────────────────────────────────────────────
def split_clip(tl: Timeline, clip_id: str, frame: int) -> Optional[Clip]:
    """Parte un clip en `frame`. Devuelve el clip derecho (o None si el corte cae en un borde)."""
    tr, c = find_clip(tl, clip_id)
    if tr.locked or not (c.start < frame < c.end):
        return None
    right = _clone(c)
    _set_start_keep_source(right, frame)
    c.duration = frame - c.start
    tr.clips.append(right)
    _sort(tr)
    return right


def split_at_playhead(tl: Timeline, frame: int, only_ids: Optional[Iterable[str]] = None) -> int:
    """Cuchilla en el playhead sobre los clips indicados (o sobre todos los de pistas no bloqueadas)."""
    ids = set(only_ids) if only_ids else None
    n = 0
    for c in list(clips_at(tl, frame)):
        if ids is not None and c.id not in ids:
            continue
        if split_clip(tl, c.id, frame):
            n += 1
    return n


def trim_start(tl: Timeline, clip_id: str, new_start: int) -> Clip:
    tr, c = find_clip(tl, clip_id)
    new_start = max(0, min(int(new_start), c.end - 1))
    if c.kind in ("video", "audio") and c.source_in + int(round((new_start - c.start) * c.speed)) < 0:
        new_start = c.start - int(c.source_in / c.speed)          # no se puede sacar más de lo que hay
    if new_start < c.start:
        make_room(tr, new_start, c.start, exclude=[c.id])
    _set_start_keep_source(c, new_start)
    _sort(tr)
    return c


def trim_end(tl: Timeline, clip_id: str, new_end: int) -> Clip:
    tr, c = find_clip(tl, clip_id)
    new_end = max(c.start + 1, int(new_end))
    if new_end > c.end:
        make_room(tr, c.end, new_end, exclude=[c.id])
    c.duration = new_end - c.start
    _sort(tr)
    return c


# ── borrar / duplicar / ripple ─────────────────────────────────────
def delete_clip(tl: Timeline, clip_id: str, ripple: bool = False) -> None:
    tr, c = find_clip(tl, clip_id)
    if tr.locked:
        raise PermissionError(f"Pista {tr.name} bloqueada")
    tr.clips = [x for x in tr.clips if x.id != clip_id]
    if ripple:
        for x in tr.clips:
            if x.start >= c.end:
                x.start -= c.duration
    _sort(tr)


def duplicate_clip(tl: Timeline, clip_id: str) -> Clip:
    tr, c = find_clip(tl, clip_id)
    n = _clone(c)
    n.start = c.end
    make_room(tr, n.start, n.end)
    tr.clips.append(n)
    _sort(tr)
    return n


def close_gaps(tl: Timeline, track_id: str) -> None:
    """Elimina huecos de una pista llevando cada clip pegado al anterior."""
    tr = tl.track(track_id)
    t = 0
    for c in sorted(tr.clips, key=lambda x: x.start):
        c.start = t
        t = c.end


def set_clip_props(tl: Timeline, clip_id: str, **props) -> Clip:
    """Edita propiedades del inspector. Acepta claves 'transform.<campo>'."""
    _tr, c = find_clip(tl, clip_id)
    for k, v in props.items():
        if k.startswith("transform."):
            setattr(c.transform, k.split(".", 1)[1], v)
        elif k in ("start", "duration", "source_in"):
            setattr(c, k, max(0 if k != "duration" else 1, int(v)))
        elif hasattr(c, k):
            setattr(c, k, v)
        else:
            raise KeyError(k)
    return c


# ── snap ───────────────────────────────────────────────────────────
def snap_points(tl: Timeline, exclude: Iterable[str] = (), playhead: Optional[int] = None) -> List[int]:
    exclude = set(exclude)
    pts = {0}
    for c in tl.all_clips():
        if c.id not in exclude:
            pts.update((c.start, c.end))
    pts.update(m.frame for m in tl.markers)
    if playhead is not None:
        pts.add(playhead)
    return sorted(pts)


def _nearest(points: List[int], x: int) -> Optional[int]:
    """Punto más cercano a x en una lista ORDENADA (bisect: O(log n))."""
    if not points:
        return None
    i = bisect.bisect_left(points, x)
    cand = points[max(0, i - 1):i + 1]
    return min(cand, key=lambda p: abs(p - x))


def snap(frame: int, points: List[int], threshold: int) -> int:
    best = _nearest(sorted(points), frame)
    return best if best is not None and abs(best - frame) <= threshold else frame


# ── auto-corte por escenas ─────────────────────────────────────────
def auto_cut_scenes(tl: Timeline, clip_id: str, scene_times_s: List[float]) -> int:
    """Corta el clip en los cambios de escena detectados (tiempos en SEGUNDOS del medio fuente).

    Cada tiempo se convierte al frame exacto del medio y de ahí a su posición en el timeline.
    """
    _tr, c = find_clip(tl, clip_id)
    cuts = []
    for ts in scene_times_s:
        src_f = int(round(ts * tl.fps))                         # frame del medio
        tl_f = c.start + int(round((src_f - c.source_in) / c.speed))
        if c.start < tl_f < c.end:
            cuts.append(tl_f)
    n, current = 0, c.id
    for f in sorted(set(cuts)):
        right = split_clip(tl, current, f)
        if right is None:
            continue
        current = right.id
        n += 1
    return n


def add_marker(tl: Timeline, frame: int, name: str = "", color: str = "#e5484d") -> Marker:
    m = Marker(frame=int(frame), name=name, color=color)
    tl.markers.append(m)
    tl.markers.sort(key=lambda x: x.frame)
    return m


# ═══════════════════════════════════════════════════════════════════
# Edición multi-clip, imán con guía y cuchilla magnética (estilo DaVinci)
# ═══════════════════════════════════════════════════════════════════
def row_order(tl: Timeline) -> List[Track]:
    """Orden de las filas en pantalla: vídeo de arriba (V2) a abajo (V1) y luego audio (A1, A2…)."""
    return [t for t in reversed(tl.tracks) if t.kind == "video"] + [t for t in tl.tracks if t.kind == "audio"]


def snap_hit(frame: int, points: List[int], threshold: int) -> Tuple[int, Optional[int]]:
    """Como `snap`, pero devuelve también el punto al que se pegó (None si no hubo imán)."""
    best = _nearest(points, frame)
    if best is not None and abs(best - frame) <= threshold:
        return best, best
    return frame, None


def group_snap(tl: Timeline, ids: Iterable[str], delta: int, threshold: int,
               playhead: Optional[int] = None) -> Tuple[int, Optional[int]]:
    """Imán para mover un GRUPO: prueba el inicio y el final de cada clip movido contra los bordes de
    los clips que NO se mueven, marcadores y playhead. Devuelve (delta_ajustado, frame_del_imán|None)."""
    ids = set(ids)
    moving = [c for c in tl.all_clips() if c.id in ids]
    if not moving:
        return delta, None
    pts = snap_points(tl, exclude=ids, playhead=playhead)
    best = None                                   # (distancia, delta_corregido, punto)
    for c in moving:
        for edge in (c.start + delta, c.end + delta):
            p = _nearest(pts, edge)
            if p is None:
                continue
            d = abs(p - edge)
            if d <= threshold and (best is None or d < best[0]):
                best = (d, delta + (p - edge), p)
    # nunca dejar que el grupo salga por la izquierda del timeline
    left = min(c.start for c in moving)
    if best is not None and left + best[1] >= 0:
        return best[1], best[2]
    return max(delta, -left), None


def move_clips(tl: Timeline, ids: Iterable[str], delta: int, row_delta: int = 0, overwrite: bool = True) -> int:
    """Mueve varios clips a la vez conservando sus posiciones relativas.

    `row_delta` desplaza las filas (+1 = una pista hacia abajo en pantalla) SOLO a los clips cuya pista destino
    es del mismo tipo; si algún clip no puede cambiar de pista no se mueve ninguno (operación atómica).
    Sobrescribe lo que quede debajo (como DaVinci). Devuelve cuántos clips se movieron.
    """
    ids = list(dict.fromkeys(ids))
    rows = row_order(tl)
    idx = {t.id: i for i, t in enumerate(rows)}
    plan = []
    for cid in ids:
        src, c = find_clip(tl, cid)
        dst = src
        if row_delta:
            j = idx[src.id] + row_delta
            if j < 0 or j >= len(rows):
                raise ValueError("No hay pista en esa dirección")
            dst = rows[j]
            if dst.kind != src.kind and not (c.kind == "placeholder" and dst.kind == "video"):
                raise ValueError("No se puede mover entre pistas de distinto tipo")
        if src.locked or dst.locked:
            raise PermissionError("Pista bloqueada")
        plan.append((c, src, dst))
    if not plan:
        return 0
    delta = max(int(delta), -min(c.start for c, _s, _d in plan))          # no salir por la izquierda
    moving = {c.id for c, _s, _d in plan}
    for _c, src, _d in plan:
        src.clips = [x for x in src.clips if x.id not in moving]
    for c, _src, dst in sorted(plan, key=lambda p: p[0].start):
        c.start += delta
        c.track_id = dst.id
        if overwrite:
            make_room(dst, c.start, c.end)
        dst.clips.append(c)
        _sort(dst)
    return len(plan)


def clips_in_rect(tl: Timeline, f0: int, f1: int, row0: int, row1: int) -> List[str]:
    """Clips tocados por el rectángulo de selección (marquesina): frames [f0,f1], filas [row0,row1]."""
    f0, f1 = sorted((int(f0), int(f1)))
    row0, row1 = sorted((int(row0), int(row1)))
    rows = row_order(tl)
    out = []
    for i, t in enumerate(rows):
        if row0 <= i <= row1:
            out += [c.id for c in t.clips if c.start < f1 and c.end > f0]
    return out


def clip_at_row(tl: Timeline, row: int, frame: int) -> Optional[Clip]:
    rows = row_order(tl)
    if not (0 <= row < len(rows)):
        return None
    for c in rows[row].clips:
        if c.start <= frame < c.end:
            return c
    return None


def blade_frame(tl: Timeline, frame: int, threshold: int, playhead: Optional[int] = None,
                snap_on: bool = True) -> Tuple[int, Optional[int]]:
    """Frame donde cortará la cuchilla: se pega a bordes de clips, marcadores y playhead (como Resolve).
    Devuelve (frame, punto_del_imán|None) para dibujar la línea de corte."""
    frame = max(0, int(frame))
    if not snap_on:
        return frame, None
    return snap_hit(frame, snap_points(tl, playhead=playhead), threshold)
