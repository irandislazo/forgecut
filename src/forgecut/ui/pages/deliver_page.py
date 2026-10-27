"""Página Deliver: exportar audio de los huecos de lipsync (o de la selección) y renderizar el vídeo."""
from __future__ import annotations

import asyncio
import os

import flet as ft

from .. import theme as T
from ..compat import Choice, btn, pick_dir, pick_save, safe_update, text, toast


# (clave, etiqueta, lado corto en px; None = tamaño original / borrador)
PRESETS = [("orig", "Original", None), ("2160", "4K · 2160p", 2160), ("1440", "2K · 1440p", 1440),
           ("1080", "Full HD · 1080p", 1080), ("720", "HD · 720p", 720), ("480", "SD · 480p", 480),
           ("360", "360p", 360), ("240", "240p", 240), ("draft", "Borrador rápido (½)", None)]
CRFS = [("18", "Alta"), ("20", "Normal"), ("26", "Compacta")]


class DeliverPage:
    def __init__(self, state, page: ft.Page):
        self.s, self.page = state, page
        self.fmt = Choice([("mp3", "MP3"), ("wav", "WAV")], "mp3")
        self.scope = Choice([("all", "Todos los huecos de lipsync"), ("sel", "Solo clips seleccionados")], "all")
        self.res = Choice([(k, lbl) for k, lbl, _ in PRESETS], "1080")
        self.crf = Choice(CRFS, "20")
        self.res_info = text("", 11, T.TEXT_DIM)
        self.out_dir = ft.TextField(label="Carpeta de salida (audio)", dense=True, width=300, text_size=12,
                                    value=os.path.join(state.data_dir, "export_audio"))
        self.info = text("", 12, T.TEXT_DIM)
        self.audio_msg = text("", 12, T.TEXT_DIM)
        self.bar = ft.ProgressBar(value=0, visible=False)
        self.render_msg = text("", 12, T.TEXT_DIM)
        card = lambda title, *ctrls: ft.Container(bgcolor=T.PANEL_2, border_radius=6, padding=14,
                                                  content=ft.Column([text(title, 14, bold=True), *ctrls], spacing=10))
        self.root = ft.Container(bgcolor=T.PANEL, padding=16, expand=True, content=ft.Column([
            card("🎙️ Exportar audio para generar los lipsync",
                 text("Cada hueco de lipsync se exporta como un archivo con el tramo exacto de la canción, "
                      "más un manifest.json con sus tiempos.", 12, T.TEXT_DIM),
                 self.scope.control, self.fmt.control,
                 ft.Row([self.out_dir, btn("Elegir…", lambda e: self.page.run_task(self.choose_dir), "FOLDER_OPEN")], spacing=8, wrap=True),
                 self.info, ft.Row([btn("Exportar audio", lambda e: self.page.run_task(self.export_audio), "AUDIO_FILE", primary=True),
                                    self.audio_msg], spacing=10, wrap=True)),
            card("🎞️ Renderizar vídeo", text("Resolución (lado corto; respeta la orientación del proyecto)", 11, T.TEXT_DIM),
                 self.res.control, text("Calidad", 11, T.TEXT_DIM), self.crf.control, self.res_info, self.bar,
                 ft.Row([btn("Renderizar MP4", lambda e: self.page.run_task(self.render), "MOVIE", primary=True), self.render_msg],
                        spacing=10, wrap=True)),
        ], spacing=14, scroll=ft.ScrollMode.AUTO))
        self.res._cb = lambda k: self.refresh_info()
        state.subscribe(lambda k: self.refresh_info() if k in ("timeline", "selection", "all") else None)
        self.refresh_info()

    def refresh_info(self) -> None:
        n_all = len(self.s.audio_ranges(False))
        self.info.value = f"{n_all} huecos de lipsync en el timeline · {len(self.s.selection)} clip(s) seleccionado(s)"
        safe_update(self.info)
        short = dict((k, v) for k, _l, v in PRESETS).get(self.res.value)
        tl = self.s.tl
        k = (short / max(1, min(tl.width, tl.height))) if short else (0.5 if self.res.value == "draft" else 1.0)
        ev = lambda x: max(2, int(x * k) // 2 * 2)
        self.res_info.value = f"Salida: {ev(tl.width)}×{ev(tl.height)} · {tl.fps} fps · {tl.timecode(tl.duration)}"
        safe_update(self.res_info)

    def apply_width(self, w: float) -> None:
        self.root.padding = 8 if w < 700 else 16
        safe_update(self.root)

    async def choose_dir(self) -> None:
        d = await pick_dir(self.page, "Carpeta de salida")
        if d:
            self.out_dir.value = d
            safe_update(self.out_dir)

    async def export_audio(self) -> None:
        only_sel = self.scope.value == "sel"
        self.audio_msg.value = "Exportando…"
        safe_update(self.audio_msg)
        try:
            outs = await asyncio.to_thread(self.s.export_audio, self.out_dir.value, self.fmt.value, only_sel)
            self.audio_msg.value = f"✔ {len(outs)} archivo(s) en {self.out_dir.value}"
        except Exception as ex:
            self.audio_msg.value = f"❌ {ex}"
        safe_update(self.audio_msg)

    async def render(self) -> None:
        if self.s.tl.duration <= 0:
            toast(self.page, "El timeline está vacío.")
            return
        default = os.path.join(self.s.data_dir, "render", f"{self.s.project.name}.mp4")
        out = await pick_save(self.page, "Guardar vídeo", f"{self.s.project.name}_{self.res.value}p.mp4" if self.res.value.isdigit() else f"{self.s.project.name}.mp4", "mp4") or default
        self.bar.visible, self.bar.value = True, 0
        self.render_msg.value = "Renderizando…"
        safe_update(self.bar, self.render_msg)

        def progress(p: float) -> None:
            self.bar.value = p
            safe_update(self.bar)
        try:
            short = dict((k, v) for k, _l, v in PRESETS).get(self.res.value)
            await asyncio.to_thread(self.s.render, out, self.res.value == "draft", progress, short, int(self.crf.value))
            self.render_msg.value = f"✔ {out}"
        except Exception as ex:
            self.render_msg.value = f"❌ {ex}"
        self.bar.visible = False
        safe_update(self.bar, self.render_msg)
