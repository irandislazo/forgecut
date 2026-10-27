import os, shutil, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(__file__))
import flet_stub
if "flet" not in sys.modules:
    flet_stub.install()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import flet as ft
from forgecut.core import editing as ed
from forgecut.core.models import Clip, new_id
from forgecut.core.project_io import save_project, load_project
from forgecut.media.ffmpeg_backend import FFmpegBackend
from forgecut.ui.state import AppState
from forgecut.ui.widgets.timeline_view import TimelineView


class Ev:
    def __init__(self, x=0.0, y=0.0, dx=0.0, dy=0.0, value=None):
        self.local_x, self.local_y, self.delta_x, self.delta_y = x, y, dx, dy
        self.control = flet_stub.Control(value=value)


def walk(c):
    yield c
    for k in (getattr(c, "controls", None) or []):
        yield from walk(k)
    k = getattr(c, "content", None)
    if isinstance(k, flet_stub.Control):
        yield from walk(k)


class _Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="v7_")
        self.s = AppState(data_dir=self.d)
        self.s.snap_on, self.s.zoom = False, 1.0
        TimelineView.ASYNC_DROP = False

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def add(self, track, start, dur):
        k = "audio" if track.startswith("A") else "video"
        c = Clip(id=new_id("c"), kind=k, track_id=track, start=start, duration=dur, name=f"{track}-{start}")
        self.s.do(lambda tl: ed.add_clip(tl, track, c))
        return c


class TestGroupSelection(_Base):
    def setUp(self):
        super().setUp()
        self.tv = TimelineView(self.s)
        self.a, self.b, self.c = self.add("V1", 0, 50), self.add("V1", 100, 50), self.add("A1", 0, 50)
        self.g = {x.id: gr for x, gr in zip((self.a, self.b, self.c), self._groups())}

    def _groups(self):
        gs = [c for c in self.tv.stack.controls if type(c).__name__ == "Stack"]
        by_start = sorted(gs, key=lambda g: (g.top, g.left))
        return by_start

    def det(self, clip):
        for g in [c for c in self.tv.stack.controls if type(c).__name__ == "Stack"]:
            if self.tv._refs[clip.id].group is g:
                return g.controls[0]

    def drag(self, clip, dx):
        d = self.det(clip)
        d.on_pan_start(Ev()); d.on_pan_update(Ev(dx=dx)); d.on_pan_end(Ev())

    def test_pressing_a_selected_member_keeps_the_group_and_drag_moves_all(self):
        self.s.select_rect(0, 400, 0, 3)                       # marquesina: todo
        self.assertEqual(set(self.s.selection), {self.a.id, self.b.id, self.c.id})
        self.det(self.a).on_tap_down(Ev())                      # pulsar un miembro NO colapsa (antes sí)
        self.assertEqual(len(self.s.selection), 3)
        self.drag(self.a, 30)
        self.assertEqual([c.start for c in (self.a, self.b, self.c)], [30, 130, 30])

    def test_click_without_drag_collapses_to_that_clip(self):
        self.s.select([self.a.id, self.b.id])
        d = self.det(self.a)
        d.on_tap_down(Ev()); self.assertEqual(len(self.s.selection), 2)
        d.on_tap(Ev())                                           # soltar sin arrastrar → queda solo ese
        self.assertEqual(self.s.selection, [self.a.id])

    def test_ctrl_select_then_release_ctrl_then_drag_group(self):
        self.s.note_ctrl()
        self.det(self.a).on_tap_down(Ev()); self.det(self.b).on_tap_down(Ev())
        self.assertEqual(set(self.s.selection), {self.a.id, self.b.id})
        self.s.release_ctrl()                                    # el usuario suelta Ctrl y arrastra
        self.det(self.b).on_tap_down(Ev())
        self.assertEqual(set(self.s.selection), {self.a.id, self.b.id})
        self.drag(self.b, -20)
        self.assertEqual((self.a.start, self.b.start), (0, 100))  # a ya está en 0: el grupo entero se frena
        self.drag(self.b, 25)
        self.assertEqual((self.a.start, self.b.start), (25, 125))

    def test_multi_mode_button_toggles_and_does_not_collapse(self):
        self.s.multi_mode = True
        d = self.det(self.a)
        d.on_tap_down(Ev()); d.on_tap(Ev())
        self.det(self.b).on_tap_down(Ev()); self.det(self.b).on_tap(Ev())
        self.assertEqual(set(self.s.selection), {self.a.id, self.b.id})
        self.det(self.b).on_tap_down(Ev())                       # alterna: quita b
        self.assertEqual(self.s.selection, [self.a.id])

    def test_plain_click_on_unselected_clip_selects_only_it(self):
        self.s.select([self.a.id, self.b.id])
        self.det(self.c).on_tap_down(Ev())
        self.assertEqual(self.s.selection, [self.c.id])


class TestPlayheadMagnet(_Base):
    def test_snap_playhead_and_edit_points(self):
        self.add("V1", 100, 50); self.add("V1", 300, 50)
        self.s.snap_on, self.s.zoom = True, 1.0                  # umbral = 8 px = 8 frames
        self.assertEqual(self.s.snap_playhead(104), (100, 100))
        self.assertEqual(self.s.snap_playhead(120), (120, None))
        self.s.snap_on = False
        self.assertEqual(self.s.snap_playhead(104), (104, None))
        self.s.set_playhead(0); self.s.goto_edit(1); self.assertEqual(self.s.playhead, 100)
        self.s.goto_edit(1); self.assertEqual(self.s.playhead, 150)
        self.s.goto_edit(-1); self.assertEqual(self.s.playhead, 100)
        self.s.goto_edit(-1); self.s.goto_edit(-1); self.assertEqual(self.s.playhead, 0)
        self.s.step_frames(10); self.s.step_frames(-1); self.assertEqual(self.s.playhead, 9)

    def test_ruler_scrub_snaps_to_clip_edge(self):
        self.add("V1", 100, 50)
        tv = TimelineView(self.s)
        self.s.snap_on = True
        ruler = [c for c in tv.stack.controls if type(c).__name__ == "GestureDetector" and c.top == 0][0]
        ruler.on_tap_down(Ev(x=103))
        self.assertEqual(self.s.playhead, 100)


class TestBins(_Base):
    def test_create_move_delete_roundtrip(self):
        a = self.s.project.add_asset(__import__("forgecut.core.models", fromlist=["MediaAsset"]).MediaAsset(
            id="m1", path="/x/a.mp4", name="a.mp4"))
        b = self.s.create_bin("Reacciones")
        sub = self.s.create_bin("Jueces", b.id)
        self.assertEqual([x.name for x in self.s.bin_path(sub.id)], ["Reacciones", "Jueces"])
        self.assertEqual(self.s.move_assets(["m1"], sub.id), 1)
        self.assertEqual([x.id for x in self.s.assets_in(sub.id)], ["m1"])
        self.assertEqual(self.s.assets_in(None), [])
        path = save_project(self.s.project, os.path.join(self.d, "p"))
        q = load_project(path)
        self.assertEqual(q.assets["m1"].bin_id, sub.id)
        self.assertEqual(q.bins[sub.id].parent, b.id)
        self.s.delete_bin(sub.id)                                # el medio sube a la carpeta padre
        self.assertEqual(self.s.project.assets["m1"].bin_id, b.id)
        self.s.delete_bin(b.id)
        self.assertIsNone(self.s.project.assets["m1"].bin_id)
        self.assertEqual(self.s.move_assets(["m1"], "no-existe"), 0)

    def test_old_projects_without_bins_still_load(self):
        from forgecut.core.models import Project
        d = Project(name="x").to_dict(); d.pop("bins")
        self.assertEqual(Project.from_dict(d).bins, {})

    @unittest.skipUnless(FFmpegBackend().available(), "ffmpeg no disponible")
    def test_import_goes_into_open_folder(self):
        be = FFmpegBackend(); v = os.path.join(self.d, "v.mp4")
        subprocess.run([be.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=64x36:r=30:d=1", v], check=True)
        self.s.ensure_visuals = lambda *_: None                   # sin hilos de miniaturas en esta prueba
        b = self.s.create_bin("Clips")
        (a,) = self.s.import_media([v], b.id)
        self.assertEqual(a.bin_id, b.id)
        (a2,) = self.s.import_media([v], None)                   # mismo medio ya importado: no se mueve
        self.assertEqual(a2.bin_id, b.id)

    def test_media_pool_ui_folders_drag_and_move(self):
        from forgecut.ui.widgets.media_pool import MediaPool
        from forgecut.core.models import MediaAsset
        self.s.project.add_asset(MediaAsset(id="m1", path="/x/a.mp4", name="a.mp4"))
        pool = MediaPool(self.s, flet_stub.Page())
        b = self.s.create_bin("Carpeta A")
        kinds = [type(c).__name__ for c in pool.list.controls]
        self.assertIn("DragTarget", kinds)                       # la carpeta acepta soltar medios
        self.s.drag_asset = "m1"; pool._drop_on(b.id)
        self.assertEqual(self.s.project.assets["m1"].bin_id, b.id)
        pool.open_folder(b.id)
        self.assertIn("Carpeta A", pool.crumb.value)
        self.assertEqual(len(pool.list.controls), 2)             # fila «Subir» + el medio
        pool.delete_folder(b.id)
        self.assertIsNone(pool.cur)
        self.assertIsNone(self.s.project.assets["m1"].bin_id)


class TestInspectorNudge(_Base):
    def test_plus_minus_reset_and_slider(self):
        from forgecut.ui.widgets.inspector import Inspector
        c = self.add("V1", 0, 60)
        insp = Inspector(self.s)
        self.s.select([c.id])
        plus = [w for w in walk(insp.col) if type(w).__name__ == "IconButton" and getattr(w, "tooltip", "") == "+ 10"]
        minus = [w for w in walk(insp.col) if type(w).__name__ == "IconButton" and getattr(w, "tooltip", "") == "− 10"]
        self.assertEqual(len(plus), 2)                           # X e Y
        plus[0].on_click(None)
        self.assertEqual(c.transform.x, 10)
        insp.refresh()
        plus = [w for w in walk(insp.col) if getattr(w, "tooltip", "") == "+ 10"]
        plus[1].on_click(None)
        self.assertEqual(c.transform.y, 10)
        insp.refresh()
        minus = [w for w in walk(insp.col) if getattr(w, "tooltip", "") == "− 10"]
        minus[0].on_click(None)
        self.assertEqual(c.transform.x, 0)
        sl = [w for w in walk(insp.col) if type(w).__name__ == "Slider" and getattr(w, "max", 0) == 4.0][0]
        sl.on_change_end(Ev(value=1.5))
        self.assertAlmostEqual(c.transform.scale, 1.5)
        insp.refresh()
        reset = [w for w in walk(insp.col) if getattr(w, "tooltip", "") == "Restablecer"][0]   # primero: Zoom
        reset.on_click(None)
        self.assertAlmostEqual(c.transform.scale, 1.0)
        self.s.undo()                                            # deshacer reemplaza el timeline: leer el clip actual
        self.assertAlmostEqual(self.s.tl.all_clips()[0].transform.scale, 1.5)


class TestCompactLayout(_Base):
    def test_compact_one_panel_at_a_time_and_back_to_desktop(self):
        from forgecut.ui.workspace import Workspace
        ws = Workspace(self.s, flet_stub.Page())
        ws.set_page("edit")
        self.assertFalse(ws.compact)
        self.assertTrue(ws.pool_box.visible and ws.inspector_box.visible and ws.preview_box.visible)
        ws.apply_width(400)
        self.assertTrue(ws.compact and ws.switch_row.visible)
        self.assertEqual((ws.preview_box.visible, ws.pool_box.visible, ws.inspector_box.visible), (True, False, False))
        self.assertEqual(ws.preview.w, 376)                      # el visor se reduce al ancho del móvil
        ws._pick_panel("inspector")
        self.assertEqual((ws.preview_box.visible, ws.pool_box.visible, ws.inspector_box.visible), (False, False, True))
        self.assertTrue(ws.inspector_box.expand and ws.inspector_box.width is None)
        ws.set_page("media")                                     # Media: abre el Media Pool y oculta el timeline
        self.assertTrue(ws.pool_box.visible and not ws.bottom.visible)
        ws.apply_width(1400)
        self.assertFalse(ws.compact or ws.switch_row.visible)
        self.assertEqual((ws.pool_box.width, ws.inspector_box.width, ws.preview.w), (520, 320, 560))


if __name__ == "__main__":
    unittest.main()
