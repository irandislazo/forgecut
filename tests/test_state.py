import asyncio, os, shutil, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(__file__))
from helpers import make_template
from forgecut.ui.state import AppState
from forgecut.media.ffmpeg_backend import FFmpegBackend


def ff(*a):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *a], check=True)


@unittest.skipUnless(FFmpegBackend().available(), "ffmpeg no disponible")
class TestState(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp(prefix="st_")
        ff("-f", "lavfi", "-i", "color=c=red:s=96x54:r=30:d=2", "-f", "lavfi", "-i", "color=c=blue:s=96x54:r=30:d=2",
           "-filter_complex", "concat=n=2:v=1:a=0", "-c:v", "libx264", "-pix_fmt", "yuv420p", os.path.join(cls.d, "sc.mp4"))
        ff("-f", "lavfi", "-i", "sine=frequency=220:duration=170", os.path.join(cls.d, "song.wav"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def setUp(self):
        self.s = AppState(data_dir=os.path.join(self.d, "data"))
        self.events = []
        self.s.subscribe(self.events.append)

    def test_import_add_split_undo(self):
        (a,) = self.s.import_media([os.path.join(self.d, "sc.mp4")])
        self.assertAlmostEqual(a.duration_s, 4.0, delta=0.2)
        clip = self.s.add_asset_to_timeline(a.id, at=0)
        self.assertEqual(self.s.selection, [clip.id])
        self.s.set_playhead(60)
        self.assertEqual(self.s.split_at_playhead(), 1)
        self.assertEqual(len(self.s.tl.track("V1").clips), 2)
        self.s.undo()
        self.assertEqual(len(self.s.tl.track("V1").clips), 1)
        self.s.redo()
        self.assertEqual(len(self.s.tl.track("V1").clips), 2)
        self.assertIn("timeline", self.events)

    def test_auto_cut_exact(self):
        (a,) = self.s.import_media([os.path.join(self.d, "sc.mp4")])
        clip = self.s.add_asset_to_timeline(a.id, at=100)
        n = asyncio.run(self.s.auto_cut_selected(0.3))
        self.assertEqual(n, 1)
        starts = sorted(c.start for c in self.s.tl.track("V1").clips)
        self.assertEqual(starts, [100, 160])                       # escena a los 2.0 s = 60 frames exactos

    def test_failed_edit_leaves_history_clean(self):
        (a,) = self.s.import_media([os.path.join(self.d, "sc.mp4")])
        self.s.add_asset_to_timeline(a.id, at=0)
        self.s.tl.track("V1").locked = True
        before = self.s.history.can_redo
        self.assertIsNone(self.s.do(lambda tl: tl.track("V1").clips.clear() or (_ for _ in ()).throw(PermissionError("bloqueada"))))
        self.assertIn("bloqueada", self.s.last_error)
        self.assertEqual(self.before_redo(), before)

    def before_redo(self):
        return self.s.history.can_redo

    def test_template_timeline_save_open_export(self):
        tpl = make_template("la_voz")
        # los clips ficticios de la plantilla no existen: solo se usan como referencias para planificar
        sp = os.path.join(self.d, "song.wav")
        plan = self.s.create_timeline(tpl, sp, seed=9)
        self.assertEqual(self.s.tl.duration, 170 * 30)
        self.assertGreaterEqual(plan.info["lipsyncs"], 3)
        path = self.s.save(os.path.join(self.d, "p1"))
        t = AppState(data_dir=os.path.join(self.d, "data2")); t.open(path)
        self.assertEqual(t.project.to_dict(), self.s.project.to_dict())
        self.assertEqual(len(t.audio_ranges(False)), plan.info["lipsyncs"])
        outs = self.s.export_audio(os.path.join(self.d, "aud"), "mp3", False)
        self.assertEqual(len(outs), plan.info["lipsyncs"])
        # solo la selección
        slot = self.s.audio_ranges(False)[1]
        self.s.select([slot.clip_id])
        self.assertEqual(len(self.s.export_audio(os.path.join(self.d, "aud2"), "wav", True)), 1)

    def test_incomplete_template_rolls_back(self):
        tpl = make_template("la_voz", girando=0)
        with self.assertRaises(ValueError):
            self.s.create_timeline(tpl, os.path.join(self.d, "song.wav"))
        self.assertFalse(self.s.history.can_undo)


if __name__ == "__main__":
    unittest.main()
