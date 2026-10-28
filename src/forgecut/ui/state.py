"""Estado de la aplicación: un único objeto compartido por todos los paneles.

La UI nunca toca el modelo directamente: llama a métodos de AppState, que hacen commit al
historial (deshacer/rehacer), ejecutan la operación pura de core.editing y notifican a los paneles.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import threading
import time
from typing import Callable, Iterable, List, Optional

from ..automation.audio_export import (AudioRange, export_audio_ranges, lipsync_ranges,
                                       selection_ranges)
from ..automation.build_timeline import create_timeline_from_template
from ..automation.planners import Plan
from ..automation.templates import PoolItem, Template, TemplateStore
from ..core import editing as ed
from ..core.history import History
from ..core.models import Clip, MediaAsset, MediaBin, Project, Timeline, new_id
from ..core.project_io import EXT, load_project, save_project
from ..media import get_backend

IMAGE_DEFAULT_S = 5.0


def default_data_dir() -> str:
    d = os.getenv("FLET_APP_STORAGE_DATA")                 # Android / iOS / paquete de escritorio
    if not d:
        d = os.path.join(os.path.expanduser("~"), ".forgecut")
    os.makedirs(d, exist_ok=True)
    return d


class AppState:
    def __init__(self, data_dir: Optional[str] = None, backend=None):
        self.data_dir = data_dir or default_data_dir()
        self.backend = backend or get_backend()
        self.project = Project()
        self.history = History()
        self.templates = TemplateStore(os.path.join(self.data_dir, "templates"))
        self.selection: List[str] = []
        self.playhead = 0
        self.tool = "select"                # select | razor
        self.zoom = 0.5                     # píxeles por frame
        self.snap_on = True
        self.project_path: Optional[str] = None
        self.dirty = False
        self.last_error = ""
        self._subs: List[Callable[[str], None]] = []
        self.drag_asset: Optional[str] = None   # medio que se está arrastrando desde el Media Pool
        self._ctrl_at = 0.0                 # última pulsación de Ctrl (Flet solo avisa al PULSAR, no al soltar)
        self.dispatch = None                 # fn(callable): ejecuta en el bucle de la UI (lo instala app.py)
        self.ui_thread = None
        self._visuals_busy: set = set()
        self._visuals_failed: set = set()    # medios cuya miniatura/onda no se pudo generar (no reintentar)
        self._vis_pool = None
        self._vis_timer = None
        # Media Pool (selección múltiple de medios y carpetas, búsqueda, vista)
        self.edit_mode = "auto"               # auto | move | trim  (botones táctiles del timeline)
        self.live_props: dict = {}            # {clip_id: {campo_transform: valor}} mientras se arrastra un deslizable
        self.pool_sel: List[str] = []         # ids de medios («m…») y carpetas («b…») seleccionados
        self.pool_anchor: Optional[str] = None   # ancla para Shift+clic (rango)
        self.pool_multi = False               # botón «Seleccionar»: cada toque suma/quita (táctil / sin Ctrl)
        self.pool_query = ""                  # filtro por nombre (busca en TODAS las carpetas)
        self.pool_view = "list"               # list | grid
        self.pool_sort = "name"               # name | duration | kind | added
        self.drag_assets: List[str] = []      # selección que se está arrastrando desde el Media Pool
        self.drag_bins: List[str] = []
        self._shift_at = 0.0
        self.multi_mode = False              # botón «multiselección» (equivale a mantener Ctrl)

    # ── observadores ───────────────────────────────────────────────
    def subscribe(self, fn: Callable[[str], None]) -> None:
        self._subs.append(fn)

    def notify(self, kind: str = "all") -> None:
        """Avisa a los paneles. Si se llama desde otro hilo (visuales, soltado, ffmpeg…) se reenvía al hilo de la UI."""
        if self.dispatch is not None and self.ui_thread is not None and threading.get_ident() != self.ui_thread:
            self.dispatch(lambda: self._notify_now(kind))
            return
        self._notify_now(kind)

    def _notify_now(self, kind: str) -> None:
        for fn in list(self._subs):
            try:
                fn(kind)
            except Exception as ex:                       # un panel roto no debe tumbar a los demás
                print(f"[state] observador falló ({kind}): {ex!r}")

    @property
    def tl(self) -> Timeline:
        return self.project.timeline

    # ── ejecución de ediciones con historial ───────────────────────
    def do(self, fn: Callable[[Timeline], object], kind: str = "timeline"):
        """Ejecuta una edición. Devuelve el resultado, o None si falló (mensaje en last_error)."""
        self.history.commit(self.tl)
        try:
            res = fn(self.tl)
        except Exception as ex:                           # PermissionError, ValueError, KeyError…
            self.history.discard()                        # descarta la instantánea inútil
            self.last_error = str(ex) or ex.__class__.__name__
            self.notify("error")
            return None
        self.dirty = True
        self.last_error = ""
        self._prune_selection()
        self.notify(kind)
        return res if res is not None else True

    def undo(self) -> None:
        tl = self.history.undo(self.tl)
        if tl:
            self.project.timeline = tl
            self._prune_selection()
            self.notify("timeline")

    def redo(self) -> None:
        tl = self.history.redo(self.tl)
        if tl:
            self.project.timeline = tl
            self._prune_selection()
            self.notify("timeline")

    def _prune_selection(self) -> None:
        ids = {c.id for c in self.tl.all_clips()}
        self.selection = [i for i in self.selection if i in ids]

    # ── selección / playhead / herramientas ────────────────────────
    def select(self, ids: Iterable[str], additive: bool = False) -> None:
        ids = list(ids)
        if additive and len(ids) == 1:                       # Ctrl+clic: alterna ese clip
            self.selection = ([i for i in self.selection if i != ids[0]] if ids[0] in self.selection
                              else self.selection + ids)
        elif additive:
            self.selection = list(dict.fromkeys(self.selection + ids))
        else:
            self.selection = ids
        self.notify("selection")

    def selected_clips(self) -> List[Clip]:
        sel = set(self.selection)
        return [c for c in self.tl.all_clips() if c.id in sel]

    def set_playhead(self, frame: int, kind: str = "playhead") -> None:
        self.playhead = max(0, int(frame))                  # puede ir más allá del final (para colocar clips)
        self.notify(kind)

    def set_tool(self, tool: str) -> None:
        self.tool = tool
        self.notify("tool")

    def set_edit_mode(self, mode: str) -> None:
        self.edit_mode = mode if mode in ("auto", "move", "trim") else "auto"
        self.notify("tool")

    def set_live(self, clip_id: str, field: str, value: float) -> None:
        """Valor provisional de la transformación (deslizable en movimiento): el visor lo muestra al instante."""
        self.live_props.setdefault(clip_id, {})[field] = float(value)
        self.notify("live")

    def clear_live(self, clip_id: str) -> None:
        if self.live_props.pop(clip_id, None) is not None:
            self.notify("live")

    def set_zoom(self, zoom: float) -> None:
        self.zoom = max(0.0005, min(float(zoom), 12.0))
        self.notify("zoom")

    # ── medios ─────────────────────────────────────────────────────
    def import_media(self, paths: Iterable[str], bin_id: Optional[str] = None) -> List[MediaAsset]:
        """Hace probe y registra los medios. Bloqueante: llamar desde un hilo (asyncio.to_thread)."""
        out = []
        for p in paths:
            try:
                info = self.backend.probe(p)
            except Exception as ex:
                self.last_error = f"{os.path.basename(p)}: {ex}"
                continue
            mid = new_id("m")
            a = self.project.add_asset(MediaAsset(
                id=mid, path=p, kind=info["kind"], name=info["name"], duration_s=info["duration_s"],
                width=info["width"], height=info["height"], fps=info["fps"], has_audio=info["has_audio"]))
            if bin_id in self.project.bins and a.id == mid:       # solo los medios NUEVOS entran en la carpeta abierta
                a.bin_id = bin_id
            out.append(a)
        self.dirty = True
        self.notify("assets")
        for a in out:
            self.ensure_visuals(a.id)
        return out

    def add_asset_to_timeline(self, asset_id: str, at: Optional[int] = None, track: Optional[str] = None) -> Optional[Clip]:
        a = self.project.assets[asset_id]
        fps = self.tl.fps
        at = self.playhead if at is None else at
        dur = max(1, self.tl.to_frames(a.duration_s or IMAGE_DEFAULT_S))
        track = track or ("A1" if a.kind == "audio" else "V1")
        kind = "audio" if a.kind == "audio" else a.kind
        clip = Clip(id=new_id("c"), kind=kind, track_id=track, start=at, duration=dur, asset_id=a.id,
                    name=a.name, color="#8b5cf6" if a.kind == "audio" else "")
        res = self.do(lambda tl: ed.add_clip(tl, track, clip))
        if res:
            self.selection = [clip.id]
            self.notify("selection")
            return clip
        return None

    def drop_asset(self, asset_id: str, frame: int, row: int) -> Optional[Clip]:
        """Suelta un medio del Media Pool sobre el timeline (fila `row`, frame `frame`), con imán a bordes.
        Un audio soltado sobre una fila de vídeo (o al revés) va a la primera pista de su tipo."""
        a = self.project.assets.get(asset_id)
        if a is None:
            return None
        rows = ed.row_order(self.tl)
        want = "audio" if a.kind == "audio" else "video"
        tr = rows[row] if 0 <= row < len(rows) else None
        if tr is None or tr.kind != want:
            tr = next((t for t in rows if t.kind == want), None)
        if tr is None:
            return None
        frame = max(0, int(frame))
        if self.snap_on:
            dur = max(1, self.tl.to_frames(a.duration_s or IMAGE_DEFAULT_S))
            pts = ed.snap_points(self.tl, playhead=self.playhead)
            th = self.snap_threshold()
            f1, p1 = ed.snap_hit(frame, pts, th)
            f2, p2 = ed.snap_hit(frame + dur, pts, th)
            if p1 is not None and (p2 is None or abs(f1 - frame) <= abs(f2 - (frame + dur))):
                frame = f1
            elif p2 is not None:
                frame = max(0, f2 - dur)
        return self.add_asset_to_timeline(asset_id, at=frame, track=tr.id)

    def drop_assets(self, asset_ids: List[str], frame: int, row: int) -> List[Clip]:
        """Soltar VARIOS medios: el primero donde cae el cursor (con imán) y los demás en secuencia a continuación."""
        ids = [i for i in asset_ids if i in self.project.assets]
        if not ids:
            return []
        first = self.drop_asset(ids[0], frame, row)
        if first is None:
            return []
        out, prev = [first], first
        for i in ids[1:]:
            a = self.project.assets[i]
            same = (a.kind == "audio") == (self.tl.track(prev.track_id).kind == "audio")
            c = self.add_asset_to_timeline(i, at=prev.end, track=prev.track_id if same else None)
            if c is None:
                break
            out.append(c)
            prev = c
        return out

    # ── edición ────────────────────────────────────────────────────
    def split_at_playhead(self) -> int:
        ids = self.selection or None
        res = self.do(lambda tl: ed.split_at_playhead(tl, self.playhead, ids))
        return res if isinstance(res, int) and not isinstance(res, bool) else 0

    def split_clip_at(self, clip_id: str, frame: int) -> None:
        self.do(lambda tl: ed.split_clip(tl, clip_id, frame))

    def delete_selected(self, ripple: bool = False) -> None:
        ids = list(self.selection)
        if not ids:
            return

        def _del(tl):
            for i in ids:
                ed.delete_clip(tl, i, ripple=ripple)
        self.do(_del)

    def duplicate_selected(self) -> None:
        ids = list(self.selection)
        self.do(lambda tl: [ed.duplicate_clip(tl, i) for i in ids])

    def move_clip(self, clip_id: str, new_start: int, new_track: Optional[str] = None) -> None:
        if self.snap_on:
            pts = ed.snap_points(self.tl, exclude=[clip_id], playhead=self.playhead)
            _t, c = ed.find_clip(self.tl, clip_id)
            th = max(2, int(8 / max(self.zoom, 0.01)))
            s1, s2 = ed.snap(new_start, pts, th), ed.snap(new_start + c.duration, pts, th) - c.duration
            new_start = s1 if abs(s1 - new_start) <= abs(s2 - new_start) else s2
        self.do(lambda tl: ed.move_clip(tl, clip_id, new_start, new_track))

    def trim_clip(self, clip_id: str, edge: str, frame: int) -> None:
        if self.snap_on:
            pts = ed.snap_points(self.tl, exclude=[clip_id], playhead=self.playhead)
            frame = ed.snap(frame, pts, max(2, int(8 / max(self.zoom, 0.01))))
        fn = ed.trim_start if edge == "start" else ed.trim_end
        self.do(lambda tl: fn(tl, clip_id, frame))

    def set_props(self, clip_id: str, **props) -> None:
        self.do(lambda tl: ed.set_clip_props(tl, clip_id, **props))

    def toggle_track(self, track_id: str, attr: str) -> None:
        def _t(tl):
            t = tl.track(track_id)
            setattr(t, attr, not getattr(t, attr))
        self.do(_t)

    def add_marker(self) -> None:
        self.do(lambda tl: ed.add_marker(tl, self.playhead, f"M{len(tl.markers) + 1}"))


    # ── carpetas del Media Pool ────────────────────────────────────
    def create_bin(self, name: str, parent: Optional[str] = None) -> MediaBin:
        b = MediaBin(id=new_id("b"), name=(name or "").strip() or "Carpeta",
                     parent=parent if parent in self.project.bins else None)
        self.project.bins[b.id] = b
        self.dirty = True
        self.notify("assets")
        return b

    def rename_bin(self, bin_id: str, name: str) -> None:
        if bin_id in self.project.bins and (name or "").strip():
            self.project.bins[bin_id].name = name.strip()
            self.dirty = True
            self.notify("assets")

    def delete_bin(self, bin_id: str) -> None:
        """Borra la carpeta; sus medios y subcarpetas suben a la carpeta padre (no se pierde nada)."""
        b = self.project.bins.pop(bin_id, None)
        if b is None:
            return
        for x in self.project.bins.values():
            if x.parent == bin_id:
                x.parent = b.parent
        for a in self.project.assets.values():
            if a.bin_id == bin_id:
                a.bin_id = b.parent
        self.dirty = True
        self.notify("assets")

    def move_assets(self, asset_ids: Iterable[str], bin_id: Optional[str]) -> int:
        if bin_id is not None and bin_id not in self.project.bins:
            return 0
        n = 0
        for i in asset_ids:
            a = self.project.assets.get(i)
            if a is not None and a.bin_id != bin_id:
                a.bin_id, n = bin_id, n + 1
        if n:
            self.dirty = True
            self.notify("assets")
        return n

    def bin_path(self, bin_id: Optional[str]) -> List[MediaBin]:
        out, seen = [], set()
        while bin_id in self.project.bins and bin_id not in seen:
            seen.add(bin_id)
            out.append(self.project.bins[bin_id])
            bin_id = self.project.bins[bin_id].parent
        return out[::-1]

    def bins_in(self, parent: Optional[str]) -> List[MediaBin]:
        return sorted((b for b in self.project.bins.values() if b.parent == parent), key=lambda b: b.name.lower())

    def assets_in(self, bin_id: Optional[str]) -> List[MediaAsset]:
        return [a for a in self.project.assets.values() if a.bin_id == bin_id]


    # ── Media Pool avanzado: selección, mover, borrar, buscar ──────
    def is_bin(self, item_id: str) -> bool:
        return item_id in self.project.bins

    def bin_descendants(self, bin_id: str) -> set:
        out, todo = set(), [bin_id]
        while todo:
            cur = todo.pop()
            for b in self.project.bins.values():
                if b.parent == cur and b.id not in out:
                    out.add(b.id)
                    todo.append(b.id)
        return out

    def can_move_bin(self, bin_id: str, target: Optional[str]) -> bool:
        """Una carpeta no puede entrar en sí misma ni en una de sus subcarpetas."""
        if bin_id not in self.project.bins or (target is not None and target not in self.project.bins):
            return False
        return target is None or (target != bin_id and target not in self.bin_descendants(bin_id))

    def move_items(self, item_ids: Iterable[str], target: Optional[str]) -> int:
        """Mueve medios Y carpetas a la carpeta `target` (None = raíz). Devuelve cuántos elementos cambiaron."""
        if target is not None and target not in self.project.bins:
            return 0
        n = 0
        for i in dict.fromkeys(item_ids):
            if i in self.project.bins:
                b = self.project.bins[i]
                if b.parent != target and self.can_move_bin(i, target):
                    b.parent, n = target, n + 1
            else:
                a = self.project.assets.get(i)
                if a is not None and a.bin_id != target:
                    a.bin_id, n = target, n + 1
        if n:
            self.dirty = True
            self.pool_sel = [i for i in self.pool_sel if i in self.project.bins or i in self.project.assets]
            self.notify("assets")
        return n

    def asset_usage(self, asset_id: str) -> int:
        return sum(1 for c in self.tl.all_clips() if c.asset_id == asset_id)

    def delete_assets(self, asset_ids: Iterable[str], remove_clips: bool = False):
        """Quita medios del Media Pool. Si alguno está en el timeline y no se pide `remove_clips`, NO se borra
        (se devuelve en `bloqueados`). Con `remove_clips` también se quitan sus clips del timeline (un paso de deshacer).
        Devuelve (borrados, bloqueados)."""
        ids = [i for i in dict.fromkeys(asset_ids) if i in self.project.assets]
        used = {i: [c.id for c in self.tl.all_clips() if c.asset_id == i] for i in ids}
        blocked = [i for i in ids if used[i] and not remove_clips]
        todo = [i for i in ids if i not in blocked]
        if remove_clips:
            clip_ids = [cid for i in todo for cid in used[i]]
            if clip_ids:
                self.do(lambda tl: [ed.delete_clip(tl, cid) for cid in clip_ids])
        for i in todo:
            self.project.assets.pop(i, None)
        if todo:
            self.pool_sel = [i for i in self.pool_sel if i not in todo]
            self.dirty = True
            self.notify("assets")
        return todo, blocked

    def rename_asset(self, asset_id: str, name: str) -> None:
        a = self.project.assets.get(asset_id)
        if a is not None and (name or "").strip():
            a.name = name.strip()
            self.dirty = True
            self.notify("assets")

    def bin_count(self, bin_id: Optional[str], recursive: bool = True) -> int:
        ids = {bin_id} | (self.bin_descendants(bin_id) if (recursive and bin_id) else set())
        return sum(1 for a in self.project.assets.values() if a.bin_id in ids) + \
            (sum(1 for b in self.project.bins.values() if b.parent in ids) if not recursive else 0)

    def _sorted_assets(self, assets: List[MediaAsset]) -> List[MediaAsset]:
        key = {"duration": lambda a: -(a.duration_s or 0), "kind": lambda a: (a.kind or "", (a.name or "").lower()),
               "added": lambda a: list(self.project.assets).index(a.id)}.get(self.pool_sort, lambda a: (a.name or "").lower())
        return sorted(assets, key=key)

    def pool_items(self, cur: Optional[str]):
        """(carpetas, medios) a mostrar. Sin búsqueda: contenido de `cur`. Con búsqueda: coincidencias en TODO el pool."""
        q = (self.pool_query or "").strip().lower()
        if not q:
            return self.bins_in(cur), self._sorted_assets(self.assets_in(cur))
        bins = sorted((b for b in self.project.bins.values() if q in (b.name or "").lower()), key=lambda b: (b.name or "").lower())
        return bins, self._sorted_assets([a for a in self.project.assets.values() if q in (a.name or "").lower()])

    def pool_click(self, item_id: str, visible_order: List[str], additive: bool = False, rng: bool = False) -> None:
        """Clic en el Media Pool: simple = solo ese; Ctrl = suma/quita; Shift = rango desde el ancla."""
        sel = list(self.pool_sel)
        if rng and self.pool_anchor in visible_order and item_id in visible_order:
            a, b = sorted((visible_order.index(self.pool_anchor), visible_order.index(item_id)))
            span = visible_order[a:b + 1]
            sel = list(dict.fromkeys(sel + span)) if additive else span
        elif additive:
            sel = [i for i in sel if i != item_id] if item_id in sel else sel + [item_id]
            self.pool_anchor = item_id
        else:
            sel, self.pool_anchor = [item_id], item_id
        self.pool_sel = sel
        self.notify("pool_sel")

    def pool_select_all(self, visible_order: List[str]) -> None:
        self.pool_sel = list(visible_order)
        self.notify("pool_sel")

    def pool_clear(self) -> None:
        if self.pool_sel:
            self.pool_sel, self.pool_anchor = [], None
            self.notify("pool_sel")

    def pool_selected_assets(self) -> List[str]:
        return [i for i in self.pool_sel if i in self.project.assets]

    def add_assets_to_timeline(self, asset_ids: Iterable[str], at: Optional[int] = None) -> List[Clip]:
        """Añade varios medios EN SECUENCIA (cada uno justo donde acaba el anterior), en la pista de su tipo."""
        out, pos = [], self.playhead if at is None else at
        for i in asset_ids:
            c = self.add_asset_to_timeline(i, at=pos)
            if c is not None:
                out.append(c)
                pos = c.end
        return out

    # ── navegación del playhead (imán, pasos de frame, puntos de edición) ──
    def snap_playhead(self, frame: int):
        """(frame, punto|None): el playhead se pega a bordes de clips y marcadores (el propio playhead no cuenta)."""
        if not self.snap_on:
            return int(frame), None
        return ed.snap_hit(int(frame), ed.snap_points(self.tl), self.snap_threshold())

    def step_frames(self, n: int) -> None:
        self.set_playhead(self.playhead + int(n))

    def goto_edit(self, direction: int) -> None:
        """Salta al punto de edición anterior (−1) o siguiente (+1): bordes de clips y marcadores."""
        pts = sorted(set(ed.snap_points(self.tl)) | {self.tl.duration})
        if direction > 0:
            nxt = [p for p in pts if p > self.playhead]
            self.set_playhead(nxt[0] if nxt else self.tl.duration)
        else:
            prv = [p for p in pts if p < self.playhead]
            self.set_playhead(prv[-1] if prv else 0)

    def select_all(self) -> None:
        self.select([c.id for c in self.tl.all_clips()])

    # ── Ctrl para multiselección ───────────────────────────────────
    CTRL_WINDOW_S = 6.0

    def note_ctrl(self) -> None:
        """Llamar al recibir una tecla Ctrl/Cmd. Flet no envía «tecla soltada», así que Ctrl se considera
        mantenido mientras sigan llegando repeticiones (el sistema repite la tecla al mantenerla)."""
        self._ctrl_at = time.monotonic()

    def release_ctrl(self) -> None:
        self._ctrl_at = 0.0

    def note_shift(self) -> None:
        self._shift_at = time.monotonic()

    def release_shift(self) -> None:
        self._shift_at = 0.0

    def shift_held(self) -> bool:
        return (time.monotonic() - self._shift_at) < 3.0

    def ctrl_held(self) -> bool:
        """Flet no avisa al SOLTAR Ctrl: se considera pulsado 2,5 s tras la última señal (cada clic aditivo la renueva)
        o siempre que esté activo el botón de multiselección."""
        if self.multi_mode:
            return True
        held = (time.monotonic() - self._ctrl_at) < self.CTRL_WINDOW_S
        if held:
            self._ctrl_at = time.monotonic()
        return held

    # ── miniaturas y forma de onda (caché en disco, generadas en segundo plano) ──
    def _visual_path(self, asset, kind: str) -> str:
        try:
            st = os.stat(asset.path)
            key = f"{asset.path}|{st.st_size}|{int(st.st_mtime)}|{kind}"
        except OSError:
            key = f"{asset.path}|{kind}"
        h = hashlib.md5(key.encode("utf-8")).hexdigest()[:16]
        d = os.path.join(self.data_dir, "cache", "visuals")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f"{h}.{kind}.{'jpg' if kind == 'film' else 'png'}")

    def asset_visual(self, asset_id: str):
        """(ruta_imagen, tipo) si ya está generada: 'film' (vídeo/imagen) o 'wave' (audio); si no, None."""
        a = self.project.assets.get(asset_id)
        if a is None:
            return None
        kind = "wave" if a.kind == "audio" else "film"
        p = self._visual_path(a, kind)
        return (p, kind) if os.path.exists(p) else None

    def ensure_visuals(self, asset_id: str) -> None:
        """Genera (cola de 2 hilos) la tira de miniaturas / forma de onda del medio y avisa UNA vez a la UI."""
        a = self.project.assets.get(asset_id)
        if (a is None or asset_id in self._visuals_busy or asset_id in self._visuals_failed
                or self.asset_visual(asset_id)):
            return
        if not self.backend.available() or a.kind == "image":
            return
        self._visuals_busy.add(asset_id)
        if self._vis_pool is None:
            from concurrent.futures import ThreadPoolExecutor
            self._vis_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="visuals")
            self._vis_pool_audio = ThreadPoolExecutor(max_workers=1, thread_name_prefix="waves")

        def work():
            try:
                if a.kind == "audio":
                    self.backend.waveform(a.path, self._visual_path(a, "wave"))
                else:
                    self.backend.filmstrip(a.path, self._visual_path(a, "film"), a.duration_s or 1.0)
                self._schedule_visuals_notify()
            except Exception as ex:                      # noqa: BLE001  (sin audio, formato raro…)
                self._visuals_failed.add(asset_id)
                print(f"[visuals] {a.name}: {ex}")
            finally:
                self._visuals_busy.discard(asset_id)
        (self._vis_pool_audio if a.kind == "audio" else self._vis_pool).submit(work)

    def _schedule_visuals_notify(self) -> None:
        """Agrupa avisos: con 100 medios se repinta el timeline una vez cada 0,6 s, no 100 veces."""
        if self._vis_timer is not None:
            return

        def fire():
            self._vis_timer = None
            self.notify("visuals")
        self._vis_timer = threading.Timer(0.6, fire)
        self._vis_timer.daemon = True
        self._vis_timer.start()

    # ── edición de grupo / arrastre / marquesina / cuchilla ────────
    def snap_threshold(self) -> int:
        return max(2, int(8 / max(self.zoom, 0.01)))

    def drag_preview(self, ids: Iterable[str], dx_frames: int):
        """Durante el arrastre: (delta_ajustado, frame_del_imán|None). No toca el modelo ni el historial."""
        ids = list(ids)
        if not self.snap_on:
            left = min((c.start for c in self.tl.all_clips() if c.id in set(ids)), default=0)
            return max(dx_frames, -left), None
        return ed.group_snap(self.tl, ids, int(dx_frames), self.snap_threshold(), self.playhead)

    def commit_drag(self, ids: Iterable[str], dx_frames: int, row_delta: int = 0) -> None:
        """Suelta el arrastre: un único paso de historial para todo el grupo."""
        ids = list(ids)
        d, _snap = self.drag_preview(ids, dx_frames)
        if d == 0 and row_delta == 0:
            return
        if self.do(lambda tl: ed.move_clips(tl, ids, d, row_delta)) is None and row_delta and d:
            self.do(lambda tl: ed.move_clips(tl, ids, d, 0))           # no cabe en otra pista: al menos mover en horizontal

    def select_rect(self, f0: int, f1: int, row0: int, row1: int, additive: bool = False) -> None:
        self.select(ed.clips_in_rect(self.tl, f0, f1, row0, row1), additive=additive and True)

    def blade_preview(self, frame: int):
        return ed.blade_frame(self.tl, frame, self.snap_threshold(), self.playhead, self.snap_on)

    def blade_cut(self, row: int, frame: int) -> bool:
        """Cuchilla: corta el clip que está bajo el cursor (fila `row`) en el frame con imán."""
        c = ed.clip_at_row(self.tl, row, frame)
        if c is None or self.tl.track(c.track_id).locked:
            return False
        f, _p = self.blade_preview(frame)
        if not (c.start < f < c.end):                # el imán lo llevó a un borde: no hay nada que cortar
            return False
        self.do(lambda tl: ed.split_clip(tl, c.id, f))
        return True

    def export_selected_audio(self, out_dir: str, fmt: str = "mp3", on_item=None) -> List[str]:
        """Exporta como archivos de audio el tramo de cada clip seleccionado (botón del editor)."""
        if not self.selection:
            raise ValueError("Selecciona uno o varios clips (Ctrl+clic o marquesina).")
        return self.export_audio(out_dir, fmt, True, on_item)

    # ── auto-corte de escenas ──────────────────────────────────────
    async def auto_cut_selected(self, threshold: float = 0.3) -> int:
        """Detecta cambios de escena en los clips de vídeo seleccionados y corta en el frame exacto."""
        total = 0
        for c in [c for c in self.selected_clips() if c.kind == "video" and c.asset_id]:
            asset = self.project.assets[c.asset_id]
            times = await asyncio.to_thread(self.backend.detect_scenes, asset.path, threshold)
            n = self.do(lambda tl, cid=c.id, ts=times: ed.auto_cut_scenes(tl, cid, ts))
            total += n if isinstance(n, int) and not isinstance(n, bool) else 0
        return total

    # ── plantillas / timeline automático ───────────────────────────
    def create_timeline(self, tpl: Template, song_path: str, seed: Optional[int] = None) -> Plan:
        """Genera el timeline desde una plantilla. Bloqueante (hace probe de la canción)."""
        info = self.backend.probe(song_path)
        song = MediaAsset(id=new_id("m"), path=song_path, kind="audio", name=info["name"], duration_s=info["duration_s"])
        self.history.commit(self.tl)
        try:
            plan = create_timeline_from_template(self.project, tpl, song, seed)
        except Exception:
            self.history.discard()
            raise
        self.selection, self.playhead, self.dirty = [], 0, True
        self.notify("all")
        return plan

    async def probe_pool_items(self, paths: List[str]) -> List[PoolItem]:
        def work():
            out = []
            for p in paths:
                try:
                    i = self.backend.probe(p)
                    out.append(PoolItem(p, i["duration_s"], i["has_audio"]))
                except Exception as ex:
                    self.last_error = f"{os.path.basename(p)}: {ex}"
            return out
        return await asyncio.to_thread(work)

    # ── exportación ────────────────────────────────────────────────
    def audio_ranges(self, only_selection: bool) -> List[AudioRange]:
        return selection_ranges(self.project, self.selection) if only_selection else lipsync_ranges(self.project)

    def export_audio(self, out_dir: str, fmt: str, only_selection: bool, on_item=None) -> List[str]:
        return export_audio_ranges(self.project, self.backend, self.audio_ranges(only_selection), out_dir, fmt, on_item)

    def render(self, out_path: str, proxy: bool, progress=None, short_side: Optional[int] = None,
               crf: Optional[int] = None) -> str:
        return self.backend.render(self.project, out_path, progress, proxy, short_side, crf)

    # ── proyecto ───────────────────────────────────────────────────
    def new_project(self, name: str = "Proyecto sin título") -> None:
        self.project, self.history = Project(name=name), History()
        self.selection, self.playhead, self.project_path, self.dirty = [], 0, None, False
        self.notify("all")

    # ── proyectos: nombre, lista y autoguardado ────────────────────
    @property
    def projects_dir(self) -> str:
        return os.path.join(self.data_dir, "projects")

    @staticmethod
    def _safe_name(name: str) -> str:
        n = "".join(ch for ch in (name or "").strip() if ch not in '\\/:*?"<>|').strip(" .")
        return n or "Proyecto sin título"

    def rename_project(self, name: str) -> None:
        name = (name or "").strip()
        if not name:
            return
        self.project.name = name
        cur = self.project_path
        if cur and os.path.exists(cur) and os.path.basename(cur)[:-len(EXT)] != self._safe_name(name):
            try:                                              # el archivo guardado también cambia de nombre
                new = self._unique_path(name)
                os.replace(cur, new)
                self.project_path = new
            except OSError:
                pass
        self.dirty = True
        self.notify("project")

    def _unique_path(self, name: str) -> str:
        base, n = os.path.join(self.projects_dir, self._safe_name(name)), 1
        path = base + EXT
        while os.path.exists(path):
            n += 1
            path = f"{base} {n}{EXT}"
        return path

    def list_projects(self) -> List[tuple]:
        """[(nombre, ruta, fecha_modificación)] de los proyectos guardados, el más reciente primero."""
        out = []
        try:
            for f in os.listdir(self.projects_dir):
                if f.lower().endswith(EXT):
                    fp = os.path.join(self.projects_dir, f)
                    out.append((f[:-len(EXT)], fp, os.path.getmtime(fp)))
        except OSError:
            pass
        return sorted(out, key=lambda x: -x[2])

    def delete_project(self, path: str) -> None:
        """Borra un proyecto guardado. Si es el abierto, se pasa a un proyecto nuevo vacío (sin recrear el archivo)."""
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        if path == self.project_path:
            self.new_project("Proyecto nuevo")

    def rename_saved_project(self, path: str, name: str) -> str:
        """Renombra un proyecto guardado (archivo y nombre interno). Devuelve la ruta resultante."""
        name = (name or "").strip()
        if not name:
            return path
        if path == self.project_path:
            self.rename_project(name)
            return self.save()
        proj = load_project(path)
        proj.name = name
        new = path if os.path.basename(path)[:-len(EXT)] == self._safe_name(name) else self._unique_path(name)
        save_project(proj, new)
        if new != path:
            os.remove(path)
        return new

    def autosave(self) -> bool:
        """Guarda en silencio si hay cambios sin guardar (temporizador, cierre o paso a segundo plano)."""
        if not self.dirty:
            return False
        try:
            self.save()
            return True
        except Exception:                                    # noqa: BLE001  el autoguardado nunca debe romper la app
            return False

    def save(self, path: Optional[str] = None) -> str:
        path = path or self.project_path or self._unique_path(self.project.name)
        self.project_path = save_project(self.project, path)
        self.dirty = False
        self.notify("project")
        return self.project_path

    def open(self, path: str) -> None:
        self.project, self.history = load_project(path), History()
        self.selection, self.playhead, self.project_path, self.dirty = [], 0, path, False
        self.notify("all")
