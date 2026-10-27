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
        self.assertTrue(grp().controls[0].on_pan_start)
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
