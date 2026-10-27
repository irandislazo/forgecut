"""Página Plantillas: crear/editar plantillas con clips del PC y generar el timeline automático."""
from __future__ import annotations

import asyncio
import os
from typing import Callable, Optional

import flet as ft

from ...automation.templates import STYLES, Template, pool_keys, pool_label
from .. import theme as T
from ..compat import Choice, btn, icon_btn, pick_files, safe_update, text, toast

VIDEO_EXT = ["mp4", "mov", "mkv", "avi", "webm", "m4v"]
AUDIO_EXT = ["mp3", "wav", "m4a", "aac", "flac", "ogg"]


class TemplatesPage:
    def __init__(self, state, page: ft.Page, on_generated: Callable[[], None]):
        self.s, self.page, self.on_generated = state, page, on_generated
        self.tpl: Optional[Template] = None
        self.expanded: set = set()
        self.song_path: Optional[str] = None
        self.new_name = ft.TextField(label="Nombre de la plantilla", dense=True, width=240, text_size=12)
        self.new_style = Choice([(k, v) for k, v in STYLES.items()], "la_voz")
        self.list_col = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO, expand=True)
        self.editor = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)
        self.msg = text("", 12, T.TEXT_DIM)
        self.left = ft.Container(width=290, bgcolor=T.PANEL, padding=10, content=ft.Column([
            text("Plantillas", 14, bold=True), self.new_name, self.new_style.control,
            btn("Crear plantilla", self.on_create, "ADD", primary=True), ft.Divider(height=8), self.list_col], spacing=8))
        self.right = ft.Container(content=self.editor, expand=True, bgcolor=T.PANEL, padding=12)
        self.root = ft.Container(expand=True)
        self.narrow = None
        self.apply_width(1200)
        self.refresh_list()
        self.refresh_editor()

    def apply_width(self, w: float) -> None:
        """Ventana ancha: lista a la izquierda y editor a la derecha. Estrecha (móvil): apilados."""
        narrow = w < 760
        if narrow == self.narrow:
            return
        self.narrow = narrow
        if narrow:
            self.left.width, self.left.height, self.left.expand = None, 230, False
            self.new_name.width = 260
            self.right.padding = 8
            self.root.content = ft.Column([self.left, self.right], spacing=2, expand=True)
        else:
            self.left.width, self.left.height, self.left.expand = 290, None, False
            self.new_name.width = 240
            self.right.padding = 12
            self.root.content = ft.Row([self.left, self.right], spacing=2, expand=True)
        safe_update(self.root)

    # ── lista ──────────────────────────────────────────────────────
    def refresh_list(self) -> None:
        names = self.s.templates.names()
        self.list_col.controls = [ft.Container(
            content=ft.Row([text(n, 12, bold=bool(self.tpl and self.tpl.name == n), expand=True),
                            icon_btn("DELETE", lambda e, x=n: self.on_delete(x), "Eliminar plantilla", 16, T.TEXT_DIM)]),
            bgcolor=T.PANEL_3 if self.tpl and self.tpl.name == n else T.PANEL_2, padding=6, border_radius=4,
            on_click=lambda e, x=n: self.open(x)) for n in names] or [text("Aún no hay plantillas.", 12, T.TEXT_DIM)]
        safe_update(self.list_col)

    def on_create(self, e) -> None:
        name = (self.new_name.value or "").strip()
        if not name:
            toast(self.page, "Escribe un nombre.")
            return
        self.tpl = Template(name=name, style=self.new_style.value)
        self.s.templates.save(self.tpl)
        self.new_name.value = ""
        safe_update(self.new_name)
        self.refresh_list()
        self.refresh_editor()

    def open(self, name: str) -> None:
        self.tpl = self.s.templates.load(name)
        self.expanded.clear()
        self.refresh_list()
        self.refresh_editor()

    def on_delete(self, name: str) -> None:
        self.s.templates.delete(name)
        if self.tpl and self.tpl.name == name:
            self.tpl = None
        self.refresh_list()
        self.refresh_editor()

    def save(self) -> None:
        if self.tpl:
            self.s.templates.save(self.tpl)

    # ── editor ─────────────────────────────────────────────────────
    def refresh_editor(self) -> None:
        t = self.tpl
        if not t:
            self.editor.controls = [text("Crea o selecciona una plantilla.", 13, T.TEXT_DIM)]
            safe_update(self.editor)
            return

        def setnum(attr, lo, hi):
            def f(e):
                try:
                    setattr(t, attr, min(hi, max(lo, float(e.control.value.replace(",", ".")))))
                    self.save()
                except ValueError:
                    pass
            return f
        params = ft.Row([
            ft.TextField(label="Duración lejanos (s)", value=str(t.far_s), width=150, dense=True, text_size=12,
                         on_submit=setnum("far_s", 1.5, 5.0), on_blur=setnum("far_s", 1.5, 5.0)),
            ft.TextField(label="Intro (s)", value=str(t.intro_s), width=110, dense=True, text_size=12,
                         on_submit=setnum("intro_s", 0, 90), on_blur=setnum("intro_s", 0, 90)) if t.style == "la_voz" else ft.Container(),
            ft.TextField(label="Hueco lipsync (s)", value=str(t.lipsync_s), width=140, dense=True, text_size=12,
                         on_submit=setnum("lipsync_s", 3, 30), on_blur=setnum("lipsync_s", 3, 30))], spacing=8, run_spacing=8, wrap=True)
        rows = [self._pool_row(k) for k in pool_keys(t.style)]
        self.editor.controls = [text(f"{t.name}  ·  {STYLES.get(t.style, t.style)}", 16, bold=True), params,
                                text("Clips por grupo (de tu PC; solo se guardan las rutas)", 11, T.TEXT_DIM), *rows,
                                ft.Divider(height=16), *self._generator()]
        safe_update(self.editor)

    def _pool_row(self, key: str):
        t = self.tpl
        items = t.pool(key)
        head = ft.Row([
            text(pool_label(key), 12, bold=True), text(f"{len(items)} clips", 11, T.TEXT_DIM),
            btn("Añadir clips", lambda e, k=key: self.page.run_task(self.add_files, k), "ADD"),
            icon_btn("EXPAND_MORE" if key not in self.expanded else "EXPAND_LESS", lambda e, k=key: self.toggle(k), "Ver lista", 18),
            icon_btn("DELETE_SWEEP", lambda e, k=key: self.clear(k), "Vaciar grupo", 16, T.TEXT_DIM)],
                       spacing=6, run_spacing=4, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        body = [head]
        if key in self.expanded:
            body += [ft.Row([text("   " + os.path.basename(i.path), 11, T.TEXT_DIM, expand=True),
                             text(f"{i.duration_s:.1f}s", 11, T.TEXT_DIM),
                             icon_btn("CLOSE", lambda e, k=key, p=i.path: self.remove(k, p), "Quitar", 14, T.TEXT_DIM)])
                     for i in items]
        return ft.Container(content=ft.Column(body, spacing=0), bgcolor=T.PANEL_2, border_radius=4, padding=6)

    def toggle(self, key: str) -> None:
        self.expanded.symmetric_difference_update({key})
        self.refresh_editor()

    def clear(self, key: str) -> None:
        self.tpl.pools[key] = []
        self.save()
        self.refresh_editor()

    def remove(self, key: str, path: str) -> None:
        self.tpl.remove(key, path)
        self.save()
        self.refresh_editor()

    async def add_files(self, key: str) -> None:
        paths = await pick_files(self.page, True, VIDEO_EXT, f"Clips para: {pool_label(key)}")
        if not paths:
            return
        toast(self.page, "Analizando clips…")
        items = await self.s.probe_pool_items(paths)
        n = self.tpl.add_files(key, items)
        self.save()
        toast(self.page, f"{n} clip(s) añadidos a «{pool_label(key)}».")
        self.refresh_editor()

    # ── generar timeline ───────────────────────────────────────────
    def _generator(self):
        t = self.tpl
        miss = t.missing()
        self.seed = ft.TextField(label="Semilla (opcional)", width=150, dense=True, text_size=12,
                                 hint_text="vacío = aleatorio")
        self.song_label = text(os.path.basename(self.song_path) if self.song_path else "Ninguna canción elegida", 12, T.TEXT_DIM)
        return [text("Generar timeline", 14, bold=True),
                text("\n".join("• " + m for m in miss) if miss else "Plantilla lista ✔", 12, T.ACCENT if miss else "#4ade80"),
                ft.Row([btn("Elegir canción", lambda e: self.page.run_task(self.pick_song), "MUSIC_NOTE"), self.song_label, self.seed], spacing=10, run_spacing=8, wrap=True),
                ft.Row([btn("Generar timeline", lambda e: self.page.run_task(self.generate), "AUTO_AWESOME", primary=True), self.msg], spacing=10, wrap=True)]

    async def pick_song(self) -> None:
        paths = await pick_files(self.page, False, AUDIO_EXT, "Canción")
        if paths:
            self.song_path = paths[0]
            self.song_label.value = os.path.basename(self.song_path)
            safe_update(self.song_label)

    async def generate(self) -> None:
        t = self.tpl
        if not t:
            return
        if t.missing():
            toast(self.page, "La plantilla está incompleta.")
            return
        if not self.song_path:
            toast(self.page, "Elige primero la canción.")
            return
        seed = None
        try:
            seed = int(self.seed.value) if (self.seed.value or "").strip() else None
        except ValueError:
            pass
        self.msg.value = "Generando…"
        safe_update(self.msg)
        try:
            plan = await asyncio.to_thread(self.s.create_timeline, t, self.song_path, seed)
        except Exception as ex:
            self.msg.value = f"❌ {ex}"
            safe_update(self.msg)
            return
        warn = ("  ⚠ " + " ".join(plan.warnings)) if plan.warnings else ""
        self.msg.value = f"✔ {len(plan.clips)} clips · {plan.info.get('lipsyncs', 0)} huecos de lipsync{warn}"
        safe_update(self.msg)
        self.on_generated()
