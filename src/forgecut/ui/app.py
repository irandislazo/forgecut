"""Aplicación ForgeCut: barra superior, páginas tipo DaVinci (Media · Cut · Edit · Plantillas · Deliver)."""
from __future__ import annotations

import asyncio
import inspect
import os
import time
from typing import Optional

import flet as ft

from ..core.project_io import EXT
from . import theme as T
import threading

from .compat import (TEXT_FOCUS, Choice, detect_ui_scale, load_prefs, save_prefs, ask_text, btn, close_dialog, icon_btn, no_auto_update, open_dialog, pick_files, safe_update,
                     text, toast, ui_call)
from .pages.deliver_page import DeliverPage
from .pages.templates_page import TemplatesPage
from .state import AppState
from .workspace import Workspace

PAGES = [("media", "Media", "PERM_MEDIA"), ("cut", "Cut", "CONTENT_CUT"), ("edit", "Edit", "EDIT"),
         ("templates", "Plantillas", "DASHBOARD_CUSTOMIZE"), ("deliver", "Deliver", "ROCKET_LAUNCH")]


def _diag() -> str:
    try:
        from ..media.ffmpeg_backend import ffmpeg_diag
        d = ffmpeg_diag() if os.environ.get("ANDROID_ROOT") else ""
        return f" [{d}]" if d else ""
    except Exception:
        return ""


def main(page: ft.Page, reopen: Optional[str] = None) -> None:
    page.title = "ForgeCut"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = T.BG
    page.padding = 0
    page.spacing = 0
    T.SCALE = detect_ui_scale()                          # pantallas pequeñas: botones, letras y paneles más compactos
    try:
        page.theme = ft.Theme(visual_density=ft.VisualDensity.COMPACT)
    except Exception:
        pass
    try:
        if reopen is None:                               # al reaplicar la escala se conserva el tamaño de ventana actual
            page.window.width, page.window.height = T.sc(1500), T.sc(900)
    except Exception:
        pass

    no_auto_update()
    state = AppState()
    async def _mark_ui_thread() -> None:                 # hilo del bucle de eventos de Flet = hilo de la interfaz
        state.ui_thread = threading.get_ident()
    state.dispatch = lambda fn: ui_call(page, fn)
    try:
        page.run_task(_mark_ui_thread)
    except Exception:
        pass
    ws = Workspace(state, page)
    deliver = DeliverPage(state, page)
    current = {"name": "edit"}
    holder = ft.Container(expand=True)
    page_bar = ft.Row(spacing=0, alignment=ft.MainAxisAlignment.CENTER)
    title = text("", 13, T.TEXT_DIM)

    def show(name: str) -> None:
        current["name"] = name
        if name in ("media", "cut", "edit"):
            ws.set_page(name)
            holder.content = ws.root
        elif name == "templates":
            holder.content = templates.root
        else:
            deliver.refresh_info()
            holder.content = deliver.root
        build_bar()
        safe_update(holder)

    def build_bar() -> None:
        page_bar.controls = [ft.Container(
            content=ft.Column([ft.Icon(getattr(ft.Icons, ic, ft.Icons.CIRCLE), size=18, color="#ffffff" if n == current["name"] else T.TEXT_DIM),
                               text(label, 11, "#ffffff" if n == current["name"] else T.TEXT_DIM)],
                              spacing=1, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            padding=8, width=None if ws.compact else 110, expand=ws.compact,
            bgcolor=T.PANEL_3 if n == current["name"] else T.PANEL,
            on_click=lambda e, k=n: show(k)) for n, label, ic in PAGES]
        safe_update(page_bar)

    templates = TemplatesPage(state, page, on_generated=lambda: show("edit"))

    # ── barra superior ─────────────────────────────────────────────
    async def on_open_file(e=None) -> None:
        paths = await pick_files(page, False, [EXT.lstrip(".")], "Abrir proyecto")
        if paths:
            _open_path(paths[0])

    def _open_path(path: str) -> None:
        state.autosave()                                        # no se pierde lo que había abierto
        try:
            state.open(path)
        except Exception as ex:
            toast(page, f"No se pudo abrir: {ex}")

    def on_open(e=None) -> None:
        items = []
        dlg = ft.AlertDialog(modal=False, title=ft.Text("Abrir proyecto"))

        def pick(path):
            close_dialog(page, dlg)
            _open_path(path)

        for name, path, mt in state.list_projects():
            items.append(ft.Container(
                padding=8, border_radius=4, bgcolor=T.PANEL_3, ink=True, on_click=lambda e, p=path: pick(p),
                content=ft.Column([text(name, 13, T.TEXT, bold=True),
                                   text(time.strftime("%d/%m/%Y %H:%M", time.localtime(mt)), 10, T.TEXT_DIM)], spacing=1)))
        if not items:
            items = [text("Aún no hay proyectos guardados.", 12, T.TEXT_DIM)]

        def browse(e):
            close_dialog(page, dlg)
            page.run_task(on_open_file)

        dlg.content = ft.Container(width=360, height=min(380, 70 * len(items) + 10),
                                   content=ft.Column(items, spacing=4, scroll=ft.ScrollMode.AUTO))
        dlg.actions = [ft.TextButton("Examinar archivo…", on_click=browse),
                       ft.TextButton("Cerrar", on_click=lambda e: close_dialog(page, dlg))]
        open_dialog(page, dlg)

    async def new_named(e=None) -> None:
        name = await ask_text(page, "Nuevo proyecto", "Nombre del proyecto", "Proyecto nuevo")
        if name and name.strip():
            state.autosave()
            state.new_project(name.strip())

    def do_save() -> None:
        try:
            toast(page, f"Guardado: {state.save()}")
        except Exception as ex:
            toast(page, f"No se pudo guardar: {ex}")

    async def save_named() -> None:
        name = await ask_text(page, "Guardar proyecto", "Nombre del proyecto", state.project.name)
        if name is None:
            return
        if name.strip():
            state.project.name = name.strip()
        do_save()

    last_key = {"t": 0.0}

    def on_save(e=None) -> None:
        if e is not None and time.monotonic() - last_key["t"] < 0.5:
            return                                              # Espacio/Intro/Tab sobre el botón enfocado NO guardan: solo Ctrl+S o un clic
        if state.project_path is None:                          # primer guardado: pide el nombre
            page.run_task(save_named)
        else:
            do_save()

    async def rename_title() -> None:
        name = await ask_text(page, "Nombre del proyecto", "Nombre", state.project.name)
        if name and name.strip():
            state.rename_project(name)

    def apply_scale(v: float) -> None:
        """Guarda la escala (0 = automática) y reconstruye la interfaz al instante con el mismo proyecto."""
        save_prefs(ui_scale=v)
        path = state.project_path
        if path is None or state.dirty:
            try:
                path = state.save()                          # nada se pierde: el proyecto se guarda y se vuelve a abrir
            except Exception:
                path = None
        try:
            page.clean()
        except Exception:
            page.controls.clear()
        main(page, path or "")

    def cycle_scale() -> None:
        """Miniventana «Tamaño de la interfaz»: se aplica al momento y se puede volver a Automático o a cualquier valor."""
        opts = [(0.0, "Automático"), (0.7, "70 %"), (0.8, "80 %"), (0.9, "90 %"), (1.0, "100 %"), (1.15, "115 %"), (1.3, "130 %")]
        cur = load_prefs().get("ui_scale") or 0.0
        dlg = ft.AlertDialog(modal=False, title=ft.Text("Tamaño de la interfaz"))

        def pick(k):
            close_dialog(page, dlg)
            apply_scale(float(k))
        ch = Choice([(str(v), lab) for v, lab in opts], str(min((v for v, _ in opts), key=lambda v: abs(v - float(cur)))),
                    on_change=pick)
        dlg.content = ft.Container(width=340, content=ft.Column([
            text(f"Ahora: {int(round(T.SCALE * 100))} %" + (" (automático)" if not cur else ""), 11, T.TEXT_DIM), ch.control,
            text("Se aplica al instante; el proyecto se guarda y se reabre.", 10, T.TEXT_DIM)], spacing=6, tight=True))
        dlg.actions = [ft.TextButton("Cerrar", on_click=lambda e: close_dialog(page, dlg))]
        open_dialog(page, dlg)

    topbar = ft.Container(bgcolor=T.PANEL, padding=6,
                          content=ft.Row([text("ForgeCut", 15, T.ACCENT, bold=True),
                                          ft.Container(content=title, on_click=lambda e: page.run_task(rename_title),
                                                       tooltip="Cambiar nombre del proyecto"), ft.Container(expand=True),
                                          btn("Nuevo", lambda e: page.run_task(new_named), "NOTE_ADD"),
                                          btn("Abrir", on_open, "FOLDER_OPEN"), btn("Guardar", on_save, "SAVE", tooltip="Guardar (Ctrl+S)"),
                          icon_btn("FORMAT_SIZE", lambda e: cycle_scale(), "Tamaño de la interfaz (Automático, 70–130 %)", 16, T.TEXT_DIM),
                                          icon_btn("UNDO", lambda e: state.undo(), "Deshacer"),
                                          icon_btn("REDO", lambda e: state.redo(), "Rehacer")], spacing=6))

    def on_state(kind: str) -> None:
        if kind == "error" and state.last_error:
            toast(page, state.last_error)
        if kind in ("project", "all", "timeline"):
            title.value = f"{state.project.name}{' •' if state.dirty else ''}" + \
                          ("" if state.backend.available() else "   ⚠ sin ffmpeg: se puede importar y editar; vista previa y exportación desactivadas" + _diag())
            safe_update(title)

    state.subscribe(on_state)

    # ── atajos de teclado ──────────────────────────────────────────
    def on_key(e) -> None:
        k = (getattr(e, "key", "") or "").lower()
        ctrl, shift = bool(getattr(e, "ctrl", False) or getattr(e, "meta", False)), bool(getattr(e, "shift", False))
        if k in (" ", "enter", "tab", "numpad enter"):
            last_key["t"] = time.monotonic()
        if shift or k in ("shift left", "shift right", "shift"):
            state.note_shift()                                  # Shift+clic = rango en el Media Pool
        elif k not in ("control left", "control right", "control", "meta left", "meta right", "meta", "alt left", "alt right", "alt"):
            state.release_shift()
        if ctrl or k.startswith(("control", "meta")):
            state.note_ctrl()                                   # habilita Ctrl+clic (multiselección)
            if k.startswith(("control", "meta")):
                return
        elif not ctrl and k not in ("shift left", "shift right", "alt left", "alt right", "shift", "alt"):
            state.release_ctrl()                                # Flet no avisa al soltar Ctrl: otra tecla lo implica
        if k == "escape":
            TEXT_FOCUS[0] = False
        if TEXT_FOCUS[0] and not (ctrl and k == "s"):
            return                                              # escribiendo en un campo: Supr/Retroceso/espacio editan el texto
        if k in (" ", "tab"):
            ws.preview.toggle_play()
        elif ctrl and k == "z":
            state.undo()
        elif ctrl and k == "y" or (ctrl and shift and k == "z"):
            state.redo()
        elif ctrl and k == "b":
            state.split_at_playhead()
        elif ctrl and k == "s":
            on_save()
        elif ctrl and k == "a":
            state.select([c.id for c in state.tl.all_clips()])
        elif k in ("delete", "backspace"):
            state.delete_selected(ripple=shift)
        elif k == "v":
            state.set_tool("select")
        elif k == "b":
            state.set_tool("razor")
        elif k == "m":
            state.add_marker()
        elif k == "arrow up":
            state.goto_edit(-1)                                 # punto de edición anterior (como DaVinci)
        elif k == "arrow down":
            state.goto_edit(1)
        elif k in ("arrow left", "arrow right"):
            state.set_playhead(state.playhead + (-1 if "left" in k else 1) * (10 if shift else 1))
        elif k == "home":
            state.set_playhead(0)
        elif k == "end":
            state.set_playhead(state.tl.duration)

    page.on_keyboard_event = on_key

    # ── autoguardado (como DaVinci): cada minuto, al cerrar y al pasar a segundo plano ──
    async def _autosave_loop() -> None:
        while True:
            await asyncio.sleep(60)
            state.autosave()

    def on_lifecycle(e=None) -> None:
        if "resume" not in str(getattr(e, "state", "")).lower():
            state.autosave()

    async def on_window(e) -> None:
        if "close" in str(getattr(e, "type", "")).lower():
            state.autosave()
            try:
                page.window.prevent_close = False
                r = page.window.destroy()
                if inspect.isawaitable(r):
                    await r
            except Exception:
                pass

    try:
        page.run_task(_autosave_loop)
        page.on_app_lifecycle_state_change = on_lifecycle
        if not os.environ.get("ANDROID_ROOT"):
            page.window.prevent_close = True
            page.window.on_event = on_window
    except Exception:
        pass

    # ── diseño adaptable (móvil / ventana estrecha) ────────────────
    last_w = {"w": 0.0}

    def on_resize(e=None) -> None:
        try:
            w = float(getattr(page, "width", 0) or 0)
        except (TypeError, ValueError):
            w = 0.0
        if w > 0 and abs(w - last_w["w"]) > 1:
            last_w["w"] = w
            ws.apply_width(w)
            templates.apply_width(w)
            deliver.apply_width(w)
            build_bar()

    page.on_resize = on_resize
    page.add(ft.Column([topbar, holder, ft.Container(content=page_bar, bgcolor=T.PANEL)], spacing=0, expand=True))
    on_state("all")
    show("edit")
    on_resize()
    if reopen:
        try:
            state.open(reopen)
        except Exception as ex:
            toast(page, f"No se pudo reabrir el proyecto: {ex}")
