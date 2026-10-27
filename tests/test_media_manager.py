import os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(__file__))
import flet_stub
if "flet" not in sys.modules:
    flet_stub.install()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from forgecut.core import editing as ed
from forgecut.core.models import MediaAsset, Clip, new_id
from forgecut.ui.state import AppState
from forgecut.ui.widgets.media_pool import MediaPool
from forgecut.ui.widgets.timeline_view import TimelineView


def walk(c):
    yield c
    for k in (getattr(c, "controls", None) or []):
        yield from walk(k)
    k = getattr(c, "content", None)
    if isinstance(k, flet_stub.Control):
        yield from walk(k)


class Ev:
    def __init__(self, x=0.0, y=0.0, value=None):
        self.local_x, self.local_y = x, y
        self.control = flet_stub.Control(value=value)


class Base(unittest.TestCase):
    def setUp(self):
        self.s = AppState(data_dir=tempfile.mkdtemp())
        self.s.ensure_visuals = lambda *_: None
        for i in range(1, 7):
            self.s.project.add_asset(MediaAsset(id=f"m{i}", path=f"/x/clip{i}.mp4", name=f"clip{i}.mp4", duration_s=2.0 + i,
                                                kind="audio" if i == 6 else "video"))
        self.pool = MediaPool(self.s, flet_stub.Page())

    def order(self):
        return [b.id for b in self.s.bins_in(self.pool.cur)] + [a.id for a in self.s.assets_in(self.pool.cur)]


class TestPoolLogic(Base):
    def test_click_ctrl_shift_selection(self):
        s, o = self.s, self.order()
        s.pool_click("m2", o)
        self.assertEqual(s.pool_sel, ["m2"])
        s.pool_click("m4", o, additive=True)
        self.assertEqual(s.pool_sel, ["m2", "m4"])
        s.pool_click("m2", o, additive=True)                      # Ctrl sobre uno ya marcado → lo quita
        self.assertEqual(s.pool_sel, ["m4"])
        s.pool_click("m1", o)                                     # ancla = m1
        s.pool_click("m3", o, rng=True)                           # Shift → rango m1..m3
        self.assertEqual(s.pool_sel, ["m1", "m2", "m3"])
        s.pool_click("m6", o, additive=True, rng=True)            # Ctrl+Shift → suma el rango al existente
        self.assertEqual(set(s.pool_sel), {"m1", "m2", "m3", "m4", "m5", "m6"})
        s.pool_clear(); self.assertEqual(s.pool_sel, [])

    def test_move_several_assets_and_bins_at_once(self):
        s = self.s
        a, b = s.create_bin("A"), s.create_bin("B")
        n = s.move_items(["m1", "m2", "m3", b.id], a.id)          # 3 medios + la carpeta B dentro de A
        self.assertEqual(n, 4)
        self.assertEqual({x.id for x in s.assets_in(a.id)}, {"m1", "m2", "m3"})
        self.assertEqual(s.project.bins[b.id].parent, a.id)
        self.assertEqual(s.move_items(["m1"], a.id), 0)           # ya estaba: no cuenta
        self.assertEqual(s.move_items(["m1"], "nope"), 0)

    def test_folder_cannot_enter_itself_or_descendant(self):
        s = self.s
        a = s.create_bin("A"); b = s.create_bin("B", a.id); c = s.create_bin("C", b.id)
        self.assertFalse(s.can_move_bin(a.id, a.id))
        self.assertFalse(s.can_move_bin(a.id, c.id))
        self.assertEqual(s.move_items([a.id], c.id), 0)
        self.assertIsNone(s.project.bins[a.id].parent)
        self.assertTrue(s.can_move_bin(c.id, None))
        self.assertEqual(s.move_items([c.id], None), 1)

    def test_delete_assets_blocked_when_used_then_forced_with_one_undo(self):
        s = self.s
        c1 = s.add_asset_to_timeline("m1", at=0)
        s.add_asset_to_timeline("m2", at=500)
        done, blocked = s.delete_assets(["m1", "m3"])
        self.assertEqual((done, blocked), (["m3"], ["m1"]))
        self.assertIn("m1", s.project.assets)
        done, blocked = s.delete_assets(["m1", "m2"], remove_clips=True)
        self.assertEqual((set(done), blocked), ({"m1", "m2"}, []))
        self.assertEqual(len(s.tl.all_clips()), 0)
        s.undo()                                                  # los clips vuelven (el medio ya no está en el pool)
        self.assertEqual(len(s.tl.all_clips()), 2)

    def test_rename_search_and_sort(self):
        s = self.s
        s.rename_asset("m1", "  Entrevista  ")
        self.assertEqual(s.project.assets["m1"].name, "Entrevista")
        f = s.create_bin("Escenas")
        s.move_items(["m2"], f.id)
        s.pool_query = "clip2"
        bins, assets = s.pool_items(None)                         # la búsqueda mira TODAS las carpetas
        self.assertEqual([a.id for a in assets], ["m2"])
        s.pool_query = "esc"
        self.assertEqual([b.id for b in s.pool_items(None)[0]], [f.id])
        s.pool_query = ""; s.pool_sort = "duration"
        self.assertEqual(s.pool_items(None)[1][0].id, "m6")       # más largo primero
        s.pool_sort = "kind"
        self.assertEqual(s.pool_items(None)[1][0].kind, "audio")

    def test_add_several_in_sequence_and_multi_drop(self):
        s = self.s
        clips = s.add_assets_to_timeline(["m1", "m2", "m3"], at=10)
        self.assertEqual([c.start for c in clips], [10, clips[0].end, clips[1].end])
        s2 = AppState(data_dir=tempfile.mkdtemp()); s2.ensure_visuals = lambda *_: None
        for i in (1, 2):
            s2.project.add_asset(MediaAsset(id=f"m{i}", path=f"/x/{i}.mp4", name=f"{i}.mp4", duration_s=2.0))
        out = s2.drop_assets(["m1", "m2"], 100, 1)
        self.assertEqual((out[0].start, out[1].start), (100, out[0].end))


class TestPoolUI(Base):
    def test_no_expand_inside_wrapping_row_anywhere(self):
        """Causa del panel en blanco: Expanded dentro de un Row(wrap=True) revienta el layout de Flutter."""
        a = self.s.create_bin("Carpeta")
        self.s.pool_click("m1", self.order())
        for view in ("list", "grid"):
            self.s.pool_view = view
            self.pool.refresh()
            for ctl in walk(self.pool.root):
                if type(ctl).__name__ == "Row" and getattr(ctl, "wrap", False):
                    for kid in ctl.controls:
                        self.assertFalse(getattr(kid, "expand", False), f"expand dentro de wrap ({view})")

    def test_panel_never_blank_even_if_a_row_fails(self):
        self.s.project.assets["m1"].name = None                   # dato corrupto: text(None) / .lower() fallan en esa fila
        self.pool.refresh()
        self.assertGreater(len(self.pool.list.controls), 0)       # el resto de filas sigue; no queda vacío
        self.assertTrue(self.pool.status.value)

    def test_row_structure_and_selection_bar(self):
        s = self.s
        s.create_bin("A")
        self.pool.refresh()
        self.assertEqual(len(self.pool.list.controls), 1 + 6)     # 1 carpeta + 6 medios
        self.assertFalse(self.pool.selbar.visible)
        s.pool_click("m1", self.order()); s.pool_click("m2", self.order(), additive=True)
        self.assertTrue(self.pool.selbar.visible)
        labels = [(c.args[0] if c.args else c.value) for c in walk(self.pool.selbar) if type(c).__name__ == "Text"]
        self.assertIn("2 seleccionado(s)", labels)

    def test_drag_selection_to_folder_moves_all(self):
        s = self.s
        b = s.create_bin("Destino")
        s.pool_click("m1", self.order()); s.pool_click("m2", self.order(), additive=True)
        self.pool._drag_start("m2")                               # arrastrar uno de la selección arrastra TODA la selección
        self.assertEqual(set(s.drag_assets), {"m1", "m2"})
        self.pool._drop_on(b.id)
        self.assertEqual({a.id for a in s.assets_in(b.id)}, {"m1", "m2"})
        self.assertEqual((s.drag_assets, s.drag_asset), ([], None))

    def test_drag_unselected_item_drags_only_it(self):
        s = self.s
        b = s.create_bin("D")
        s.pool_click("m1", self.order())
        self.pool._drag_start("m3")
        self.assertEqual(s.drag_assets, ["m3"])
        self.pool._drop_on(b.id)
        self.assertEqual([a.id for a in s.assets_in(b.id)], ["m3"])

    def test_drag_folder_into_folder_and_up_row_back_to_root(self):
        s = self.s
        a, b = s.create_bin("A"), s.create_bin("B")
        self.pool._drag_start(b.id); self.pool._drop_on(a.id)
        self.assertEqual(s.project.bins[b.id].parent, a.id)
        self.pool.open_folder(a.id)
        self.pool._drag_start(b.id); self.pool._drop_on(None)     # soltar en «subir» (padre = raíz)
        self.assertIsNone(s.project.bins[b.id].parent)

    def test_ctrl_click_and_select_mode_in_ui(self):
        s = self.s
        o = self.order()
        self.pool._click("m1", o); self.pool._click("m3", o)
        self.assertEqual(s.pool_sel, ["m3"])
        s.note_ctrl(); self.pool._click("m1", o)
        self.assertEqual(set(s.pool_sel), {"m1", "m3"})
        s.release_ctrl(); self.pool._toggle_multi()               # modo selección (táctil): cada toque suma
        self.pool._click("m5", o)
        self.assertEqual(set(s.pool_sel), {"m1", "m3", "m5"})
        s.note_shift(); self.pool._toggle_multi(); self.pool._click("m2", o)    # Shift desde el ancla m5 → m2..m5
        self.assertEqual(set(s.pool_sel), {"m2", "m3", "m4", "m5"})

    def test_plain_click_on_folder_opens_it(self):
        s = self.s
        b = s.create_bin("Abre")
        self.pool._click(b.id, self.order(), True)
        self.assertEqual(self.pool.cur, b.id)
        self.assertEqual(s.pool_sel, [])

    def test_search_hides_up_row_and_finds_in_subfolders(self):
        s = self.s
        b = s.create_bin("Deep"); s.move_items(["m4"], b.id)
        self.pool.open_folder(b.id)
        e = Ev(value="clip5"); self.pool._on_search(e)
        names = [(c.args[0] if c.args else c.value) for c in walk(self.pool.list) if type(c).__name__ == "Text"]
        self.assertTrue(any("clip5.mp4" == n for n in names))
        self.assertFalse(any(n == "Subir un nivel" for n in names))

    def test_view_and_sort_toggles(self):
        self.pool._toggle_view(); self.assertEqual(self.s.pool_view, "grid")
        kinds = [type(c).__name__ for c in self.pool.list.controls]
        self.assertIn("Row", kinds)                                # tarjetas dentro de un Row(wrap)
        self.pool._toggle_view(); self.assertEqual(self.s.pool_view, "list")
        self.pool._cycle_sort(); self.assertEqual(self.s.pool_sort, "duration")

    def test_multi_drop_on_timeline(self):
        s = self.s
        tv = TimelineView(s)
        s.pool_click("m1", self.order()); s.pool_click("m2", self.order(), additive=True)
        self.pool._drag_start("m1")
        from forgecut.ui.widgets.timeline_view import RULER_H, TRACK_H
        tgt = tv.dropzone
        tv.ASYNC_DROP = False
        e = Ev(x=40, y=RULER_H + TRACK_H + 3); e.data = None
        tgt.on_accept(e)
        clips = sorted(s.tl.all_clips(), key=lambda c: c.start)
        self.assertEqual(len(clips), 2)
        self.assertEqual(clips[1].start, clips[0].end)


if __name__ == "__main__":
    unittest.main()
