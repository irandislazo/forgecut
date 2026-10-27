"""Visor: muestra el frame del timeline bajo el playhead y reproduce.

Parado / arrastrando el playhead: un frame suelto por ffmpeg (con caché).
Reproduciendo: UN ffmpeg persistente por clip (media/stream.py) entrega JPEG por pipe a ritmo del
timeline y se pinta sobre el MISMO control de imagen (sin crear controles por frame). Si el decodificador
no llega, se saltan frames para no desfasarse del reloj. Todavía SIN audio.
"""
from __future__ import annotations

import asyncio
import base64
import dataclasses
import math
import os
import tempfile
import time

import flet as ft

from ...core import editing as ed
from ...media.audio_play import AudioPlayer, build_audio_mix
from .. import theme as T
from ..compat import hard_clip, icon_btn, make_image, run_bg, safe_update, set_image_bytes, text, ui_call

PREVIEW_W = 560


class Preview:
    def __init__(self, state, page: ft.Page, width: int = PREVIEW_W):
        self.s, self.page = state, page
        self.w = width
        self.h = max(60, int(width * state.tl.height / max(1, state.tl.width)))
        self.holder = ft.Container(width=self.w, height=self.h, bgcolor="#000000")
        self.tc = text("00:00:00:00", 16, T.TEXT, bold=True)
        self.play_btn = icon_btn("PLAY_ARROW", self.toggle_play, "Reproducir (Espacio)", 26)
        bar = ft.Row([self.tc, ft.Container(expand=True),
                      icon_btn("SKIP_PREVIOUS", lambda e: state.set_playhead(0), "Inicio", 20),
                      icon_btn("CHEVRON_LEFT", lambda e: state.set_playhead(state.playhead - 1), "−1 frame", 20),
                      self.play_btn,
                      icon_btn("CHEVRON_RIGHT", lambda e: state.set_playhead(state.playhead + 1), "+1 frame", 20),
                      icon_btn("SKIP_NEXT", lambda e: state.set_playhead(state.tl.duration), "Final", 20),
                      ], spacing=0)
        # botones pequeños (equivalen a las flechas del teclado, pensados para móvil): ±1 y ±10 frames, punto de edición
        steps = ft.Row([icon_btn("SKIP_PREVIOUS", lambda e: state.goto_edit(-1), "Punto de edición anterior (↑)", 16, T.TEXT_DIM),
                        icon_btn("FAST_REWIND", lambda e: state.step_frames(-10), "−10 frames (Mayús+←)", 16, T.TEXT_DIM),
                        icon_btn("ARROW_LEFT", lambda e: state.step_frames(-1), "−1 frame (←)", 16),
                        icon_btn("ARROW_RIGHT", lambda e: state.step_frames(1), "+1 frame (→)", 16),
                        icon_btn("FAST_FORWARD", lambda e: state.step_frames(10), "+10 frames (Mayús+→)", 16, T.TEXT_DIM),
                        icon_btn("SKIP_NEXT", lambda e: state.goto_edit(1), "Siguiente punto de edición (↓)", 16, T.TEXT_DIM)],
                       spacing=0, alignment=ft.MainAxisAlignment.CENTER)
        self.root = ft.Container(bgcolor=T.PANEL, padding=8, expand=True,
                                 content=ft.Column([ft.Row([self.holder], alignment=ft.MainAxisAlignment.CENTER), bar, steps],
                                                   spacing=2, scroll=ft.ScrollMode.AUTO))
        self.playing = False
        self._token = 0
        self._cache: dict = {}
        self._stream = None                     # {"clip": id, "src0": frame, "st": FrameStream}
        self._lock, self._busy, self._pending = __import__("threading").Lock(), False, None
        self.audio = AudioPlayer()
        self._adir = None
        self._aend = None                       # frame donde termina el tramo de audio en curso
        self._anext = None                      # siguiente tramo ya mezclado: (ruta|None, frame_fin)
        self._acount = 0
        self._img = None                        # imagen persistente de la reproducción
        self._xsig = None                       # firma de la transformación del envoltorio actual
        self._clip_now = None
        state.subscribe(self._on_state)
        self.show(state.playhead)

    def resize(self, width: int) -> None:
        """Adapta el visor al ancho disponible (móvil/ventana estrecha)."""
        width = max(200, int(width))
        if width == self.w:
            return
        self.w = width
        self.h = max(60, int(width * self.s.tl.height / max(1, self.s.tl.width)))
        self.holder.width, self.holder.height = self.w, self.h
        self._cache.clear()
        safe_update(self.holder)
        self.show(self.s.playhead)

    def _on_state(self, kind: str) -> None:
        if kind == "live" and not self.playing:
            self.show(self.s.playhead)
            return
        if self.playing and kind == "playhead":      # durante la reproducción pinta _loop (evita doble trabajo)
            return
        if kind in ("playhead", "timeline", "all"):
            self.h = max(60, int(self.w * self.s.tl.height / max(1, self.s.tl.width)))
            self.holder.height = self.h
            self.show(self.s.playhead)

    # ── dibujo ─────────────────────────────────────────────────────
    def _xf(self, clip):
        tr = clip.transform
        ov = self.s.live_props.get(clip.id)
        return dataclasses.replace(tr, **ov) if ov else tr

    def _wrap(self, ctl, clip):
        """Aplica la transformación del clip (posición, zoom, rotación, opacidad, volteo) a la imagen del visor."""
        if ctl is None or clip is None or clip.kind == "placeholder":
            return ctl
        tr = self._xf(clip)
        if (abs(tr.scale - 1) < 1e-6 and not tr.x and not tr.y and not tr.rotation and tr.opacity >= 0.999
                and not tr.flip_h and not tr.flip_v):
            return ctl
        k = self.w / max(1, self.s.tl.width)
        try:
            scale = ft.Scale(scale_x=-tr.scale if tr.flip_h else tr.scale, scale_y=-tr.scale if tr.flip_v else tr.scale)
        except Exception:
            scale = tr.scale
        inner = ft.Container(content=ctl, left=tr.x * k, top=tr.y * k, width=self.w, height=self.h, scale=scale,
                             rotate=math.radians(tr.rotation), opacity=tr.opacity)
        return ft.Container(content=ft.Stack([inner], width=self.w, height=self.h), width=self.w, height=self.h, **hard_clip())

    def show(self, frame: int) -> None:
        self._xsig, self._clip_now = None, None
        tl = self.s.tl
        self.tc.value = f"{tl.timecode(frame)}   /   {tl.timecode(tl.duration)}"
        clip = ed.top_video_clip_at(tl, frame)
        self._token += 1
        token = self._token
        if clip is None:
            self.holder.content = None
        elif clip.kind == "placeholder" or not clip.asset_id:
            self.holder.content = ft.Container(bgcolor="#2a2a2a", content=ft.Column(
                [text(clip.name or "HUECO", 22, "#ffffff", bold=True), text("Hueco de lipsync", 12, T.TEXT_DIM)],
                alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER))
        else:
            self._clip_now = clip
            asset = self.s.project.assets.get(clip.asset_id)
            if asset is not None:
                src_f = clip.source_in + int(round((frame - clip.start) * clip.speed))
                key = (asset.path, src_f)
                if key in self._cache:
                    self.holder.content = self._wrap(self._cache[key], clip)
                else:
                    self._request(asset.path, src_f / tl.fps, key, token)
        safe_update(self.holder, self.tc)

    def _request(self, path, t_s, key, token) -> None:
        """Una sola extracción ffmpeg a la vez: al arrastrar el playhead solo se pide el ÚLTIMO frame."""
        with self._lock:
            self._pending = (path, t_s, key, token)
            if self._busy:
                return
            self._busy = True

        def work():
            while True:
                with self._lock:
                    req, self._pending = self._pending, None
                    if req is None:
                        self._busy = False
                        return
                try:
                    ctl = self._fetch(req[0], req[1], req[2])
                    ui_call(self.page, lambda c=ctl, t=req[3]: self._apply(t, c))
                except Exception:                                  # noqa: BLE001
                    pass
        import threading
        threading.Thread(target=work, daemon=True).start()

    def _fetch(self, path: str, t_s: float, key):
        data = self.s.backend.frame_jpeg(path, t_s, self.w)
        if not data:
            return None
        ctl = make_image(base64.b64encode(data).decode("ascii"), self.w, self.h)
        if len(self._cache) > 150:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = ctl
        return ctl

    def _apply(self, token: int, ctl) -> None:
        if ctl is not None and token == self._token:           # descarta respuestas viejas
            self.holder.content = self._wrap(ctl, self._clip_now)
            safe_update(self.holder)

    # ── reproducción ───────────────────────────────────────────────
    def toggle_play(self, e=None) -> None:
        self.playing = not self.playing
        self.play_btn.icon = getattr(ft.Icons, "PAUSE" if self.playing else "PLAY_ARROW")
        safe_update(self.play_btn)
        if self.playing:
            if self.s.playhead >= self.s.tl.duration:
                self.s.set_playhead(0)
            self.page.run_task(self._loop)

    # ── audio (mezcla por tramos: 30 s al empezar, luego 10 min en segundo plano) ──
    def _render_chunk(self, sf: int, secs: int):
        tl = self.s.tl
        ef = min(tl.duration, sf + secs * tl.fps)
        if ef <= sf or not self.s.backend.available():
            return None, ef
        if self._adir is None:
            self._adir = tempfile.mkdtemp(prefix="forgecut_audio_")
        self._acount += 1
        path = os.path.join(self._adir, f"mix{self._acount}.wav")
        ok = build_audio_mix(self.s.backend.ffmpeg, self.s.project, sf, ef, path)
        return (path if ok else None), ef

    def _kick_next(self) -> None:
        if self._aend is not None and self._aend < self.s.tl.duration:
            run_bg(self._render_chunk, self._aend, 600, on_done=lambda r: setattr(self, "_anext", r),
                   on_error=lambda ex: print("[audio]", ex))

    async def _audio_begin(self, f0: int) -> None:
        self._aend = self._anext = None
        try:
            path, end = await asyncio.to_thread(self._render_chunk, f0, 30)
        except Exception as ex:                                   # noqa: BLE001
            print("[audio]", ex)
            return
        if not self.playing:
            return
        if path:
            self.audio.play(path)
        self._aend = end
        self._kick_next()

    def _audio_tick(self, f: int) -> None:
        if self._aend is not None and f >= self._aend - 1 and self._anext is not None:
            (path, end), self._anext = self._anext, None
            if path:
                self.audio.play(path)
            self._aend = end
            self._kick_next()

    async def _loop(self) -> None:
        await self._audio_begin(self.s.playhead)
        t0, f0 = time.perf_counter(), self.s.playhead
        last = f0 - 1
        try:
            while self.playing:
                fps, dur = self.s.tl.fps, self.s.tl.duration
                f = f0 + int((time.perf_counter() - t0) * fps)
                if f >= dur:
                    self.s.set_playhead(dur)
                    break
                if f != last:
                    last = f
                    self._audio_tick(f)
                    self.s.set_playhead(f)
                    try:
                        await self._paint_stream(f)
                    except Exception as ex:                      # noqa: BLE001  un frame malo no para la reproducción
                        print("[preview] frame:", ex)
                await asyncio.sleep(max(0.001, t0 + (f + 1 - f0) / fps - time.perf_counter()))
        finally:
            self.playing = False
            self.audio.stop()
            self._aend = self._anext = None
            self.close_stream()
            self.play_btn.icon = ft.Icons.PLAY_ARROW
            safe_update(self.play_btn)
            self.show(self.s.playhead)

    def close_stream(self) -> None:
        if self._stream:
            try:
                self._stream["st"].close()
            except Exception:
                pass
            self._stream = None

    async def _paint_stream(self, frame: int) -> None:
        tl = self.s.tl
        self.tc.value = f"{tl.timecode(frame)}   /   {tl.timecode(tl.duration)}"
        clip = ed.top_video_clip_at(tl, frame)
        asset = self.s.project.assets.get(clip.asset_id) if (clip is not None and clip.asset_id) else None
        if clip is None or asset is None or clip.kind not in ("video", "image"):
            self.close_stream()
            self.show(frame)                                        # hueco / placeholder / vacío: dibujo normal
            return
        src_f = clip.source_in + int(round((frame - clip.start) * clip.speed))
        st = self._stream
        idx = src_f - st["src0"] if st else 0
        if st is None or st["clip"] != clip.id or idx < 0 or idx < st["st"].earliest():
            self.close_stream()
            self._stream = st = {"clip": clip.id, "src0": src_f,
                                 "st": self.s.backend.open_stream(asset.path, src_f / tl.fps, tl.fps, self.w)}
            idx = 0
        jpeg = await asyncio.to_thread(st["st"].get, idx, 0.06)
        if not jpeg:
            return                                                  # aún no hay frame: se mantiene el anterior
        if self._img is None:
            import base64
            self._img = make_image(base64.b64encode(jpeg).decode("ascii"), self.w, self.h)
        else:
            set_image_bytes(self._img, jpeg)
        self._img.width, self._img.height = self.w, self.h
        sig = (clip.id, dataclasses.astuple(self._xf(clip)))
        if self.holder.content is None or sig != self._xsig:     # solo se toca el contenedor al cambiar de control/transformación
            self._xsig = sig
            self.holder.content = self._wrap(self._img, clip)
            safe_update(self.holder)
        safe_update(self._img, self.tc)
