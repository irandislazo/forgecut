"""Flet simulado: acepta cualquier control/atributo. Sirve para ejecutar la lógica de ForgeCut sin Flet real."""
import sys, types, asyncio


class _Meta(type):
    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return name


class Control(metaclass=_Meta):
    def __init__(self, *args, **kw):
        self.__dict__.update(kw)
        self.args = args
        if "controls" not in kw:
            self.controls = list(args[0]) if args and isinstance(args[0], list) else []
        self.updates = 0

    def update(self):
        self.updates += 1

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return None


class Enum(metaclass=_Meta):
    pass


class Page(Control):
    _forgecut_sync = True

    def __init__(self):
        super().__init__()
        self.added, self.tasks, self.services, self.overlay = [], [], [], []
        self.window = Control()

    def add(self, *c): self.added += c
    def update(self): pass
    def run_task(self, fn, *a): self.tasks.append((fn, a)); return None
    def show_dialog(self, d): self.dialog = d
    def pop_dialog(self): self.dialog = None


def install():
    m = types.ModuleType("flet")
    names = ["Container", "Row", "Column", "Stack", "Text", "TextField", "IconButton", "Icon", "Image", "Slider", "Checkbox",
             "ProgressBar", "GestureDetector", "Draggable", "DragTarget", "Divider", "AlertDialog", "TextButton", "SnackBar", "FilePicker", "Padding"]
    for n in names:
        setattr(m, n, type(n, (Control,), {}))
    for n in ["Icons", "Colors", "FontWeight", "TextOverflow", "ScrollMode", "MainAxisAlignment", "CrossAxisAlignment",
              "ThemeMode", "MouseCursor", "FilePickerFileType"]:
        setattr(m, n, type(n, (Enum,), {}))
    m.Page = Page
    m.run = lambda *a, **k: None
    sys.modules["flet"] = m
    return m
