import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from forgecut.core.models import Clip, Project, Timeline, MediaAsset, new_id
from forgecut.core import editing as ed
from forgecut.core.history import History
from forgecut.core.project_io import save_project, load_project


def mk(tl, track="V1", start=0, dur=100, src=0, **kw):
    return ed.add_clip(tl, track, Clip(id=new_id("c"), kind="video", track_id=track, start=start,
                                       duration=dur, source_in=src, asset_id="a", **kw))


class TestEditing(unittest.TestCase):
    def setUp(self):
        self.tl = Timeline.default(fps=30)

    def test_overwrite_splits_and_trims(self):
        a = mk(self.tl, start=0, dur=100)
        b = mk(self.tl, start=40, dur=20)            # cae dentro de a → a se parte en dos
        clips = sorted(self.tl.track("V1").clips, key=lambda c: c.start)
        self.assertEqual([(c.start, c.duration) for c in clips], [(0, 40), (40, 20), (60, 40)])
        self.assertEqual(clips[2].source_in, 60)      # el contenido no se desplaza
        self.assertIs(clips[1], b)

    def test_overwrite_removes_covered(self):
        mk(self.tl, start=10, dur=10)
        mk(self.tl, start=0, dur=100)
        self.assertEqual(len(self.tl.track("V1").clips), 1)

    def test_split_exact_frame_and_source(self):
        c = mk(self.tl, start=30, dur=90, src=10)
        r = ed.split_clip(self.tl, c.id, 75)
        self.assertEqual((c.start, c.duration), (30, 45))
        self.assertEqual((r.start, r.duration, r.source_in), (75, 45, 10 + 45))
        self.assertIsNone(ed.split_clip(self.tl, c.id, c.start))     # borde: no corta

    def test_speed_split(self):
        c = mk(self.tl, start=0, dur=100, speed=2.0)
        r = ed.split_clip(self.tl, c.id, 50)
        self.assertEqual(r.source_in, 100)            # 50 frames de timeline a 2x = 100 del medio

    def test_trim_and_ripple(self):
        a, b = mk(self.tl, start=0, dur=50), mk(self.tl, start=50, dur=50)
        ed.trim_end(self.tl, a.id, 30)
        self.assertEqual(a.duration, 30)
        ed.trim_start(self.tl, b.id, 60)
        self.assertEqual((b.start, b.duration, b.source_in), (60, 40, 10))
        ed.delete_clip(self.tl, a.id, ripple=True)
        self.assertEqual(b.start, 30)

    def test_trim_start_cannot_exceed_source(self):
        c = mk(self.tl, start=100, dur=50, src=5)
        ed.trim_start(self.tl, c.id, 0)
        self.assertGreaterEqual(c.source_in, 0)
        self.assertEqual(c.start, 95)

    def test_move_between_tracks(self):
        c = mk(self.tl, start=0, dur=30)
        ed.move_clip(self.tl, c.id, 200, "V2")
        self.assertEqual((c.track_id, c.start), ("V2", 200))
        with self.assertRaises(ValueError):
            ed.move_clip(self.tl, c.id, 0, "A1")

    def test_locked_track(self):
        self.tl.track("V1").locked = True
        with self.assertRaises(PermissionError):
            mk(self.tl)

    def test_snap(self):
        mk(self.tl, start=0, dur=100)
        pts = ed.snap_points(self.tl)
        self.assertEqual(ed.snap(97, pts, 5), 100)
        self.assertEqual(ed.snap(80, pts, 5), 80)

    def test_auto_cut_scenes(self):
        c = mk(self.tl, start=60, dur=300, src=0)            # 10 s de medio, empieza en t=2 s
        n = ed.auto_cut_scenes(self.tl, c.id, [1.0, 4.0, 4.0, 9.5, 12.0])   # el último cae fuera
        self.assertEqual(n, 3)
        starts = [x.start for x in self.tl.track("V1").clips]
        self.assertEqual(starts, [60, 90, 180, 345])         # = 60 + 30, 120, 285
        self.assertEqual(sum(x.duration for x in self.tl.track("V1").clips), 300)

    def test_set_props(self):
        c = mk(self.tl)
        ed.set_clip_props(self.tl, c.id, **{"transform.scale": 1.5, "volume": 0.5, "duration": 0})
        self.assertEqual((c.transform.scale, c.volume, c.duration), (1.5, 0.5, 1))

    def test_timecode_duration(self):
        mk(self.tl, start=0, dur=95)
        self.assertEqual(self.tl.duration, 95)
        self.assertEqual(self.tl.timecode(95), "00:00:03:05")


class TestHistoryIO(unittest.TestCase):
    def test_undo_redo(self):
        tl, h = Timeline.default(), History()
        h.commit(tl); mk(tl, dur=10)
        h.commit(tl); mk(tl, start=20, dur=10)
        tl2 = h.undo(tl)
        self.assertEqual(len(tl2.track("V1").clips), 1)
        tl3 = h.redo(tl2)
        self.assertEqual(len(tl3.track("V1").clips), 2)
        self.assertTrue(h.can_undo)

    def test_roundtrip(self):
        p = Project(name="x")
        p.add_asset(MediaAsset(id="a", path="/tmp/a.mp4", duration_s=3.5))
        mk(p.timeline, dur=33)
        ed.add_marker(p.timeline, 10, "m")
        with tempfile.TemporaryDirectory() as d:
            path = save_project(p, os.path.join(d, "t"))
            q = load_project(path)
        self.assertEqual(q.to_dict(), p.to_dict())
        self.assertIsInstance(q.timeline.track("V1").clips[0].transform.scale, float)


if __name__ == "__main__":
    unittest.main()
