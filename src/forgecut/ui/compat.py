"""Capa de compatibilidad con Flet.

TODO lo que cambia entre versiones de Flet (pickers, diálogos, eventos, nombres de botones)
vive aquí. Si una actualización de Flet rompe algo, se arregla en este único archivo.
Probado contra la serie 0.8x (flet.run / servicios / FilePicker asíncrono).
"""
from __future__ import annotations

import asyncio
import threading
from typing import Callable, List, Optional

import flet as ft

from . import theme as T


# ── actualización automática / hilos ───────────────────────────────
def no_auto_update() -> None:
    """Flet ≥1.0 repinta (diff) la PÁGINA ENTERA tras cada manejador de evento. En el timeline eso ocurre en cada
    movimiento de ratón y es el mayor cuello de botella: aquí ya se actualiza explícitamente solo lo que cambia."""
    try:
        ft.context.disable_auto_update()
    except Exception:
        pass


def quiet(fn):
    """Envuelve un manejador de evento para que Flet no haga el diff global tras ejecutarlo."""
    if fn is None:
        return None
    if asyncio.iscoroutinefunction(fn):
        async def aw(*a, **k):
            no_auto_update()
            return await fn(*a, **k)
        return aw

    def w(*a, **k):
        no_auto_update()
        return fn(*a, **k)
    return w


def ui_call(page, fn: Callable) -> None:
    """Ejecuta `fn` en el hilo/bucle de la interfaz (los controles NO deben modificarse desde otros hilos:
    provoca «dictionary changed size during iteration» en el diff de Flet)."""
    async def _c():
        fn()
    if getattr(page, "_forgecut_sync", False):          # pruebas: sin bucle real
        fn()
        return
    try:
        page.run_task(_c)
    except Exception:
        fn()


def safe_update(*controls) -> None:
    for c in controls:
        try:
            c.update()
        except Exception:
            pass


def text(value: str = "", size: int = 12, color: str = T.TEXT, bold: bool = False, **kw) -> ft.Text:
    return ft.Text(value, size=size, color=color,
                   weight=ft.FontWeight.BOLD if bold else None, **kw)


def icon_btn(icon_name: str, on_click=None, tooltip: str = "", size: int = 18, color: str = T.TEXT) -> ft.IconButton:
    icon = getattr(ft.Icons, icon_name, ft.Icons.CIRCLE)
    return ft.IconButton(icon=icon, icon_size=size, icon_color=color, tooltip=tooltip, on_click=on_click)


def btn(label: str, on_click=None, icon: Optional[str] = None, primary: bool = False, tooltip: str = "") -> ft.Control:
    """Botón de texto hecho con Container: no depende del nombre de clase de Flet (Button/ElevatedButton)."""
    row = []
    if icon:
        row.append(ft.Icon(getattr(ft.Icons, icon, ft.Icons.CIRCLE), size=14, color="#ffffff" if primary else T.TEXT))
    row.append(text(label, 12, "#ffffff" if primary else T.TEXT))
    return ft.Container(content=ft.Row(row, spacing=6, tight=True), on_click=on_click, tooltip=tooltip or None,
                        bgcolor=T.ACCENT if primary else T.PANEL_3, padding=8, border_radius=4, ink=True)


class Choice:
    """Selector de opciones exclusivas (sustituye al Dropdown/SegmentedButton, que cambian entre versiones).
    Composición, no herencia: el control a insertar es `.control`."""

    def __init__(self, options: List[tuple], value: str, on_change: Optional[Callable[[str], None]] = None):
        self.options, self.value, self._cb = options, value, on_change
        self.control = ft.Row(spacing=4, run_spacing=4, wrap=True)
        self._build()

    def _build(self):
        self.control.controls = [ft.Container(
            content=text(label, 12, "#ffffff" if key == self.value else T.TEXT),
            bgcolor=T.ACCENT if key == self.value else T.PANEL_3, border_radius=4,
            padding=8, on_click=lambda e, k=key: self.select(k)) for key, label in self.options]

    def select(self, key: str):
        self.value = key
        self._build()
        safe_update(self.control)
        if self._cb:
            self._cb(key)


def _new_image(**kw) -> ft.Image:
    """ft.Image con gapless_playback (mantiene el fotograma anterior en pantalla mientras se decodifica el
    siguiente; sin esto Flutter deja el recuadro vacío entre frames y la reproducción «parpadea» en negro).
    Si esta versión de Flet no conoce el parámetro, se crea sin él."""
    try:
        return ft.Image(gapless_playback=True, **kw)
    except TypeError:
        return ft.Image(**kw)


def make_image(b64: str, width: int, height: int, **extra) -> ft.Control:
    """Imagen desde base64 (el nombre del parámetro cambió entre versiones)."""
    try:
        img = _new_image(src_base64=b64, width=width, height=height, **extra)
        _IMG_MODE[id(img)] = "src_base64"
        return img
    except TypeError:
        try:
            img = _new_image(src=f"data:image/jpeg;base64,{b64}", width=width, height=height, **extra)
        except TypeError:
            img = _new_image(src=f"data:image/jpeg;base64,{b64}", width=width, height=height)
        _IMG_MODE[id(img)] = "src"
        return img


# ── eventos de ratón (la forma de leer posición cambió entre versiones) ──
def evt_x(e) -> float:
    for path in (("local_position", "x"), ("local_x",), ("x",)):
        o = e
        try:
            for p in path:
                o = getattr(o, p)
            if o is not None:
                return float(o)
        except Exception:
            continue
    return 0.0


def evt_dx(e) -> float:
    for path in (("local_delta", "x"), ("delta_x",), ("primary_delta",), ("delta", "x")):
        o = e
        try:
            for p in path:
                o = getattr(o, p)
            if o is not None:
                return float(o)
        except Exception:
            continue
    return 0.0


def evt_y(e) -> float:
    for path in (("local_position", "y"), ("local_y",), ("y",)):
        o = e
        try:
            for p in path:
                o = getattr(o, p)
            if o is not None:
                return float(o)
        except Exception:
            continue
    return 0.0


def evt_dy(e) -> float:
    for path in (("local_delta", "y"), ("delta_y",), ("delta", "y")):
        o = e
        try:
            for p in path:
                o = getattr(o, p)
            if o is not None:
                return float(o)
        except Exception:
            continue
    return 0.0


def evt_gxy(e):
    """Posición GLOBAL (ventana) del evento, o None si esta versión de Flet no la da."""
    for px, py in (("global_position.x", "global_position.y"), ("global_x", "global_y")):
        try:
            o1, o2 = e, e
            for part in px.split("."):
                o1 = getattr(o1, part)
            for part in py.split("."):
                o2 = getattr(o2, part)
            if o1 is not None and o2 is not None:
                return float(o1), float(o2)
        except Exception:
            continue
    return None


def fit_fill():
    """Valor «estirar a todo el recuadro» para ft.Image (el enum cambió de nombre entre versiones)."""
    for enum_name in ("BoxFit", "ImageFit"):
        enum = getattr(ft, enum_name, None)
        v = getattr(enum, "FILL", None) if enum is not None else None
        if v is not None and not isinstance(v, str):
            return v
    return None


def hard_clip() -> dict:
    """kwargs para que un Container recorte lo que sobresale (miniaturas desplazadas dentro del clip)."""
    cb = getattr(ft, "ClipBehavior", None)
    v = getattr(cb, "HARD_EDGE", None) if cb is not None else None
    return {"clip_behavior": v} if v is not None and not isinstance(v, str) else {}


_B64_CACHE: dict = {}


def file_image(path: str, width: float, height: float) -> ft.Control:
    """Imagen de un archivo local estirada a width×height (tira de miniaturas / forma de onda).
    Se carga como base64 (una vez por archivo, en caché): una ruta local no siempre la resuelve Flet en Windows."""
    f = fit_fill()
    extra = {"fit": f} if f is not None else {}
    try:
        b64 = _B64_CACHE.get(path)
        if b64 is None:
            import base64
            with open(path, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode("ascii")
            if len(_B64_CACHE) > 200:
                _B64_CACHE.pop(next(iter(_B64_CACHE)))
            _B64_CACHE[path] = b64
        return make_image(b64, width, height, **extra)
    except OSError:
        kw = {"src": path, "width": width, "height": height, **extra}
        try:
            return _new_image(**kw)
        except TypeError:
            kw.pop("fit", None)
            return _new_image(**kw)


_IMG_MODE: dict = {}


def set_image_bytes(ctl, jpeg: bytes) -> None:
    """Cambia el contenido de una imagen existente (reproducción: evita crear un control por frame)."""
    import base64
    b64 = base64.b64encode(jpeg).decode("ascii")
    mode = _IMG_MODE.get(id(ctl), "src_base64")
    if mode == "src_base64":
        ctl.src_base64 = b64
    else:
        ctl.src = f"data:image/jpeg;base64,{b64}"


def draggable(content, data: str, group: str = "media", on_start=None):
    """Elemento arrastrable dentro de la app (Media Pool → timeline)."""
    kw = {"group": group, "content": content, "data": data}
    if on_start is not None:
        try:
            return ft.Draggable(on_drag_start=on_start, **kw)
        except TypeError:
            pass
    return ft.Draggable(**kw)


_DROP_LOGGED = False


def evt_drop_xy(e):
    """Posición del soltado dentro del DragTarget. El nombre/espacio de coordenadas cambia entre versiones de
    Flet: se prueban las variantes locales primero. La primera vez se imprimen los atributos para depurar."""
    global _DROP_LOGGED
    if not _DROP_LOGGED:
        _DROP_LOGGED = True
        try:
            print("[drop] atributos del evento:", {k: getattr(e, k, None) for k in dir(e)
                                                    if not k.startswith("_") and k in ("x", "y", "local_x", "local_y",
                                                                                      "local_position", "global_position", "src_id", "data")})
        except Exception:
            pass
    return evt_x(e), evt_y(e)


# ── notificaciones y diálogos ──────────────────────────────────────
def toast(page: ft.Page, msg: str) -> None:
    try:
        sb = ft.SnackBar(ft.Text(msg))
        if hasattr(page, "show_dialog"):
            page.show_dialog(sb)
        elif hasattr(page, "open"):
            page.open(sb)
        else:
            page.snack_bar = sb
            sb.open = True
            page.update()
    except Exception:
        print("[toast]", msg)


def open_dialog(page: ft.Page, dlg: ft.AlertDialog) -> None:
    if hasattr(page, "show_dialog"):
        page.show_dialog(dlg)
    elif hasattr(page, "open"):
        page.open(dlg)
    else:
        page.dialog = dlg
        dlg.open = True
        page.update()


def close_dialog(page: ft.Page, dlg: ft.AlertDialog) -> None:
    try:
        if hasattr(page, "pop_dialog"):
            page.pop_dialog()
        elif hasattr(page, "close"):
            page.close(dlg)
        else:
            dlg.open = False
            page.update()
    except Exception:
        pass


async def ask_text(page: ft.Page, title: str, hint: str = "", initial: str = "") -> Optional[str]:
    """Diálogo de una línea (también sirve de plan B si el selector de archivos no está disponible)."""
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    field = ft.TextField(value=initial, hint_text=hint, autofocus=True, width=420)

    def done(val):
        close_dialog(page, dlg)
        if not fut.done():
            fut.set_result(val)

    dlg = ft.AlertDialog(modal=True, title=ft.Text(title), content=field, actions=[
        ft.TextButton("Cancelar", on_click=lambda e: done(None)),
        ft.TextButton("Aceptar", on_click=lambda e: done(field.value or None))])
    open_dialog(page, dlg)
    return await fut


# ── selectores de archivo ──────────────────────────────────────────
_picker = None


def _get_picker(page: ft.Page):
    global _picker
    if _picker is None:
        _picker = ft.FilePicker()
        try:
            page.services.append(_picker)
        except Exception:
            page.overlay.append(_picker)
        page.update()
    return _picker


async def pick_files(page: ft.Page, multiple: bool = True, exts: Optional[List[str]] = None,
                     title: str = "Selecciona archivos") -> List[str]:
    try:
        p = _get_picker(page)
        kw = {"allow_multiple": multiple, "dialog_title": title}
        if exts:
            kw["file_type"] = ft.FilePickerFileType.CUSTOM
            kw["allowed_extensions"] = [x.lstrip(".") for x in exts]
        res = await p.pick_files(**kw)
        return [f.path for f in (res or []) if getattr(f, "path", None)]
    except Exception:
        v = await ask_text(page, title, "Pega la ruta completa (varias separadas por ;)")
        return [x.strip().strip('"') for x in v.split(";") if x.strip()] if v else []


async def pick_dir(page: ft.Page, title: str = "Selecciona carpeta") -> Optional[str]:
    try:
        return await _get_picker(page).get_directory_path(dialog_title=title)
    except Exception:
        return await ask_text(page, title, "Pega la ruta de la carpeta")


async def pick_save(page: ft.Page, title: str, filename: str, ext: str) -> Optional[str]:
    try:
        p = await _get_picker(page).save_file(dialog_title=title, file_name=filename,
                                              allowed_extensions=[ext.lstrip(".")])
        return p
    except Exception:
        return await ask_text(page, title, "Ruta completa de destino", initial=filename)


# ── trabajo en segundo plano ───────────────────────────────────────
def run_bg(fn: Callable, *args, on_done: Optional[Callable] = None, on_error: Optional[Callable] = None):
    def work():
        try:
            r = fn(*args)
            if on_done:
                on_done(r)
        except Exception as ex:                       # noqa: BLE001
            if on_error:
                on_error(ex)
            else:
                print("[bg error]", ex)
    threading.Thread(target=work, daemon=True).start()
