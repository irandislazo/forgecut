"""Inspector: propiedades del clip seleccionado (tiempo, velocidad, audio, transformación, recorte)."""
from __future__ import annotations

import flet as ft

from .. import theme as T
from ..compat import btn, icon_btn, safe_update, text


def _num(v, default, cast=float):
    try:
        return cast(str(v).replace(",", ".").strip())
    except (ValueError, TypeError):
        return default


class Inspector:
    def __init__(self, state):
        self.s = state
        self.col = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)
        self.root = ft.Container(content=self.col, bgcolor=T.PANEL, padding=10, expand=True)
        state.subscribe(lambda k: self.refresh() if k in ("selection", "timeline", "all") else None)
        self.refresh()

    # ── piezas de formulario ───────────────────────────────────────
    def _title(self, label):
        return ft.Container(content=text(label.upper(), 10, T.TEXT_DIM, bold=True), padding=4)

    def _field(self, label, value, on_apply, width=110):
        def apply(e):
            if str(e.control.value) != str(value):        # sin cambio real no se edita (evita bucles blur→editar→reconstruir)
                on_apply(e.control.value)
        tf = ft.TextField(label=label, value=str(value), width=width, dense=True, text_size=12,
                          on_submit=apply, on_blur=apply)
        return tf

    def _slider(self, label, value, lo, hi, on_apply, divisions=None):
        lab = text(f"{label}: {value:.2f}", 11, T.TEXT_DIM)

        def change(e):
            lab.value = f"{label}: {float(e.control.value):.2f}"
            safe_update(lab)

        def end(e):
            on_apply(float(e.control.value))
        return ft.Column([lab, ft.Slider(min=lo, max=hi, value=value, divisions=divisions,
                                         on_change=change, on_change_end=end)], spacing=0)

    def _nudge(self, label, value, lo, hi, step, on_apply, default=None, fmt="{:.2f}"):
        """Control táctil: [−] deslizable [+] y ↺. Se cambia sin escribir; − / + suman o restan `step`."""
        st = {"v": min(hi, max(lo, float(value)))}
        lab = text(f"{label}: {fmt.format(st['v'])}", 11, T.TEXT_DIM)
        sl = ft.Slider(min=lo, max=hi, value=st["v"], expand=True)

        def show(v):
            lab.value = f"{label}: {fmt.format(v)}"
            safe_update(lab)

        def apply(v):
            v = round(min(hi, max(lo, float(v))), 4)
            if v != st["v"]:
                st["v"] = v
                on_apply(v)

        sl.on_change = lambda e: show(float(e.control.value))
        sl.on_change_end = lambda e: apply(float(e.control.value))
        head = [lab, ft.Container(expand=True)]
        if default is not None:
            head.append(icon_btn("RESTART_ALT", lambda e: apply(default), "Restablecer", 14, T.TEXT_DIM))
        return ft.Column([ft.Row(head, spacing=0),
                          ft.Row([icon_btn("REMOVE", lambda e: apply(st["v"] - step), f"− {step}", 16), sl,
                                  icon_btn("ADD", lambda e: apply(st["v"] + step), f"+ {step}", 16)], spacing=0)], spacing=0)

    # ── contenido ──────────────────────────────────────────────────
    def refresh(self) -> None:
        s = self.s
        sel = s.selected_clips()
        if len(sel) != 1:
            self.col.controls = self._timeline_panel(len(sel))
        else:
            self.col.controls = self._clip_panel(sel[0])
        safe_update(self.col)

    def _timeline_panel(self, n: int):
        tl = self.s.tl
        msg = "Selecciona un clip para editar sus propiedades." if n == 0 else f"{n} clips seleccionados."
        out = [text("Inspector", 14, bold=True), text(msg, 12, T.TEXT_DIM), self._title("Timeline"),
               text(f"{tl.width}×{tl.height}  ·  {tl.fps} fps", 12),
               text(f"Duración: {tl.timecode(tl.duration)}  ({tl.duration} frames)", 12),
               text(f"Clips: {len(tl.all_clips())}   Marcadores: {len(tl.markers)}", 12)]
        if n > 1:
            out += [self._title("Acciones"), btn("Borrar seleccionados", lambda e: self.s.delete_selected(), "DELETE")]
        return out

    def _clip_panel(self, c):
        s = self.s
        sp = lambda **kw: s.set_props(c.id, **kw)
        t = c.transform
        fps = float(s.tl.fps)
        sec = lambda f: f"{f / fps:.2f}"                                   # frames → segundos
        frm = lambda v, cur: max(0, round(_num(v, float(cur)) * fps))       # segundos → frames
        out = [text("Inspector", 14, bold=True), text(f"{c.kind} · pista {c.track_id}", 11, T.TEXT_DIM),
               self._field("Nombre", c.name, lambda v: sp(name=v), 230), self._title("Tiempo (segundos)"),
               ft.Row([self._field("Inicio (s)", sec(c.start), lambda v: sp(start=frm(v, sec(c.start)))),
                       self._field("Duración (s)", sec(c.duration), lambda v: sp(duration=max(1, frm(v, sec(c.duration)))))], spacing=6),
               ft.Row([self._field("Entrada medio (s)", sec(c.source_in), lambda v: sp(source_in=frm(v, sec(c.source_in)))),
                       self._field("Velocidad", c.speed, lambda v: sp(speed=max(0.1, _num(v, c.speed))))], spacing=6),
               text(f"Fin: {sec(c.end)} s  ·  {s.tl.timecode(c.start)} → {s.tl.timecode(c.end)}  ·  {c.duration} frames", 10, T.TEXT_DIM)]
        if c.kind in ("video", "image", "placeholder"):
            out += [self._title("Transformación"),
                    self._nudge("Zoom", t.scale, 0.1, 4.0, 0.05, lambda v: sp(**{"transform.scale": v}), 1.0),
                    self._nudge("Posición X (px)", t.x, -float(s.tl.width), float(s.tl.width), 10,
                                lambda v: sp(**{"transform.x": v}), 0.0, "{:.0f}"),
                    self._nudge("Posición Y (px)", t.y, -float(s.tl.height), float(s.tl.height), 10,
                                lambda v: sp(**{"transform.y": v}), 0.0, "{:.0f}"),
                    self._nudge("Rotación °", t.rotation, -180, 180, 1, lambda v: sp(**{"transform.rotation": v}), 0.0, "{:.0f}"),
                    self._nudge("Opacidad", t.opacity, 0.0, 1.0, 0.05, lambda v: sp(**{"transform.opacity": v}), 1.0),
                    self._title("Recorte (0–0.9)"),
                    self._nudge("Izquierda", t.crop_l, 0.0, 0.9, 0.01, lambda v: sp(**{"transform.crop_l": v}), 0.0),
                    self._nudge("Derecha", t.crop_r, 0.0, 0.9, 0.01, lambda v: sp(**{"transform.crop_r": v}), 0.0),
                    self._nudge("Arriba", t.crop_t, 0.0, 0.9, 0.01, lambda v: sp(**{"transform.crop_t": v}), 0.0),
                    self._nudge("Abajo", t.crop_b, 0.0, 0.9, 0.01, lambda v: sp(**{"transform.crop_b": v}), 0.0),
                    ft.Row([ft.Checkbox(label="Voltear H", value=t.flip_h, on_change=lambda e: sp(**{"transform.flip_h": bool(e.control.value)})),
                            ft.Checkbox(label="Voltear V", value=t.flip_v, on_change=lambda e: sp(**{"transform.flip_v": bool(e.control.value)}))])]
        out += [self._title("Audio"),
                self._nudge("Volumen", c.volume, 0.0, 2.0, 0.05, lambda v: sp(volume=v), 1.0),
                ft.Row([ft.Checkbox(label="Silenciar", value=c.mute, on_change=lambda e: sp(mute=bool(e.control.value))),
                        ft.Checkbox(label="Activo", value=c.enabled, on_change=lambda e: sp(enabled=bool(e.control.value)))])]
        if c.kind == "placeholder":
            out += [self._title("Lipsync"),
                    text(f"Hueco #{c.meta.get('index', '?')} · plano {c.meta.get('shot', '?')}", 12)]
        out += [self._title("Acciones"),
                ft.Row([btn("Cuchilla (playhead)", lambda e: s.split_at_playhead(), "CONTENT_CUT"),
                        btn("Duplicar", lambda e: s.duplicate_selected(), "CONTENT_COPY")], wrap=True, spacing=6),
                ft.Row([btn("Borrar", lambda e: s.delete_selected(False), "DELETE"),
                        btn("Borrar + ripple", lambda e: s.delete_selected(True), "DELETE_SWEEP")], wrap=True, spacing=6)]
        return out
