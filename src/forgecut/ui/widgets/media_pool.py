"""Media Pool estilo DaVinci Resolve: carpetas anidadas, selección múltiple (Ctrl / Shift / marcar), mover varios
elementos a una carpeta (arrastrando o con «Mover a…»), renombrar, borrar con aviso si están en el timeline,
búsqueda en todo el pool, orden, vista de lista o de miniaturas y arrastre al timeline (uno o varios, en secuencia).

Notas de maquetación (Flet 1.x): NUNCA se mezcla `expand=True` con `Row(wrap=True)` (Flutter lanza una excepción de
layout y todo el panel queda en blanco). Cada fila se construye aislada: si una falla se muestra el error en su lugar
en vez de vaciar el panel.
"""
from __future__ import annotations

import asyncio
import struct
import traceback

import flet as ft

from ...media.visuals import thumbs_count
from .. import theme as T
from ..compat import (ask_text, btn, close_dialog, draggable, file_image, hard_clip, icon_btn, open_dialog, pick_files,
                      quiet, safe_update, text, toast)

MEDIA_EXT = ["mp4", "mov", "mkv", "avi", "webm", "m4v", "mp3", "wav", "m4a", "aac", "flac", "ogg", "png", "jpg", "jpeg"]
ICONS = {"video": "MOVIE", "audio": "AUDIOTRACK", "image": "IMAGE"}
SEL_BG = "#24406b"
SORTS = [("name", "nombre"), ("duration", "duración"), ("kind", "tipo"), ("added", "orden de importación")]


def _png_size(path: str):
    try:
        with open(path, "rb") as f:
            h = f.read(24)
        w, hh = struct.unpack(">II", h[16:24])
        return (w, hh) if w and hh else None
    except Exception:
        return None


def _center():
    """Alineación centrada (el nombre del tipo cambió entre versiones de Flet); None si no existe."""
    try:
        return ft.Alignment(0, 0)
    except Exception:
        try:
            return ft.alignment.center
        except Exception:
            return None


def _ico(name: str):
    return getattr(ft.Icons, name, ft.Icons.CIRCLE)


class MediaPool:
    def __init__(self, state, page: ft.Page):
        self.s, self.page = state, page
        self.cur = None                                   # carpeta abierta (None = raíz)
        self.list = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO, expand=True)
        self.status = text("", 11, T.TEXT_DIM)
        self.crumb = text("", 11, T.TEXT_DIM, visible=False)      # texto plano (ruta), útil para accesibilidad/pruebas
        self.crumbs_row = ft.Row([], wrap=True, spacing=2, run_spacing=2)
        self.view_btn = icon_btn("GRID_VIEW", self._toggle_view, "Vista de miniaturas / lista", 18, T.TEXT_DIM)
        self.multi_btn = icon_btn("CHECKLIST", self._toggle_multi, "Modo selección: cada toque suma o quita", 18, T.TEXT_DIM)
        self.sort_btn = icon_btn("SORT", self._cycle_sort, "Ordenar", 18, T.TEXT_DIM)
        self.search = ft.TextField(hint_text="Buscar en todo el Media Pool…", dense=True, text_size=12, expand=True,
                                   on_change=self._on_search)
        self.selbar = ft.Container(visible=False, bgcolor=T.PANEL_3, padding=6, border_radius=4)
        header = ft.Row([text("Media Pool", 14, bold=True), ft.Container(expand=True), self.view_btn, self.multi_btn,
                         self.sort_btn], spacing=2)
        actions = ft.Row([btn("Carpeta", lambda e: self.page.run_task(self.new_folder), "CREATE_NEW_FOLDER"),
                          btn("Importar", self.on_import, "ADD")], wrap=True, spacing=6)       # sin expand: seguro
        self.root = ft.Container(bgcolor=T.PANEL, padding=8, expand=True, content=ft.Column([
            header, actions, ft.Row([self.search], spacing=0), self.crumbs_row, self.selbar, self.status, self.list],
            spacing=6))
        state.subscribe(lambda k: self.refresh() if k in ("assets", "all", "pool_sel", "visuals") else None)
        self.refresh()

    # ── importar / carpetas ────────────────────────────────────────
    async def on_import(self, e) -> None:
        paths = await pick_files(self.page, True, MEDIA_EXT, "Importar medios")
        if not paths:
            return
        self.status.value = f"Analizando {len(paths)} archivo(s)…"
        safe_update(self.status)
        assets = await asyncio.to_thread(self.s.import_media, paths, self.cur)    # entran en la carpeta abierta
        self.status.value = f"{len(assets)} importado(s)." + (f"  ⚠ {self.s.last_error}" if self.s.last_error else "")
        safe_update(self.status)

    async def new_folder(self) -> None:
        name = await ask_text(self.page, "Nueva carpeta", "Nombre de la carpeta")
        if name:
            self.s.create_bin(name, self.cur)

    async def rename_folder(self, bin_id: str) -> None:
        b = self.s.project.bins.get(bin_id)
        name = await ask_text(self.page, "Renombrar carpeta", "Nombre", b.name if b else "")
        if name:
            self.s.rename_bin(bin_id, name)

    async def rename_asset(self, asset_id: str) -> None:
        a = self.s.project.assets.get(asset_id)
        name = await ask_text(self.page, "Renombrar medio", "Nombre", a.name if a else "")
        if name:
            self.s.rename_asset(asset_id, name)

    def open_folder(self, bin_id) -> None:
        self.cur = bin_id
        self.s.pool_clear()
        self.refresh()

    def delete_folder(self, bin_id: str) -> None:
        if self.cur == bin_id:
            self.cur = self.s.project.bins[bin_id].parent
        self.s.delete_bin(bin_id)
        toast(self.page, "Carpeta eliminada (su contenido pasó a la carpeta superior).")

    # ── arrastrar y soltar ─────────────────────────────────────────
    def _drag_start(self, item_id: str) -> None:
        """No toca la selección ni repinta (cambiar controles en pleno arrastre lo cancelaría)."""
        s = self.s
        ids = list(s.pool_sel) if item_id in s.pool_sel else [item_id]
        s.drag_assets = [i for i in ids if i in s.project.assets]
        s.drag_bins = [i for i in ids if s.is_bin(i)]
        s.drag_asset = s.drag_assets[0] if s.drag_assets else None

    def _drop_on(self, bin_id) -> int:
        s = self.s
        ids = list(s.drag_assets) + list(s.drag_bins)
        if not ids and s.drag_asset:
            ids = [s.drag_asset]
        s.drag_asset, s.drag_assets, s.drag_bins = None, [], []
        n = s.move_items(ids, bin_id)
        if n:
            toast(self.page, "Movido." if n == 1 else f"{n} elementos movidos.")
        return n

    # ── «Mover a…» (también en móvil) ──────────────────────────────
    def move_dialog(self, ids) -> None:
        ids = [ids] if isinstance(ids, str) else list(ids)
        s = self.s
        moving_bins = [i for i in ids if s.is_bin(i)]
        dlg = ft.AlertDialog(modal=True, title=ft.Text(f"Mover {len(ids)} elemento(s) a…" if len(ids) > 1 else "Mover a…"))

        def choose(bin_id):
            close_dialog(self.page, dlg)
            n = s.move_items(ids, bin_id)
            toast(self.page, f"{n} elemento(s) movido(s)." if n else "Nada que mover.")

        def depth(b):
            return len(s.bin_path(b.id)) - 1
        opts = [ft.Container(content=text("📁 Raíz del Media Pool", 13), padding=8, bgcolor=T.PANEL_3, border_radius=4,
                             on_click=lambda e: choose(None))]
        for b in sorted(s.project.bins.values(), key=lambda b: [x.name.lower() for x in s.bin_path(b.id)]):
            if all(s.can_move_bin(m, b.id) for m in moving_bins):          # no se puede meter una carpeta en sí misma
                opts.append(ft.Container(content=text("      " * depth(b) + "📁 " + b.name, 13), padding=8,
                                         bgcolor=T.PANEL_3, border_radius=4, on_click=lambda e, i=b.id: choose(i)))
        dlg.content = ft.Column(opts, spacing=3, scroll=ft.ScrollMode.AUTO, height=280, width=320)
        dlg.actions = [ft.TextButton("Cancelar", on_click=lambda e: close_dialog(self.page, dlg))]
        open_dialog(self.page, dlg)

    async def _confirm(self, title: str, msg: str, ok: str = "Eliminar") -> bool:
        fut = asyncio.get_running_loop().create_future()
        dlg = ft.AlertDialog(modal=True, title=ft.Text(title), content=ft.Text(msg))

        def done(v):
            close_dialog(self.page, dlg)
            if not fut.done():
                fut.set_result(v)
        dlg.actions = [ft.TextButton("Cancelar", on_click=lambda e: done(False)), ft.TextButton(ok, on_click=lambda e: done(True))]
        open_dialog(self.page, dlg)
        return await fut

    async def delete_items(self, ids) -> None:
        s = self.s
        bins = [i for i in ids if s.is_bin(i)]
        assets = [i for i in ids if i in s.project.assets]
        if bins and await self._confirm(f"¿Eliminar {len(bins)} carpeta(s)?", "Su contenido pasará a la carpeta superior."):
            for b in bins:
                if b in s.project.bins:
                    self.delete_folder(b)
        if assets:
            done, blocked = s.delete_assets(assets)
            if blocked and await self._confirm(
                    f"{len(blocked)} medio(s) están en el timeline",
                    "Si continúas, también se quitarán sus clips del timeline (puedes deshacerlo con Ctrl+Z).", "Quitar todo"):
                s.delete_assets(blocked, remove_clips=True)
                done += blocked
            if done:
                toast(self.page, f"{len(done)} medio(s) quitado(s) del Media Pool.")

    # ── selección ──────────────────────────────────────────────────
    def _toggle_view(self, e=None) -> None:
        self.s.pool_view = "list" if self.s.pool_view == "grid" else "grid"
        self.refresh()

    def _toggle_multi(self, e=None) -> None:
        self.s.pool_multi = not self.s.pool_multi
        self.refresh()

    def _cycle_sort(self, e=None) -> None:
        keys = [k for k, _ in SORTS]
        self.s.pool_sort = keys[(keys.index(self.s.pool_sort) + 1) % len(keys)]
        self.refresh()

    def _on_search(self, e) -> None:
        self.s.pool_query = getattr(getattr(e, "control", None), "value", "") or ""
        self.refresh()

    def _click(self, item_id: str, order, open_if_folder: bool = False) -> None:
        s = self.s
        additive, rng = s.ctrl_held() or s.pool_multi, s.shift_held()
        if open_if_folder and s.is_bin(item_id) and not (additive or rng):
            self.open_folder(item_id)
            return
        s.pool_click(item_id, order, additive=additive, rng=rng)

    def _tick(self, item_id: str, order) -> None:
        """Casilla de la fila: suma/quita ese elemento sin necesitar Ctrl."""
        self.s.pool_click(item_id, order, additive=True, rng=False)

    # ── piezas visuales ────────────────────────────────────────────
    def _thumb(self, a, w: int, h: int) -> ft.Control:
        s = self.s
        body = None
        try:
            vis = s.asset_visual(a.id)
            if a.kind == "image" and a.path:
                body = file_image(a.path, w, h)
            elif vis and vis[1] == "film":
                size = _png_size(vis[0])
                if size:
                    sw = size[0] * h / size[1]                          # primera miniatura de la tira, a su proporción
                    thumbs_count(a.duration_s)
                    body = ft.Stack([ft.Container(content=file_image(vis[0], sw, h), left=0, top=0, width=sw, height=h)],
                                    width=w, height=h)
            elif vis and vis[1] == "wave":
                body = file_image(vis[0], w, h)
        except Exception:
            body = None
        if body is None:
            body = ft.Icon(_ico(ICONS.get(a.kind, "MOVIE")), size=min(w, h) * 0.55, color=T.TEXT_DIM)
        return ft.Container(content=body, width=w, height=h, bgcolor="#1f1f24", border_radius=3,
                            alignment=_center(), **hard_clip())

    def _check(self, item_id: str, order, selected: bool) -> ft.Control:
        return ft.Container(content=ft.Icon(_ico("CHECK_BOX" if selected else "CHECK_BOX_OUTLINE_BLANK"), size=18,
                                            color=T.ACCENT_2 if selected else T.TEXT_DIM),
                            on_click=lambda e, i=item_id: self._tick(i, order), padding=2, tooltip="Seleccionar")

    def _info(self, a) -> str:
        t = f"{a.duration_s:.1f}s" + (f" · {a.width}×{a.height}" if a.width else "")
        if (self.s.pool_query or "").strip() and a.bin_id:
            t += "  ·  📁 " + " / ".join(b.name for b in self.s.bin_path(a.bin_id))
        used = self.s.asset_usage(a.id)
        return t + (f"  ·  en timeline ×{used}" if used else "")

    def _wrap_drag(self, content, item_id: str):
        return draggable(content, item_id, on_start=lambda e, i=item_id: self._drag_start(i))

    def _folder_target(self, content, bin_id):
        return ft.DragTarget(group="media", content=content, on_accept=quiet(lambda e, i=bin_id: self._drop_on(i)))

    # ── filas: lista ───────────────────────────────────────────────
    def _folder_row(self, b, order):
        s = self.s
        sel = b.id in s.pool_sel
        n = len(s.assets_in(b.id)) + len(s.bins_in(b.id))
        row = ft.Container(bgcolor=SEL_BG if sel else T.PANEL_3, border_radius=4, padding=6,
                           on_click=lambda e, i=b.id: self._click(i, order, True),
                           content=ft.Row([self._check(b.id, order, sel),
                                           ft.Icon(ft.Icons.FOLDER, size=18, color="#e0a13a"),
                                           ft.Column([text(b.name, 12, bold=True, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                                                      text(f"{n} elemento(s)", 10, T.TEXT_DIM)], spacing=0, expand=True),
                                           icon_btn("EDIT", lambda e, i=b.id: self.page.run_task(self.rename_folder, i),
                                                    "Renombrar", 16, T.TEXT_DIM),
                                           icon_btn("DELETE", lambda e, i=b.id: self.delete_folder(i), "Eliminar carpeta", 16, T.TEXT_DIM)],
                                          spacing=2))
        return self._folder_target(self._wrap_drag(row, b.id), b.id)

    def _asset_row(self, a, order):
        s = self.s
        sel = a.id in s.pool_sel
        acts = [icon_btn("ADD_BOX", lambda e, i=a.id: s.add_asset_to_timeline(i), "Añadir al timeline en el playhead", 16),
                icon_btn("DRIVE_FILE_MOVE", lambda e, i=a.id: self.move_dialog(list(s.pool_sel) if i in s.pool_sel else [i]),
                         "Mover a una carpeta", 16)]
        if a.kind == "video":
            acts.append(icon_btn("CONTENT_CUT", lambda e, i=a.id: self.page.run_task(self.add_and_cut, i),
                                 "Añadir y cortar por escenas", 16))
        if sel:                                                     # acciones extra solo en la fila seleccionada (ahorra ancho)
            acts += [icon_btn("EDIT", lambda e, i=a.id: self.page.run_task(self.rename_asset, i), "Renombrar", 16, T.TEXT_DIM),
                     icon_btn("DELETE", lambda e, i=a.id: self.page.run_task(self.delete_items, [i]), "Quitar del Media Pool", 16, T.TEXT_DIM)]
        row = ft.Container(bgcolor=SEL_BG if sel else T.PANEL_2, border_radius=4, padding=6,
                           on_click=lambda e, i=a.id: self._click(i, order),
                           content=ft.Row([self._check(a.id, order, sel), self._thumb(a, 46, 28),
                                           ft.Column([text(a.name, 12, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                                                      text(self._info(a), 10, T.TEXT_DIM, no_wrap=True,
                                                           overflow=ft.TextOverflow.ELLIPSIS)], spacing=0, expand=True),
                                           *acts], spacing=2))
        return self._wrap_drag(row, a.id)

    # ── tarjetas: miniaturas ───────────────────────────────────────
    def _folder_card(self, b, order):
        s = self.s
        sel = b.id in s.pool_sel
        n = len(s.assets_in(b.id)) + len(s.bins_in(b.id))
        card = ft.Container(width=104, bgcolor=SEL_BG if sel else T.PANEL_3, border_radius=4, padding=4,
                            on_click=lambda e, i=b.id: self._click(i, order, True),
                            content=ft.Column([ft.Container(content=ft.Icon(ft.Icons.FOLDER, size=34, color="#e0a13a"),
                                                            width=96, height=54, alignment=_center()),
                                               text(b.name, 11, bold=True, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                                               text(f"{n} elemento(s)", 10, T.TEXT_DIM)], spacing=2))
        return self._folder_target(self._wrap_drag(card, b.id), b.id)

    def _asset_card(self, a, order):
        s = self.s
        sel = a.id in s.pool_sel
        card = ft.Container(width=104, bgcolor=SEL_BG if sel else T.PANEL_2, border_radius=4, padding=4,
                            on_click=lambda e, i=a.id: self._click(i, order),
                            content=ft.Column([self._thumb(a, 96, 54),
                                               text(a.name, 11, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                                               text(f"{a.duration_s:.1f}s", 10, T.TEXT_DIM)], spacing=2))
        return self._wrap_drag(card, a.id)

    # ── barra de selección ─────────────────────────────────────────
    def _selbar(self, order) -> None:
        s = self.s
        sel = [i for i in s.pool_sel if i in s.project.assets or i in s.project.bins]
        if not sel:
            self.selbar.visible = False
            return
        assets = [i for i in sel if i in s.project.assets]
        bar = [text(f"{len(sel)} seleccionado(s)", 12, bold=True),
               btn("Mover a…", lambda e: self.move_dialog(sel), "DRIVE_FILE_MOVE")]
        if assets:
            bar.append(btn("Al timeline", lambda e: self._to_timeline(assets), "ADD_BOX"))
        if len(sel) == 1:
            one = sel[0]
            bar.append(btn("Renombrar", lambda e: self.page.run_task(self.rename_folder if s.is_bin(one) else self.rename_asset, one), "EDIT"))
        bar += [btn("Eliminar", lambda e: self.page.run_task(self.delete_items, sel), "DELETE"),
                btn("Todo", lambda e: s.pool_select_all(order), "SELECT_ALL"),
                btn("Limpiar", lambda e: s.pool_clear(), "CLOSE")]
        self.selbar.content = ft.Row(bar, wrap=True, spacing=6, run_spacing=4)
        self.selbar.visible = True

    def _to_timeline(self, assets) -> None:
        clips = self.s.add_assets_to_timeline(assets)
        toast(self.page, f"{len(clips)} clip(s) añadido(s) en secuencia." if clips else (self.s.last_error or "No se pudo añadir"))

    # ── pintar ─────────────────────────────────────────────────────
    def _crumbs(self, path) -> None:
        chips = [self._folder_target(ft.Container(content=text("Media Pool", 11, T.TEXT if self.cur is None else T.TEXT_DIM, bold=self.cur is None),
                                                  padding=2,
                                                  on_click=lambda e: self.open_folder(None)), None)]
        for b in path:
            chips.append(text("›", 11, T.TEXT_DIM))
            chips.append(self._folder_target(ft.Container(content=text(b.name, 11, T.TEXT if b.id == self.cur else T.TEXT_DIM,
                                                                       bold=b.id == self.cur),
                                                          padding=2, on_click=lambda e, i=b.id: self.open_folder(i)), b.id))
        self.crumbs_row.controls = chips

    def _safe(self, fn, *a):
        try:
            return fn(*a)
        except Exception as ex:                                       # noqa: BLE001  una fila rota no vacía el panel
            traceback.print_exc()
            return text(f"⚠ {type(ex).__name__}: {ex}", 10, "#ff6b6b")

    def refresh(self) -> None:
        s = self.s
        try:
            if self.cur is not None and self.cur not in s.project.bins:
                self.cur = None
            path = s.bin_path(self.cur)
            self.crumb.value = "Media Pool" + "".join(f"  ›  {b.name}" for b in path)
            searching = bool((s.pool_query or "").strip())
            bins, assets = s.pool_items(self.cur)
            order = [b.id for b in bins] + [a.id for a in assets]
            s.pool_sel = [i for i in s.pool_sel if i in s.project.bins or i in s.project.assets]
            rows = []
            if self.cur is not None and not searching:               # fila «subir» (también acepta soltar elementos)
                parent = s.project.bins[self.cur].parent
                up = ft.Container(bgcolor=T.PANEL_3, border_radius=4, padding=6, on_click=lambda e, p=parent: self.open_folder(p),
                                  content=ft.Row([ft.Icon(ft.Icons.ARROW_UPWARD, size=16, color=T.TEXT_DIM),
                                                  text("Subir un nivel", 12)], spacing=6))
                rows.append(ft.DragTarget(group="media", content=up, on_accept=quiet(lambda e, p=parent: self._drop_on(p))))
            if s.pool_view == "grid":
                cards = [self._safe(self._folder_card, b, order) for b in bins] + [self._safe(self._asset_card, a, order) for a in assets]
                if cards:
                    rows.append(ft.Row(cards, wrap=True, spacing=6, run_spacing=6))
            else:
                rows += [self._safe(self._folder_row, b, order) for b in bins]
                rows += [self._safe(self._asset_row, a, order) for a in assets]
            if not rows:
                msg = "Sin resultados." if searching else ("Carpeta vacía." if self.cur else "Importa vídeos, audios o imágenes.")
                rows = [text(msg, 12, T.TEXT_DIM)]
            self.list.controls = rows
            self._crumbs(path)
            self._selbar(order)
            self.view_btn.icon = _ico("VIEW_LIST" if s.pool_view == "grid" else "GRID_VIEW")
            self.multi_btn.icon_color = T.ACCENT_2 if s.pool_multi else T.TEXT_DIM
            self.sort_btn.tooltip = "Orden: " + dict(SORTS).get(s.pool_sort, s.pool_sort) + " (clic para cambiar)"
            self.status.value = f"{len(bins)} carpeta(s) · {len(assets)} medio(s)" + (" · búsqueda en todo el pool" if searching else "")
        except Exception as ex:                                      # noqa: BLE001
            traceback.print_exc()
            self.list.controls = [text(f"⚠ No se pudo pintar el Media Pool: {type(ex).__name__}: {ex}", 11, "#ff6b6b")]
        safe_update(self.crumbs_row, self.selbar, self.status, self.view_btn, self.multi_btn, self.sort_btn, self.list)

    async def add_and_cut(self, asset_id: str) -> None:
        clip = self.s.add_asset_to_timeline(asset_id)
        if clip is None:
            toast(self.page, self.s.last_error or "No se pudo añadir")
            return
        toast(self.page, "Detectando escenas…")
        n = await self.s.auto_cut_selected()
        toast(self.page, f"Auto-corte: {n} cortes en el frame exacto del cambio de escena.")
