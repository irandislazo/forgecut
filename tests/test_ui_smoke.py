import asyncio, os, shutil, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(__file__))
import flet_stub
ft = flet_stub.install()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from helpers import make_template
from forgecut.ui import compat
from forgecut.ui.state import AppState
from forgecut.media.ffmpeg_backend import FFmpegBackend
from forgecut.ui.widgets.timeline_view import RULER_H


class Ev:                                   # evento falso con las dos variantes de nombres de Flet
    def __init__(self, x=0.0, dx=0.0, key="", ctrl=False, shift=False, value=None, y=0.0, dy=0.0):
        self.local_x, self.delta_x, self.key, self.ctrl, self.shift = x, dx, key, ctrl, shift
        self.local_y, self.delta_y = y, dy
        self.control = flet_stub.Control(value=value)


def walk(ctrl):
    yield ctrl
    for c in (getattr(ctrl, "controls", None) or []):
        yield from walk(c)
    for attr in ("content",):
        c = getattr(ctrl, attr, None)
        if isinstance(c, flet_stub.Control):
            yield from walk(c)


def run_tasks(page):
    while page.tasks:
        fn, a = page.tasks.pop(0)
        asyncio.run(fn(*a))


@unittest.skipUnless(FFmpegBackend().available(), "ffmpeg no disponible")
class TestUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp(prefix="ui_")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=96x54:r=30:d=2",
                        "-f", "lavfi", "-i", "color=c=blue:s=96x54:r=30:d=2", "-filter_complex", "concat=n=2:v=1:a=0",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", os.path.join(cls.d, "sc.mp4")], check=True)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=170",
                        os.path.join(cls.d, "song.wav")], check=True)
        os.environ["FLET_APP_STORAGE_DATA"] = os.path.join(cls.d, "data")
        import forgecut.ui.app as appmod
        cls.appmod = appmod
        cls.page = ft.Page()
        appmod.main(cls.page)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def find(self, root, pred):
        return [c for c in walk(root) if pred(c)]

    def test_01_main_builds_and_keys(self):
        self.assertTrue(self.page.added)
        self.assertTrue(self.page.on_keyboard_event)
        self.page.on_keyboard_event(Ev(key="V"))
        self.page.on_keyboard_event(Ev(key="B"))
        self.page.on_keyboard_event(Ev(key="V"))

    def test_02_import_timeline_drag_trim_razor(self):
        # reconstruye el estado accesible: main() lo creó internamente → lo obtenemos por el observador de teclado
        state = self.page.on_keyboard_event.__closure__ and next(
            c.cell_contents for c in self.page.on_keyboard_event.__closure__ if isinstance(c.cell_contents, AppState))
        (a,) = state.import_media([os.path.join(self.d, "sc.mp4")])
        clip = state.add_asset_to_timeline(a.id, at=0)
        # el TimelineView se refrescó: busca el clip en el árbol de controles
        tl_roots = [c for c in self.find(self.page.added[0], lambda c: type(c).__name__ == "Stack" and getattr(c, "width", 0) and c.width > 400)]
        self.assertTrue(tl_roots, "no se construyó el Stack del timeline")
        stack = tl_roots[0]
        groups = [g for g in stack.controls if type(g).__name__ == "Stack"]
        self.assertEqual(len(groups), 1)
        main_gd = groups[0].controls[0]
        # seleccionar
        main_gd.on_tap_down(Ev(x=10)); self.assertEqual(state.selection, [clip.id])
        # arrastrar 50 px (zoom .5 → 100 frames) con snap desactivado
        state.snap_on = False
        main_gd.on_pan_start(Ev()); main_gd.on_pan_update(Ev(dx=30)); main_gd.on_pan_update(Ev(dx=20))
        main_gd.on_pan_end(Ev())
        self.assertEqual(state.tl.track("V1").clips[0].start, 100)
        # recorte por el borde derecho: refrescar y tomar el nuevo grupo
        stack = [c for c in self.find(self.page.added[0], lambda c: type(c).__name__ == "Stack" and getattr(c, "width", 0) and c.width > 400)][0]
        g = [g for g in stack.controls if type(g).__name__ == "Stack"][0]
        right = g.controls[2]
        right.on_horizontal_drag_update(Ev(dx=-20)); right.on_horizontal_drag_end(Ev())      # −20 px = −40 frames
        self.assertEqual(state.tl.track("V1").clips[0].duration, 120 - 40)
        # cuchilla
        state.set_tool("razor")
        stack = [c for c in self.find(self.page.added[0], lambda c: type(c).__name__ == "Stack" and getattr(c, "width", 0) and c.width > 400)][0]
        g = [g for g in stack.controls if type(g).__name__ == "Stack"][0]
        overlay = [c for c in stack.controls if type(c).__name__ == "GestureDetector" and c.on_hover and not c.on_pan_start and c.top == RULER_H][0]
        overlay.on_hover(Ev(x=70, y=60))                                                       # fila 1 (V1), frame 140
        blade = [c for c in stack.controls if type(c).__name__ == "Container" and c.width == 1 and c.bgcolor == "#ffffff"][0]
        self.assertTrue(blade.visible); self.assertEqual(blade.left, 140 * 0.5)                  # la línea de corte sigue al ratón
        overlay.on_tap_down(Ev(x=70, y=60))                                                    # 70 px = 140 frames (dentro del clip 100–180)
        self.assertEqual(len(state.tl.track("V1").clips), 2)
        state.set_tool("select")
        # regla: clic y arrastre → playhead
        ruler = [c for c in stack.controls if type(c).__name__ == "GestureDetector"][0]
        ruler.on_tap_down(Ev(x=100)); self.assertEqual(state.playhead, 200)
        ruler.on_horizontal_drag_start(Ev(x=100)); ruler.on_horizontal_drag_update(Ev(dx=50)); self.assertEqual(state.playhead, 300)
        # atajos
        self.page.on_keyboard_event(Ev(key="z", ctrl=True)); self.assertEqual(len(state.tl.track("V1").clips), 1)
        self.page.on_keyboard_event(Ev(key="a", ctrl=True)); self.assertEqual(len(state.selection), 1)
        self.page.on_keyboard_event(Ev(key="Delete")); self.assertEqual(len(state.tl.track("V1").clips), 0)
        self.page.on_keyboard_event(Ev(key="arrow right")); self.page.on_keyboard_event(Ev(key="m"))
        self.assertTrue(state.tl.markers)

    def test_03_inspector_edits(self):
        state = next(c.cell_contents for c in self.page.on_keyboard_event.__closure__ if isinstance(c.cell_contents, AppState))
        state.new_project()
        (a,) = state.import_media([os.path.join(self.d, "sc.mp4")])
        clip = state.add_asset_to_timeline(a.id, at=0)
        insp = [c for c in self.find(self.page.added[0], lambda c: type(c).__name__ == "Column" and getattr(c, "scroll", None))]
        fields = [f for root in insp for f in walk(root) if type(f).__name__ == "TextField" and getattr(f, "label", "") == "Duración (s)"]
        self.assertTrue(fields, "el inspector no muestra el campo Duración")
        fields[0].on_submit(Ev(value=str(45 / state.tl.fps)))   # la duración se edita en segundos
        self.assertEqual(state.tl.track("V1").clips[0].duration, 45)
        sliders = [f for root in insp for f in walk(root) if type(f).__name__ == "Slider" and getattr(f, "max", 0) == 4.0]
        sliders[0].on_change_end(Ev(value=1.7))
        self.assertAlmostEqual(state.tl.track("V1").clips[0].transform.scale, 1.7)

    def test_04_templates_generate_deliver(self):
        state = next(c.cell_contents for c in self.page.on_keyboard_event.__closure__ if isinstance(c.cell_contents, AppState))
        state.new_project()
        # plantilla creada vía almacén y abierta en la página (sin FilePicker real)
        tpl = make_template("la_voz"); tpl.name = "demo"
        state.templates.save(tpl)
        from forgecut.ui.pages.templates_page import TemplatesPage
        shown = []
        tp = TemplatesPage(state, self.page, on_generated=lambda: shown.append(1))
        tp.open("demo")
        self.assertTrue(any("Plantilla lista" in (getattr(c, "args", ("",))[0] if getattr(c, "args", None) else "")
                            for c in walk(tp.editor) if type(c).__name__ == "Text"))
        tp.song_path = os.path.join(self.d, "song.wav")
        tp.seed = flet_stub.Control(value="4")
        asyncio.run(tp.generate())
        self.assertEqual(shown, [1])
        self.assertEqual(state.tl.duration, 170 * 30)
        # añadir clips a un grupo con selector falso
        async def fake_pick(*a, **k): return [os.path.join(self.d, "sc.mp4")]
        import forgecut.ui.pages.templates_page as tpmod
        orig = tpmod.pick_files; tpmod.pick_files = fake_pick
        try:
            tp.open("demo"); n0 = len(tp.tpl.pool("far"))
            tp.tpl.pools["far"] = []; asyncio.run(tp.add_files("far"))
            self.assertEqual(len(tp.tpl.pool("far")), 1)
            self.assertTrue(tp.tpl.pool("far")[0].duration_s > 3)
        finally:
            tpmod.pick_files = orig
        # deliver: exportar audio de todos los huecos
        from forgecut.ui.pages.deliver_page import DeliverPage
        dp = DeliverPage(state, self.page)
        dp.out_dir.value = os.path.join(self.d, "aud_out")
        asyncio.run(dp.export_audio())
        self.assertIn("✔", dp.audio_msg.value, dp.audio_msg.value)
        self.assertTrue(os.path.exists(os.path.join(dp.out_dir.value, "manifest.json")))

    def test_05_toolbar_autocut_and_preview(self):
        state = next(c.cell_contents for c in self.page.on_keyboard_event.__closure__ if isinstance(c.cell_contents, AppState))
        state.new_project()
        (a,) = state.import_media([os.path.join(self.d, "sc.mp4")])
        state.add_asset_to_timeline(a.id, at=0)
        from forgecut.ui.workspace import Toolbar
        tb = Toolbar(state, self.page)
        asyncio.run(tb._auto_cut(None))
        self.assertEqual(sorted(c.start for c in state.tl.track("V1").clips), [0, 60])
        from forgecut.ui.widgets.preview import Preview
        pv = Preview(state, self.page)
        state.set_playhead(70)
        import time; time.sleep(1.0)
        self.assertIsNotNone(pv.holder.content, "el visor no mostró frame")


if __name__ == "__main__":
    unittest.main()
