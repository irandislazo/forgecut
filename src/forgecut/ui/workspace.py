"""Espacio de trabajo de edición (Media · Cut · Edit): un solo árbol de paneles cuya
visibilidad cambia según la página, como las páginas de DaVinci Resolve."""
from __future__ import annotations

import asyncio
import math
import os

import flet as ft

from . import theme as T
from .compat import (Choice, android_public_dirs, btn, default_out_dir, evt_dx, evt_dy, icon_btn, quiet, safe_update, text,
                     toast, writable_dir)
from .widgets.inspector import Inspector
from .widgets.media_pool import MediaPool
from .widgets.preview import Preview
from .widgets.timeline_view import TimelineView


class Toolbar:
    def __init__(self, state, page: ft.Page):
        self.s, self.page = state, page
        self.thr = ft.TextField(value="0.30", width=60, dense=True, text_size=12, tooltip="Sensibilidad de escenas (0.1–0.9)")
        self.viewport = lambda: 800.0                  # ancho visible del timeline (lo fija el Workspace)
        self.zoom = ft.Slider(min=0.0, max=1.0, value=0.5, width=140, on_change=self._slide)
        self.tool_btns = {}
        self.fmt = Choice([("mp3", "MP3"), ("wav", "WAV")], "mp3")
        self.row = ft.Row(spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER, wrap=False)
        self.root = ft.Container(content=ft.Row([self.row], scroll=ft.ScrollMode.AUTO), bgcolor=T.PANEL_2, padding=4)
        state.subscribe(lambda k: self.refresh() if k in ("tool", "all", "timeline") else (self.sync_zoom() if k == "zoom" else None))
        self.refresh()

    # ── zoom: el rango del deslizable llega hasta «ver todo el timeline», sea cual sea su largo ──
    ZMAX = 6.0

    def zoom_range(self):
        tl = self.s.tl
        fit = float(self.viewport() or 800.0) / max(float(tl.duration) * 1.05, tl.fps * 10.0)
        zmin = max(0.0005, min(0.05, fit))
        return zmin, max(zmin * 2, self.ZMAX)

    def _slide(self, e) -> None:
        zmin, zmax = self.zoom_range()
        self.s.set_zoom(zmin * (zmax / zmin) ** float(e.control.value))

    def sync_zoom(self) -> None:
        zmin, zmax = self.zoom_range()
        v = math.log(max(self.s.zoom, zmin) / zmin) / math.log(zmax / zmin)
        v = max(0.0, min(1.0, v))
        if abs(v - float(self.zoom.value or 0)) > 0.003:
            self.zoom.value = v
            safe_update(self.zoom)

    def _zoom_step(self, factor: float) -> None:
        zmin, zmax = self.zoom_range()
        self.s.set_zoom(max(zmin, min(zmax, self.s.zoom * factor)))

    def refresh(self) -> None:
        s = self.s
        c = lambda ic, fn, tip, on=False: icon_btn(ic, fn, tip, 18, T.ACCENT if on else T.TEXT)
        self.row.controls = [
            c("NEAR_ME", lambda e: s.set_tool("select"), "Selección (V)", s.tool == "select"),
            c("CONTENT_CUT", lambda e: s.set_tool("razor"), "Cuchilla (B): clic en un clip para cortar", s.tool == "razor"),
            c("AUTO_FIX_HIGH", lambda e: s.set_edit_mode("auto"), "Auto: mueve o recorta según dónde toques el clip", s.edit_mode == "auto"),
            c("OPEN_WITH", lambda e: s.set_edit_mode("move"), "Mover clips (táctil)", s.edit_mode == "move"),
            c("SWAP_HORIZ", lambda e: s.set_edit_mode("trim"), "Recortar bordes (táctil)", s.edit_mode == "trim"),
            c("CALL_SPLIT", lambda e: s.split_at_playhead(), "Cortar en el playhead (Ctrl+B)"),
            c("DELETE", lambda e: s.delete_selected(False), "Borrar (Supr)"),
            c("DELETE_SWEEP", lambda e: s.delete_selected(True), "Borrar con ripple (Mayús+Supr)"),
            c("CONTENT_COPY", lambda e: s.duplicate_selected(), "Duplicar"),
            c("CHECKLIST", self._toggle_multi, "Multiselección fija (equivale a mantener Ctrl; Ctrl+clic también funciona)", s.multi_mode),
            c("SELECT_ALL", lambda e: s.select_all(), "Seleccionar todo (Ctrl+A)"),
            c("BOOKMARK_ADD", lambda e: s.add_marker(), "Marcador (M)"),
            ft.Container(width=10),
            c("UNDO", lambda e: s.undo(), "Deshacer (Ctrl+Z)"), c("REDO", lambda e: s.redo(), "Rehacer (Ctrl+Y)"),
            ft.Container(width=10),
            c("MAGNET" if hasattr(ft.Icons, "MAGNET") else "GRID_ON", self._toggle_snap, "Imán (snap)", s.snap_on),
            icon_btn("ZOOM_OUT", lambda e: self._zoom_step(1 / 1.5), "Alejar", 16, T.TEXT_DIM), self.zoom,
            icon_btn("ZOOM_IN", lambda e: self._zoom_step(1.5), "Acercar", 16, T.TEXT_DIM),
            ft.Container(width=10),
            btn("Auto-cortar escenas", self._auto_cut, "MOVIE_FILTER", tooltip="Corta el clip seleccionado en cada cambio de escena (frame exacto)"),
            self.thr,
            ft.Container(width=10),
            btn("Exportar audio selección", self._export_audio, "AUDIOTRACK",
                tooltip="Guarda como archivo de audio el tramo de cada clip seleccionado (Ctrl+clic o marquesina para varios)"),
            self.fmt.control]
        safe_update(self.row)
        self.sync_zoom()

    def _toggle_multi(self, e) -> None:
        self.s.multi_mode = not self.s.multi_mode
        self.refresh()

    def _toggle_snap(self, e) -> None:
        self.s.snap_on = not self.s.snap_on
        self.refresh()

    async def _export_audio(self, e) -> None:
        if not self.s.selection:
            toast(self.page, "Selecciona uno o varios clips (Ctrl+clic o arrastra una marquesina).")
            return
        out_dir, note = writable_dir(default_out_dir("audio", self.s.data_dir), os.path.join(self.s.data_dir, "export_audio"),
                                     android_public_dirs("audio"))          # Music/ForgeCut por defecto
        toast(self.page, "Exportando audio…")
        try:
            outs = await asyncio.to_thread(self.s.export_selected_audio, out_dir, self.fmt.value)
            toast(self.page, f"{len(outs)} audio(s) exportado(s) en {out_dir}")
        except Exception as ex:                                   # noqa: BLE001
            toast(self.page, f"No se pudo exportar: {ex}")

    async def _auto_cut(self, e) -> None:
        if not [c for c in self.s.selected_clips() if c.kind == "video"]:
            toast(self.page, "Selecciona primero un clip de vídeo.")
            return
        try:
            thr = min(0.9, max(0.05, float(self.thr.value.replace(",", "."))))
        except ValueError:
            thr = 0.3
        toast(self.page, "Detectando escenas…")
        try:
            n = await self.s.auto_cut_selected(thr)
            toast(self.page, f"{n} cortes aplicados en los cambios de escena.")
        except Exception as ex:
            toast(self.page, f"Error: {ex}")


class Workspace:
    LAYOUT = {  # panel → visible por página
        "media": dict(pool=True, preview=True, inspector=False, timeline=False),
        "cut": dict(pool=False, preview=True, inspector=False, timeline=True),
        "edit": dict(pool=True, preview=True, inspector=True, timeline=True),
    }

    def __init__(self, state, page: ft.Page):
        self.pool = MediaPool(state, page)
        self.preview = Preview(state, page)
        self.inspector = Inspector(state)
        self.timeline = TimelineView(state)
        self.toolbar = Toolbar(state, page)
        self.pool_box = ft.Container(content=self.pool.root, width=300)
        self.inspector_box = ft.Container(content=self.inspector.root, width=320)
        self.preview_box = ft.Container(content=self.preview.root, expand=True)
        # Divisores arrastrables (escritorio): ancho de Medios/Inspector y alto del timeline
        self.pool_w, self.insp_w, self.top_w = None, 320.0, 60
        self.split_pool = self._splitter(True, lambda dx: self._resize_w("pool", dx))
        self.split_insp = self._splitter(True, lambda dx: self._resize_w("insp", -dx))
        self.split_tl = self._splitter(False, self._resize_h)
        self.top = ft.Row([self.pool_box, self.split_pool, self.preview_box, self.split_insp, self.inspector_box], spacing=0, expand=self.top_w)
        self.bottom = ft.Column([self.toolbar.root, ft.Container(content=self.timeline.root, expand=True)], spacing=0, expand=100 - self.top_w)
        # Modo compacto (móvil / ventana estrecha): una sola zona a la vez, elegida con este selector
        self.page = page
        self.compact, self.panel, self.page_name = False, "preview", "edit"
        self.switch = Choice([("pool", "Medios"), ("preview", "Visor"), ("inspector", "Inspector")], "preview",
                             on_change=self._pick_panel)
        self.switch_row = ft.Container(content=self.switch.control, visible=False, padding=4, bgcolor=T.PANEL_2)
        self.root = ft.Column([self.switch_row, self.top, self.split_tl, self.bottom], spacing=0, expand=True)

    def _splitter(self, vertical: bool, fn) -> ft.Control:
        """Borde arrastrable entre paneles (cursor de redimensionar)."""
        mc = getattr(ft, "MouseCursor", None)
        cur = getattr(mc, "RESIZE_LEFT_RIGHT" if vertical else "RESIZE_UP_DOWN", None)
        cur = None if isinstance(cur, str) else cur
        box = ft.Container(bgcolor=T.PANEL_2, width=6 if vertical else None, height=None if vertical else 6)
        kw = dict(on_horizontal_drag_update=lambda e: fn(evt_dx(e))) if vertical else dict(on_vertical_drag_update=lambda e: fn(evt_dy(e)))
        for k, v in kw.items():
            kw[k] = quiet(v)
        return ft.GestureDetector(content=box, mouse_cursor=cur, drag_interval=30, **kw)

    def _resize_w(self, which: str, dx: float) -> None:
        if which == "pool":
            cur = self.pool_w or (520 if self.page_name == "media" else 300)
            self.pool_w = max(180.0, min(900.0, cur + dx))
        else:
            self.insp_w = max(200.0, min(700.0, self.insp_w + dx))
        self._layout()

    def _resize_h(self, dy: float) -> None:
        h = float(getattr(self.page, "height", 0) or 900)
        self.top_w = int(max(15, min(85, self.top_w + dy / h * 100)))
        self.top.expand, self.bottom.expand = self.top_w, 100 - self.top_w
        self._layout()

    def _pick_panel(self, key: str) -> None:
        self.panel = key
        self._layout()

    def _set_switch(self, key: str) -> None:
        self.panel, self.switch.value = key, key
        self.switch._build()
        safe_update(self.switch.control)

    def set_page(self, name: str) -> None:
        self.page_name = name
        if self.compact:
            self._set_switch("pool" if name == "media" else ("preview" if self.panel == "pool" else self.panel))
        self._layout()

    def apply_width(self, w: float) -> None:
        """Llamado al cambiar el tamaño de la ventana: alterna entre el diseño de escritorio y el compacto."""
        compact = w < 900
        if compact != self.compact:
            self.compact = compact
            self._set_switch("pool" if (compact and self.page_name == "media") else "preview")
        self.preview.resize(min(560, int(w) - 24) if compact else 560)
        self.timeline.viewport_w = max(0.0, float(w) - 100.0)
        self.toolbar.viewport = lambda: self.timeline.viewport_w
        self.toolbar.sync_zoom()
        self._layout()

    def _layout(self) -> None:
        cfg = self.LAYOUT[self.page_name]
        if self.compact:
            self.preview_box.visible = self.panel == "preview"
            self.pool_box.visible, self.inspector_box.visible = self.panel == "pool", self.panel == "inspector"
            for b in (self.pool_box, self.inspector_box):
                b.width, b.expand = None, True
            self.switch_row.visible = True
        else:
            self.preview_box.visible = True
            self.pool_box.visible, self.inspector_box.visible = cfg["pool"], cfg["inspector"]
            self.pool_box.width, self.pool_box.expand = (self.pool_w or (520 if self.page_name == "media" else 300)), False
            self.inspector_box.width, self.inspector_box.expand = self.insp_w, False
            self.switch_row.visible = False
        self.split_pool.visible = (not self.compact) and cfg["pool"]
        self.split_insp.visible = (not self.compact) and cfg["inspector"]
        self.bottom.visible = cfg["timeline"]
        self.split_tl.visible = cfg["timeline"] and not self.compact
        safe_update(self.root)
