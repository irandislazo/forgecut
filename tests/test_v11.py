import os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(__file__))
import flet_stub
if "flet" not in sys.modules:
    flet_stub.install()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from forgecut.core import editing as ed
from forgecut.core.models import Clip, MediaAsset, new_id
from forgecut.ui.state import AppState
from forgecut.ui.widgets.media_pool import MediaPool
from forgecut.ui.widgets.timeline_view import TimelineView
from forgecut.ui.widgets.preview import Preview
from forgecut.ui.widgets.inspector import Inspector


class Ev:
    def __init__(self, x=0.0, y=0.0, dx=0.0, dy=0.0, value=None):
        self.local_x, self.local_y, self.delta_x, self.delta_y = x, y, dx, dy
        self.control = flet_stub.Control(value=value)


class T(unittest.TestCase):
    def setUp(self):
        self.s = AppState(data_dir=tempfile.mkdtemp()); self.s.ensure_visuals = lambda *_: None
        self.s.snap_on = False; self.s.zoom = 1.0

    def clip(self, start=0, dur=100, track="V1"):
        c = Clip(id=new_id("c"), kind="video", track_id=track, start=start, duration=dur, name="c")
        self.s.do(lambda tl: ed.add_clip(tl, track, c)); return c

    def test_pool_select_all_and_none_buttons(self):
        for i in range(3):
            self.s.project.add_asset(MediaAsset(id=f"m{i}", path=f"/x/{i}.mp4", name=f"{i}.mp4"))
        pool = MediaPool(self.s, flet_stub.Page())
        pool.all_btn.on_click(None)
        self.assertEqual(set(self.s.pool_sel), {"m0", "m1", "m2"})
        pool.none_btn.on_click(None)
        self.assertEqual(self.s.pool_sel, [])

    def test_inspector_slider_drives_live_preview_override_then_clears(self):
        c = self.clip(); self.s.select([c.id])
        insp = Inspector(self.s)
        sliders = [x for k in insp.col.controls for x in self._walk(k) if type(x).__name__ == "Slider"]
        zoom = sliders[0]
        zoom.on_change(Ev(value=1.8))
        self.assertEqual(self.s.live_props[c.id]["scale"], 1.8)
        zoom.on_change_end(Ev(value=1.8))
        self.assertEqual(self.s.live_props, {})
        self.assertAlmostEqual(self.s.tl.all_clips()[0].transform.scale, 1.8)

    def _walk(self, c):
        yield c
        for k in (getattr(c, "controls", None) or []): yield from self._walk(k)
        k = getattr(c, "content", None)
        if isinstance(k, flet_stub.Control): yield from self._walk(k)

    def test_preview_wraps_image_with_transform_and_live_override(self):
        c = self.clip()
        pv = Preview(self.s, flet_stub.Page())
        img = flet_stub.Control()
        self.assertIs(pv._wrap(img, c), img)                         # sin transformación: la imagen tal cual
        self.s.set_live(c.id, "x", 100); self.s.set_live(c.id, "scale", 2.0)
        w = pv._wrap(img, c)
        inner = w.content.controls[0]
        self.assertAlmostEqual(inner.left, 100 * pv.w / self.s.tl.width)
        self.assertIsNotNone(inner.scale)

    def test_edit_modes_change_handles_and_gestures(self):
        c = self.clip(); tv = TimelineView(self.s)
        grp = lambda: [g for g in tv.stack.controls if type(g).__name__ == "Stack" and getattr(g, "controls", None)][0]
        self.assertEqual(len(grp().controls), 3)                     # auto: clip + 2 bordes
        self.s.set_edit_mode("move")
        self.assertEqual(len(grp().controls), 1)                     # mover: sin bordes, todo el clip se arrastra
        self.assertIsNone(grp().controls[0].on_pan_start)
        self.assertTrue(grp().controls[0].on_horizontal_drag_start and grp().controls[0].on_vertical_drag_start)   # gana al scroll
        self.s.set_edit_mode("trim")
        g = grp()
        self.assertEqual(len(g.controls), 3)
        self.assertIsNone(g.controls[0].on_pan_start)                # recortar: el cuerpo no arrastra
        self.assertGreaterEqual(g.controls[1].width, 28)             # bordes grandes para el dedo

    def test_edge_autoscroll_rates(self):
        tv = TimelineView(self.s); tv.viewport_w = 800; tv._view = (0.0, 800.0)
        self.assertEqual(tv._edge_rate(100, 300), 0.0)
        self.assertGreater(tv._edge_rate(600, 790), 0)               # cerca del borde derecho → scroll a la derecha
        tv._view = (500.0, 800.0)
        self.assertLess(tv._edge_rate(520, 600), 0)                  # cerca del borde izquierdo → a la izquierda

    def test_drag_grows_timeline_and_autoscroll_moves_clip_with_scroll(self):
        c = self.clip(0, 50); tv = TimelineView(self.s); tv.viewport_w = 800; tv._view = (0.0, 800.0)
        self.s.select([c.id])
        g = [x for x in tv.stack.controls if type(x).__name__ == "Stack" and getattr(x, "controls", None)][0]
        g.controls[0].on_pan_start(Ev()); self.s.dispatch = None
        d = tv._drag
        d["dx"] = 760; tv._apply_drag(d)
        self.assertGreater(tv._auto["rate"], 0)
        base = d["dx"]; tv._scroll_by(40); d["dx"] += 40; tv._apply_drag(d)
        self.assertEqual(d["dx"], base + 40)
        self.assertGreaterEqual(tv.stack.width, 800 + 600)           # el timeline crece para seguir hacia la derecha
        g.controls[0].on_pan_end(Ev())
        self.assertGreater(self.s.tl.all_clips()[0].start, 700)

    def test_touch_autoscroll_needs_finger_push_toward_edge(self):
        tv = TimelineView(self.s); tv.viewport_w = 800; tv._view = (0.0, 800.0); tv.INTENT_SCROLL = True
        tv._push = 0.0
        self.assertEqual(tv._edge_rate(600, 790), 0.0)               # tocar el borde sin empujar: no se desplaza
        tv._push = -9.0
        self.assertEqual(tv._edge_rate(600, 790), 0.0)               # empuje en sentido contrario: tampoco
        tv._push = 6.0
        r1 = tv._edge_rate(600, 790)
        tv._push = 12.0
        r2 = tv._edge_rate(600, 790)
        self.assertTrue(0 < r1 < r2 <= 30)                           # más empuje = más velocidad, con tope suave
        tv.INTENT_SCROLL = False; tv._push = 0.0
        self.assertGreater(tv._edge_rate(600, 790), 0)               # escritorio: igual que antes

    def test_move_mode_single_touch_drags_clip_on_both_axes_and_ends_once(self):
        c = self.clip(0, 50); tv = TimelineView(self.s); self.s.select([c.id]); self.s.set_edit_mode("move")
        g = [x for x in tv.stack.controls if type(x).__name__ == "Stack" and getattr(x, "controls", None)][0].controls[0]
        g.on_horizontal_drag_start(Ev()); g.on_vertical_drag_start(Ev())
        g.on_horizontal_drag_update(Ev(dx=80)); g.on_vertical_drag_update(Ev(dy=60))
        g.on_horizontal_drag_end(Ev()); self.assertIsNotNone(tv._drag)    # aún queda el eje vertical
        g.on_vertical_drag_end(Ev()); self.assertIsNone(tv._drag)
        self.assertEqual(self.s.tl.all_clips()[0].start, 80)

    def test_pickers_are_independent_and_unwritable_dirs_fall_back(self):
        from forgecut.ui import compat
        pg = flet_stub.Page()
        a, b = compat._get_picker(pg, "files"), compat._get_picker(pg, "dir")
        self.assertIsNot(a, b)
        fb = tempfile.mkdtemp()
        d, note = compat.writable_dir("/proc/forgecut_no/escribible", fb)
        self.assertEqual(d, fb); self.assertIn("No se puede escribir", note)
        ok = os.path.join(fb, "sub")
        self.assertEqual(compat.writable_dir(ok, fb), (ok, None))

    def test_android_public_dirs_and_fallback_chain(self):
        from forgecut.ui import compat
        self.assertEqual(compat.android_public_dirs("render"), [])          # fuera de Android no hay carpetas públicas
        fb, pub = tempfile.mkdtemp(), tempfile.mkdtemp()
        d, note = compat.writable_dir("/proc/x/no", fb, [pub + "/ForgeCut"])  # usuario → pública → privada
        self.assertEqual(d, pub + "/ForgeCut"); self.assertIn("se guardó en", note)
        d, note = compat.writable_dir("/proc/x/no", fb, ["/proc/y/no"])
        self.assertEqual(d, fb)
        os.environ["ANDROID_ROOT"] = "/system"
        try:
            self.assertTrue(compat.android_public_dirs("render")[0].endswith("Movies/ForgeCut"))
            self.assertTrue(compat.default_out_dir("render", fb).startswith(("/storage", fb)))
        finally:
            os.environ.pop("ANDROID_ROOT")

    def test_android_audio_player_never_crashes_without_plugin(self):
        from forgecut.media.audio_play import FletAudioPlayer
        pg = flet_stub.Page(); pg.services = []; pg.overlay = []
        pl = FletAudioPlayer(pg)
        pl.play("/x/a.wav")                                          # sin flet_audio instalado: solo guarda el error
        self.assertTrue(pl.error); pl.stop()
        import types
        m = types.ModuleType("flet_audio"); m.Audio = lambda **k: flet_stub.Control(**k); sys.modules["flet_audio"] = m
        try:
            pl.play("/x/a.wav"); self.assertEqual(len(pg.services), 1)   # el servicio se añade (suena)
            pl.stop(); self.assertEqual(len(pg.services), 0)             # y se quita (se detiene)
        finally:
            del sys.modules["flet_audio"]

    def test_deliver_render_uses_chosen_folder(self):
        from forgecut.ui.pages.deliver_page import DeliverPage
        dp = DeliverPage(self.s, flet_stub.Page())
        self.assertTrue(dp.render_dir.value.endswith("render"))
        self.assertTrue(any(type(x).__name__ == "TextField" and x is dp.render_dir for x in self._walk(dp.root)))

    def test_windows_build_has_utf8_env(self):
        y = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows", "build.yml"), encoding="utf-8").read()
        self.assertIn('PYTHONUTF8: "1"', y)


if __name__ == "__main__":
    unittest.main()
