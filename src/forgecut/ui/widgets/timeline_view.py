"""Timeline estilo DaVinci Resolve.

Qué hace distinto a la versión anterior (y por qué se siente más fluido):
  · Los controles de cada clip se REUTILIZAN entre refrescos si no cambió nada (Flet solo envía diferencias);
    antes cada clic reconstruía todo el timeline.
  · La selección cambia el color del borde EN SITIO (no sustituye controles → no se corta un arrastre en curso).
  · Arrastre de GRUPO en 2 ejes con imán en tiempo real y línea amarilla en el punto donde se pega.
  · Marquesina (arrastrar en zona vacía) y Ctrl+clic para multiselección.
  · Cuchilla: línea de corte que sigue al ratón, se pega a bordes/playhead/marcadores (línea amarilla = imán).
  · Miniaturas (vídeo) y forma de onda (audio) como UNA imagen por clip, recortada con source_in.
"""
from __future__ import annotations

import time

import asyncio
import os

import flet as ft

from ...core import editing as ed
from .. import theme as T
from ..compat import (quiet, evt_drop_xy, evt_dx, evt_gxy, evt_dy, evt_x, evt_y, file_image, hard_clip, icon_btn, safe_update, text)

TRACK_H = 48
RULER_H = 28
HEADER_W = 92
HANDLE_W = 7
HANDLE_TOUCH = 30            # borde grande en el modo «Recortar» (táctil)
KIND_COLOR = {"video": "#3b6ea5", "audio": "#4b3b86", "image": "#2f8f6f", "placeholder": "#a33a3a"}
SNAP_COLOR = "#ffd60a"
MAX_STRIP_PX = 40000          # por encima se omite la imagen del clip (zoom enorme) y queda el color liso


def _center():
    try:
        return ft.Alignment(0, 0)
    except Exception:
        try:
            return ft.alignment.center
        except Exception:
            return None


def _gd(**kw):
    """GestureDetector cuyos manejadores no disparan el diff global de página de Flet."""
    for k, v in list(kw.items()):
        if k.startswith("on_") and callable(v):
            kw[k] = quiet(v)
    return ft.GestureDetector(**kw)


def _cursor(name: str):
    mc = getattr(ft, "MouseCursor", None)
    v = getattr(mc, name, None) if mc is not None else None
    return None if isinstance(v, str) else v


class _Ref:
    """Controles vivos de un clip (para mover/colorear sin reconstruir)."""
    __slots__ = ("group", "outline", "sig")

    def __init__(self, group, outline, sig):
        self.group, self.outline, self.sig = group, outline, sig


class TimelineView:
    ASYNC_DROP = True
    INTENT_SCROLL = bool(os.environ.get("ANDROID_ROOT"))   # autodesplazamiento solo con empuje del dedo (Android)
    _push = 0.0
    viewport_w = 0.0              # ancho visible del timeline (lo fija el Workspace)

    def __init__(self, state):
        self.s = state
        self.headers = ft.Column(spacing=0, width=HEADER_W)
        self.stack = ft.Stack(width=2000, height=RULER_H + 4 * TRACK_H)
        # El DragTarget envuelve al Stack en vez de ser una capa superpuesta: una capa encima del timeline
        # intercepta los clics/arrastres y ningún clip ni la cuchilla recibían eventos.
        self.dropzone = ft.DragTarget(group="media", content=self.stack, on_accept=quiet(self._drop))
        self._auto = None
        self.scroller = ft.Row([self.dropzone], scroll=ft.ScrollMode.AUTO, expand=True,
                               vertical_alignment=ft.CrossAxisAlignment.START,
                               on_scroll=quiet(self._on_scroll), scroll_interval=80)
        self.root = ft.Container(content=ft.Row([self.headers, self.scroller], spacing=0, expand=True),
                                 bgcolor=T.PANEL, expand=True)
        self._refs: dict = {}                  # clip_id → _Ref
        self._static: dict = {}                # "ruler" / "bg" / "razor" → (sig, control)
        self._hdr_sig = None
        self._drag = None
        self._marq = None
        self._refreshes = 0
        self._ruler_drag = 0.0
        self._view = (0.0, 4000.0)             # (desplazamiento, ancho visible) en px: solo se construye lo visible
        self._win = (0.0, 0.0)                 # ventana construida [lo, hi] en px
        self._last: dict = {}                  # limitador de frecuencia por acción
        self._origin = None                    # (x, y) global del origen del Stack, calibrado con el ratón
        self._playhead = ft.Container(left=0, top=0, width=2, height=RULER_H + 4 * TRACK_H, bgcolor=T.PLAYHEAD)
        self._guide = ft.Container(left=0, top=0, width=1, height=10, bgcolor=SNAP_COLOR, visible=False)
        self._blade = ft.Container(left=0, top=0, width=1, height=10, bgcolor="#ffffff", visible=False)
        self._blade_icon = ft.Icon(getattr(ft.Icons, "CONTENT_CUT", ft.Icons.CIRCLE), size=14, color="#ffffff",
                                   left=0, top=RULER_H, visible=False)
        self._marquee = ft.Container(left=0, top=0, width=0, height=0, bgcolor=T.ACCENT_2, opacity=0.28, visible=False)
        state.subscribe(self._on_state)
        self.refresh()

    # ── sincronización con el estado ───────────────────────────────
    def _on_state(self, kind: str) -> None:
        if kind == "playhead":
            self.update_playhead()
        elif kind == "selection":
            self._apply_selection()
        elif kind in ("timeline", "zoom", "all", "tool", "visuals"):
            self.refresh()

    def rows(self):
        return ed.row_order(self.s.tl)

    # ── rendimiento: limitador y virtualización horizontal ─────────
    def _tick(self, name: str, dt: float = 0.03) -> bool:
        """True como mucho cada `dt` s. Los eventos de ratón llegan más rápido de lo que Flet puede pintar; los
        intermedios solo acumulan posición (barato) y así nunca se forma una cola que retrase la interfaz."""
        now = time.perf_counter()
        if now - self._last.get(name, 0.0) >= dt:
            self._last[name] = now
            return True
        return False

    def _on_scroll(self, e) -> None:
        off, vw = getattr(e, "pixels", None), getattr(e, "viewport_dimension", None)
        if off is None:
            return
        self._view = (float(off), float(vw or self._view[1]))
        if self._drag or self._marq:
            return
        lo, hi = self._win
        if self._view[0] < lo or self._view[0] + self._view[1] > hi:
            self.refresh()

    def _window(self, width: float):
        off, vw = self._view
        off = max(0.0, min(off, max(0.0, width - vw)))
        return max(0.0, off - vw), off + 2 * vw

    # ── construcción ───────────────────────────────────────────────
    def refresh(self) -> None:
        self._refreshes += 1
        s, tl, z = self.s, self.s.tl, self.s.zoom
        rows = self.rows()
        width = max(tl.duration, tl.fps * 60) * z + 400
        height = RULER_H + len(rows) * TRACK_H
        idx = {t.id: i for i, t in enumerate(rows)}
        lo, hi = self._win = self._window(width)

        ctrls = [self._ruler(width, z, lo, hi), self._bg(width, height, len(rows))]
        live = set()
        for tr in rows:
            for c in tr.clips:
                if c.end * z < lo or c.start * z > hi:           # fuera de la ventana: no se crea el control
                    continue
                live.add(c.id)
                ctrls.append(self._clip(c, idx[tr.id], tr))
        for cid in [k for k in self._refs if k not in live]:
            del self._refs[cid]
        for m in tl.markers:
            if m.frame * z < lo or m.frame * z > hi:
                continue
            ctrls.append(ft.Container(left=m.frame * z, top=0, width=2, height=height, bgcolor=m.color, opacity=0.8,
                                      tooltip=m.name))
        self._guide.height = self._blade.height = height
        self._playhead.height, self._playhead.left = height, s.playhead * z
        ctrls += [self._marquee, self._guide]
        if s.tool == "razor":
            ctrls += [self._razor(width, height), self._blade, self._blade_icon]
        ctrls.append(self._playhead)
        self.stack.controls, self.stack.width, self.stack.height = ctrls, width, height

        hsig = tuple((t.id, t.name, t.kind, t.locked, t.muted, t.hidden) for t in rows)
        if hsig != self._hdr_sig:
            self._hdr_sig = hsig
            self.headers.controls = [ft.Container(height=RULER_H, bgcolor=T.RULER)] + [self._header(t) for t in rows]
            safe_update(self.headers)
        safe_update(self.stack)

    def update_playhead(self) -> None:
        self._playhead.left = self.s.playhead * self.s.zoom
        safe_update(self._playhead)

    def _apply_selection(self) -> None:
        sel = set(self.s.selection)
        changed = []
        for cid, ref in self._refs.items():
            want = "#ffffff" if cid in sel else None
            if ref.outline.bgcolor != want:
                ref.outline.bgcolor = want
                changed.append(ref.outline)
        safe_update(*changed)

    def _hbtn(self, icon: str, fn, tip: str, color: str):
        """Icono pequeño de cabecera (24 px de ancho, sin el relleno de IconButton): caben bien en pantallas estrechas."""
        return ft.Container(content=ft.Icon(getattr(ft.Icons, icon, ft.Icons.CIRCLE), size=15, color=color), width=24,
                            height=TRACK_H - 8, alignment=_center(), on_click=fn, tooltip=tip)

    def _header(self, tr):
        col = T.TEXT if not tr.muted else T.TEXT_DIM
        eye = "VISIBILITY_OFF" if tr.hidden else "VISIBILITY"
        return ft.Container(height=TRACK_H, bgcolor=T.PANEL_2, padding=ft.Padding(left=4, top=4, right=2, bottom=4), content=ft.Row([
            ft.Container(content=text(tr.name, 12, col, bold=True, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS), expand=True),
            self._hbtn("LOCK" if tr.locked else "LOCK_OPEN", lambda e, t=tr: self.s.toggle_track(t.id, "locked"), "Bloquear",
                       T.ACCENT if tr.locked else T.TEXT_DIM),
            self._hbtn("VOLUME_OFF" if tr.muted else "VOLUME_UP", lambda e, t=tr: self.s.toggle_track(t.id, "muted"), "Silenciar",
                       T.ACCENT if tr.muted else T.TEXT_DIM) if tr.kind == "audio" else
            self._hbtn(eye, lambda e, t=tr: self.s.toggle_track(t.id, "hidden"), "Mostrar/ocultar",
                       T.ACCENT if tr.hidden else T.TEXT_DIM)], spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER))

    # ── regla ──────────────────────────────────────────────────────
    def _ruler(self, width: float, z: float, lo: float = 0.0, hi: float = 1e12):
        fps = self.s.tl.fps
        sig = (round(width), z, fps, round(lo), round(hi))
        hit = self._static.get("ruler")
        if hit and hit[0] == sig:
            return hit[1]
        step = fps
        for mult in (1, 2, 5, 10, 15, 30, 60, 120, 300):        # etiquetas separadas ≥ 80 px
            step = fps * mult
            if step * z >= 80:
                break
        marks, f = [], max(0, int(lo / z) // step * step)
        while f * z < min(width, hi):
            marks.append(ft.Container(left=f * z, top=16, width=1, height=12, bgcolor=T.TEXT_DIM))
            marks.append(ft.Container(left=f * z + 4, top=2, content=text(self.s.tl.timecode(f)[3:], 10, T.TEXT_DIM)))
            f += step
        body = ft.Stack(marks, width=width, height=RULER_H)
        ctl = _gd(content=ft.Container(content=body, bgcolor=T.RULER, width=width, height=RULER_H),
                                 left=0, top=0, width=width, height=RULER_H, drag_interval=24,
                                 on_tap_down=lambda e: self._ruler_seek(evt_x(e), True), hover_interval=60,
                                 on_hover=lambda e: self._calib(e, 0),
                                 on_horizontal_drag_start=lambda e: setattr(self, "_ruler_drag", evt_x(e)),
                                 on_horizontal_drag_update=self._ruler_drag_update,
                                 on_horizontal_drag_end=lambda e: self._ruler_seek(self._ruler_drag, True))
        self._static["ruler"] = (sig, ctl)
        return ctl

    def _calib(self, e, top: float) -> None:
        """Memoriza dónde está el Stack en la ventana (global − local), para convertir el soltado del Media Pool."""
        g = evt_gxy(e)
        if g is not None:
            self._origin = (g[0] - evt_x(e), g[1] - evt_y(e) - top)

    def _ruler_seek(self, x: float, final: bool = False) -> None:
        """Mueve el playhead con imán (línea amarilla mientras se pega a un borde/marcador)."""
        f, pt = self.s.snap_playhead(round(x / self.s.zoom))
        self._show_guide(None if final else pt)
        safe_update(self._guide)
        self.s.set_playhead(f)

    def _ruler_drag_update(self, e) -> None:
        self._ruler_drag = max(0.0, self._ruler_drag + evt_dx(e))
        if self._tick("ruler"):
            self._ruler_seek(self._ruler_drag)

    # ── fondo de pistas: clic = deseleccionar, arrastre = marquesina ──
    def _bg(self, width: float, height: float, n_rows: int):
        sig = (round(width), n_rows)
        hit = self._static.get("bg")
        if hit and hit[0] == sig:
            return hit[1]
        rows = ft.Column([ft.Container(width=width, height=TRACK_H - 1,
                                       bgcolor=T.TRACK_V if t.kind == "video" else T.TRACK_A) for t in self.rows()], spacing=1)
        ctl = _gd(content=ft.Container(content=rows, width=width, height=n_rows * TRACK_H),
                                 left=0, top=RULER_H, width=width, height=n_rows * TRACK_H, drag_interval=24,
                                 on_tap_down=self._bg_tap, hover_interval=60,
                                 on_hover=lambda e: self._calib(e, RULER_H),
                                 on_pan_start=self._marq_start, on_pan_update=self._marq_update, on_pan_end=self._marq_end)
        self._static["bg"] = (sig, ctl)
        return ctl

    def _bg_tap(self, e) -> None:
        if not self.s.ctrl_held():
            self.s.select([])

    def _marq_start(self, e) -> None:
        self._marq = {"x0": evt_x(e), "y0": evt_y(e), "x1": evt_x(e), "y1": evt_y(e), "ctrl": self.s.ctrl_held()}

    def _marq_update(self, e) -> None:
        m = self._marq
        if not m:
            return
        m["x1"] += evt_dx(e)
        m["y1"] += evt_dy(e)
        x0, x1 = sorted((m["x0"], m["x1"]))
        y0, y1 = sorted((m["y0"], m["y1"]))
        if not self._tick("marq"):
            return
        self._marquee.left, self._marquee.top = x0, RULER_H + y0
        self._marquee.width, self._marquee.height = max(1.0, x1 - x0), max(1.0, y1 - y0)
        self._marquee.visible = True
        safe_update(self._marquee)

    def _marq_end(self, e) -> None:
        m, self._marq = self._marq, None
        self._marquee.visible = False
        safe_update(self._marquee)
        if not m:
            return
        z = self.s.zoom
        f0, f1 = round(min(m["x0"], m["x1"]) / z), round(max(m["x0"], m["x1"]) / z)
        r0, r1 = int(min(m["y0"], m["y1"]) // TRACK_H), int(max(m["y0"], m["y1"]) // TRACK_H)
        self.s.select_rect(f0, f1, r0, r1, additive=m["ctrl"])

    # ── soltar desde el Media Pool ─────────────────────────────────
    def _drop(self, e) -> None:
        s = self.s
        aid, s.drag_asset = s.drag_asset or getattr(e, "data", None), None
        ids = list(s.drag_assets) if (len(s.drag_assets) > 1 and (aid is None or aid in s.drag_assets)) else []
        s.drag_assets, s.drag_bins = [], []
        if ids:
            aid = aid or ids[0]
        if not aid:
            return
        try:
            x, y = evt_drop_xy(e)
            g = evt_gxy(e)
            has_local = any(getattr(e, a, None) is not None for a in ("local_position", "local_x"))
            if not has_local:                                     # x/y del evento son globales (ventana)
                ox, oy = self._origin if self._origin else (HEADER_W, None)
                x = (g[0] if g else x) - ox
                y = (g[1] if g else y) - oy - RULER_H if oy is not None else -1.0
                row = int(y // TRACK_H) if oy is not None else -1
            else:
                row = int(max(0.0, y - RULER_H) // TRACK_H)
            frame = round(max(0.0, x) / s.zoom)
            print(f"[drop] frame={frame} fila={row}")

            def work():
                try:
                    c = s.drop_assets(ids, frame, row) if ids else s.drop_asset(aid, frame, row)
                    print("[drop] ok:", getattr(c, "id", None) if not isinstance(c, list) else [x.id for x in c], s.last_error)
                except Exception:                                  # noqa: BLE001
                    import traceback
                    traceback.print_exc()
            if self.ASYNC_DROP:                                   # el manejador vuelve ya: la edición y el repintado
                import threading                                  # corren fuera del hilo de eventos de Flet
                threading.Thread(target=work, daemon=True).start()
            else:
                work()
        except Exception as ex:                                   # noqa: BLE001
            import traceback
            traceback.print_exc()
            s.last_error = f"Soltar falló: {ex}"
            s.notify("error")

    # ── cuchilla ───────────────────────────────────────────────────
    def _razor(self, width: float, height: float):
        sig = (round(width), round(height))
        hit = self._static.get("razor")
        if hit and hit[0] == sig:
            return hit[1]
        ctl = _gd(
            content=ft.Container(width=width, height=height - RULER_H, bgcolor="#01000000"),
            left=0, top=RULER_H, width=width, height=height - RULER_H,
            mouse_cursor=_cursor("PRECISE"), on_hover=self._blade_hover, on_tap_down=self._blade_tap)
        self._static["razor"] = (sig, ctl)
        return ctl

    def _blade_hover(self, e) -> None:
        if not self._tick("blade"):
            return
        z = self.s.zoom
        f, pt = self.s.blade_preview(round(evt_x(e) / z))
        self._blade.left, self._blade_icon.left = f * z, f * z + 3
        self._blade.bgcolor = SNAP_COLOR if pt is not None else "#ffffff"
        self._blade.visible = self._blade_icon.visible = True
        safe_update(self._blade, self._blade_icon)

    def _blade_tap(self, e) -> None:
        z = self.s.zoom
        self.s.blade_cut(int(evt_y(e) // TRACK_H), round(evt_x(e) / z))

    # ── clips ──────────────────────────────────────────────────────
    def _clip(self, c, row: int, tr):
        s, z = self.s, self.s.zoom
        asset = s.project.assets.get(c.asset_id) if c.asset_id else None
        vis = s.asset_visual(c.asset_id) if asset is not None else None
        if asset is not None and vis is None:
            s.ensure_visuals(asset.id)                  # plantillas / proyectos abiertos: genera miniaturas/onda al vuelo
        sig = (c.start, c.duration, c.source_in, c.speed, c.name, c.color, c.enabled, c.kind, row, z, tr.locked,
               vis[0] if vis else None, asset.duration_s if asset else 0, s.edit_mode)
        ref = self._refs.get(c.id)
        if ref and ref.sig == sig:
            return ref.group
        cid = c.id
        w, x, h = max(3.0, c.duration * z), c.start * z, TRACK_H - 7
        color = c.color or KIND_COLOR.get(c.kind, T.CLIP_DEFAULT)
        layers = []
        if vis and asset is not None and asset.duration_s > 0:
            total_w = s.tl.to_frames(asset.duration_s) * z / max(0.01, c.speed)
            if 0 < total_w <= MAX_STRIP_PX:
                img = file_image(vis[0], total_w, h)
                img.left, img.top = -c.source_in * z / max(0.01, c.speed), 0
                layers.append(img)
        label = c.name or c.kind
        layers.append(ft.Container(left=0, top=0, padding=2, bgcolor="#99000000", border_radius=2,
                                   content=text(f"{label}  ·  {c.duration}f", 10, "#ffffff", bold=True, no_wrap=True)))
        body = ft.Container(content=ft.Stack(layers, width=w, height=h), width=w, height=h, bgcolor=color,
                            border_radius=3, opacity=0.45 if not c.enabled else 1.0, **hard_clip())
        outline = ft.Container(content=body, bgcolor="#ffffff" if cid in s.selection else None, padding=1, border_radius=4)
        group = ft.Stack(left=x, top=RULER_H + row * TRACK_H + 3, width=w + 2, height=h + 2)
        main = _gd(
            content=outline, left=0, top=0, width=w + 2, height=h + 2, drag_interval=24,
            mouse_cursor=_cursor("MOVE"),
            on_tap_down=lambda e, i=cid: self._clip_tap(i),
            on_tap=lambda e, i=cid: self._clip_click(i),
            on_pan_start=None if s.edit_mode in ("trim", "move") else (lambda e, i=cid: self._drag_start(i)),
            on_pan_update=None if s.edit_mode in ("trim", "move") else self._drag_update,
            on_pan_end=None if s.edit_mode in ("trim", "move") else self._drag_end,
            **({"on_horizontal_drag_start": lambda e, i=cid: self._mv_start(i, "h"),
                "on_horizontal_drag_update": self._drag_update, "on_horizontal_drag_end": lambda e: self._mv_end("h"),
                "on_vertical_drag_start": lambda e, i=cid: self._mv_start(i, "v"),
                "on_vertical_drag_update": self._drag_update, "on_vertical_drag_end": lambda e: self._mv_end("v")}
               if s.edit_mode == "move" else {}))
        if s.edit_mode == "move":                          # modo Mover: todo el clip es para arrastrar (sin bordes)
            hs = []
        elif s.edit_mode == "trim":                        # modo Recortar: bordes grandes y visibles
            hw = max(8.0, min(float(HANDLE_TOUCH), (w - 2) / 2))
            hs = [] if tr.locked else [self._handle(cid, "start", c, w, h, x, hw, 0.7), self._handle(cid, "end", c, w, h, x, hw, 0.7)]
        else:
            hs = [self._handle(cid, "start", c, w, h, x), self._handle(cid, "end", c, w, h, x)] if w > 24 and not tr.locked else []
        group.controls = [main] + hs
        self._refs[cid] = _Ref(group, outline, sig)
        return group

    def _clip_tap(self, cid: str) -> None:
        """Al PULSAR sobre un clip. Con Ctrl alterna ese clip. Sin Ctrl: si ya forma parte de la selección se CONSERVA
        el grupo (para poder arrastrarlo entero); solo si no estaba seleccionado pasa a ser el único."""
        s = self.s
        if s.ctrl_held():
            s.select([cid], additive=True)
        elif cid not in s.selection:
            s.select([cid])

    def _clip_click(self, cid: str) -> None:
        """Clic SIN arrastre sobre un clip de un grupo (sin Ctrl): ahora sí se queda solo ese clip (como DaVinci)."""
        s = self.s
        if not s.ctrl_held() and len(s.selection) > 1 and cid in s.selection:
            s.select([cid])

    # ── arrastre de grupo (2 ejes + imán) ──────────────────────────
    def _drag_start(self, cid: str) -> None:
        s = self.s
        if cid not in s.selection:
            s.select([cid], additive=s.ctrl_held())
        ids = list(s.selection) if cid in s.selection else [cid]
        self._drag = {"ids": ids, "dx": 0.0, "dy": 0.0,
                      "base": {i: (self._refs[i].group.left, self._refs[i].group.top) for i in ids if i in self._refs}}
        if self._auto is not None:
            self._auto["on"] = False
        import threading
        self._push = 0.0
        self._auto = {"on": True, "rate": 0.0}
        threading.Thread(target=self._auto_loop, args=(self._auto,), daemon=True).start()

    # Modo Mover (táctil): el recognizer de arrastre HORIZONTAL del clip tiene la misma holgura que el del scroll del
    # timeline y, al ser el hijo, gana: tocar un clip y arrastrar lo mueve a él, no al timeline (el «pan» no, necesita el doble).
    def _mv_start(self, cid: str, axis: str) -> None:
        if self._drag is None:
            self._drag_start(cid)
        if self._drag is not None:
            self._drag.setdefault("axes", set()).add(axis)

    def _mv_end(self, axis: str) -> None:
        d = self._drag
        if d is None:
            return
        d.setdefault("axes", set()).discard(axis)
        if not d["axes"]:
            self._drag_end(None)

    def _drag_update(self, e) -> None:
        d = self._drag
        if not d:
            return
        s, z = self.s, self.s.zoom
        ev = evt_dx(e)
        d["dx"] += ev
        d["dy"] += evt_dy(e)
        if ev:
            self._push = 0.6 * self._push + 0.4 * ev          # hacia dónde y con qué fuerza empuja el dedo
        if not self._tick("drag"):
            return
        self._apply_drag(d)

    def _apply_drag(self, d) -> None:
        s, z = self.s, self.s.zoom
        frames, snap = s.drag_preview(d["ids"], round(d["dx"] / z))
        rowd = self._clamp_rows(d["ids"], round(d["dy"] / TRACK_H))
        lo, hi = 1e12, 0.0
        for i in d["base"]:
            g = self._refs[i].group
            g.left, g.top = d["base"][i][0] + frames * z, d["base"][i][1] + rowd * TRACK_H
            lo, hi = min(lo, g.left), max(hi, g.left + (g.width or 0))
        if hi + 600 > (self.stack.width or 0):               # el timeline crece para poder seguir hacia la derecha
            self.stack.width = hi + 600
            safe_update(self.stack)
        if self._auto is not None:
            self._auto["rate"] = self._edge_rate(lo, hi)
        self._show_guide(snap)
        safe_update(*[self._refs[i].group for i in d["base"]], self._guide)

    # ── desplazamiento automático al arrastrar cerca de los bordes (como DaVinci) ──
    def _edge_rate(self, left: float, right: float) -> float:
        sx, vp = self._view[0], (self._view[1] or self.viewport_w)
        if vp <= 0:
            return 0.0
        m = 70.0
        if right > sx + vp - m:
            base = min(45.0, 6.0 + (right - (sx + vp - m)) / 3.0)
        elif left < sx + m and sx > 0:
            base = -min(45.0, 6.0 + ((sx + m) - left) / 3.0)
        else:
            return 0.0
        if not self.INTENT_SCROLL:                            # escritorio: comportamiento de siempre
            return base
        # táctil: solo se desplaza si el dedo EMPUJA hacia ese borde; la velocidad sigue a la del dedo
        # (sin empuje no hay desplazamiento, así tocar el borde sin querer no mueve nada)
        if base * self._push <= 0 or abs(self._push) < 1.5:
            return 0.0
        return max(-30.0, min(30.0, self._push * 1.6))

    def _scroll_by(self, px: float) -> None:
        new = max(0.0, self._view[0] + px)
        try:
            res = self.scroller.scroll_to(offset=new, duration=0)
            if asyncio.iscoroutine(res):
                asyncio.get_event_loop().create_task(res)
        except Exception:
            return
        self._view = (new, self._view[1])

    def _auto_loop(self, auto) -> None:
        import time
        disp = getattr(self.s, "dispatch", None) or (lambda f: f())

        def tick():
            d, r = self._drag, auto["rate"]
            if not d or not auto["on"] or not r:
                return
            self._push *= 0.88                              # el empuje se enfría: dedo quieto = se detiene
            before = self._view[0]
            self._scroll_by(r)
            d["dx"] += self._view[0] - before
            self._apply_drag(d)
        while auto["on"]:
            time.sleep(0.04)
            if auto["rate"]:
                disp(tick)

    def _clamp_rows(self, ids, rowd: int) -> int:
        rows = self.rows()
        idx = {t.id: i for i, t in enumerate(rows)}
        if len({ed.find_clip(self.s.tl, i)[0].kind for i in ids}) > 1:   # audio + vídeo juntos: solo horizontal
            return 0
        lo = -min(idx[ed.find_clip(self.s.tl, i)[0].id] for i in ids)
        hi = len(rows) - 1 - max(idx[ed.find_clip(self.s.tl, i)[0].id] for i in ids)
        return max(lo, min(hi, rowd))

    def _show_guide(self, frame) -> None:
        if frame is None:
            self._guide.visible = False
        else:
            self._guide.left, self._guide.visible = frame * self.s.zoom, True

    def _drag_end(self, e) -> None:
        d, self._drag = self._drag, None
        self._guide.visible = False
        if self._auto is not None:
            self._auto["on"] = False
            self._auto = None
        if not d:
            return
        z = self.s.zoom
        before = self._refreshes
        rowd = self._clamp_rows(d["ids"], round(d["dy"] / TRACK_H))
        for i in d["ids"]:                              # fuerza reconstruir: sus posiciones fueron movidas a mano
            self._refs.pop(i, None)
        self.s.commit_drag(d["ids"], round(d["dx"] / z), rowd)
        if self._refreshes == before:                   # sin cambio de modelo (o error): volver a pintar
            self.refresh()

    # ── recorte por bordes con imán ────────────────────────────────
    def _handle(self, cid: str, edge: str, c, w: float, h: float, x: float, hw: float = HANDLE_W, op: float = 0.35):
        hs = {"dx": 0.0, "base": c.start if edge == "start" else c.end}

        def upd(e):
            s, z = self.s, self.s.zoom
            hs["dx"] += evt_dx(e)
            if not self._tick("trim"):
                return
            f = hs["base"] + round(hs["dx"] / z)
            pt = None
            if s.snap_on:
                f, pt = ed.snap_hit(f, ed.snap_points(s.tl, exclude=[cid], playhead=s.playhead), s.snap_threshold())
            ref = self._refs.get(cid)
            if ref is not None:
                g = ref.group
                if edge == "start":
                    right = c.end * z
                    g.left, g.width = f * z, max(5.0, right - f * z + 2)
                else:
                    g.width = max(5.0, f * z - c.start * z + 2)
            self._show_guide(pt)
            safe_update(ref.group if ref is not None else self.stack, self._guide)

        def end(e):
            before = self._refreshes
            self._guide.visible = False
            f = hs["base"] + round(hs["dx"] / self.s.zoom)
            self._refs.pop(cid, None)
            self.s.trim_clip(cid, edge, f)
            if self._refreshes == before:
                self.refresh()
        return _gd(
            content=ft.Container(bgcolor="#ffffff", opacity=op, width=hw, height=h),
            left=0 if edge == "start" else w + 2 - hw, top=1, width=hw, height=h, drag_interval=24,
            mouse_cursor=_cursor("RESIZE_LEFT_RIGHT"), on_horizontal_drag_update=upd, on_horizontal_drag_end=end)
