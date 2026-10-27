import json, os, random, subprocess, sys, tempfile, unittest, shutil
sys.path.insert(0, os.path.dirname(__file__))
from helpers import make_template
from forgecut.core.models import Project, Timeline, Clip, MediaAsset, new_id
from forgecut.core import editing as ed
from forgecut.media.ffmpeg_backend import FFmpegBackend
from forgecut.media.renderer import build_filtergraph, flatten_video
from forgecut.automation.templates import Template, PoolItem
from forgecut.automation.build_timeline import create_timeline_from_template
from forgecut.automation.audio_export import lipsync_ranges, selection_ranges, export_audio_ranges

FF = FFmpegBackend()


def ff(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@unittest.skipUnless(FF.available(), "ffmpeg no disponible")
class TestMedia(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp(prefix="fc_")
        P = lambda n: os.path.join(cls.d, n)
        ff("-f", "lavfi", "-i", "testsrc=size=160x90:rate=30:duration=3", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", P("clipA.mp4"))
        ff("-f", "lavfi", "-i", "color=c=red:s=160x90:r=30:d=2", "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=30:d=2",
           "-filter_complex", "concat=n=2:v=1:a=0", "-c:v", "libx264", "-pix_fmt", "yuv420p", P("scenes.mp4"))
        ff("-f", "lavfi", "-i", "sine=frequency=330:duration=60", P("song60.wav"))
        cls.P = staticmethod(P)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_probe(self):
        i = FF.probe(self.P("clipA.mp4"))
        self.assertEqual((i["kind"], i["width"], i["height"], i["has_audio"]), ("video", 160, 90, True))
        self.assertAlmostEqual(i["duration_s"], 3.0, delta=0.15)
        self.assertEqual(FF.probe(self.P("song60.wav"))["kind"], "audio")

    def test_frame_jpeg(self):
        b = FF.frame_jpeg(self.P("clipA.mp4"), 1.0, 80)
        self.assertTrue(b.startswith(b"\xff\xd8"))

    def test_scene_detect_exact(self):
        cuts = FF.detect_scenes(self.P("scenes.mp4"), 0.3)
        self.assertEqual(len(cuts), 1)
        self.assertAlmostEqual(cuts[0], 2.0, delta=1 / 30 + 1e-3)       # dentro de 1 frame
        # y el auto-corte cae en el frame 60 exacto
        tl = Timeline.default(fps=30)
        c = ed.add_clip(tl, "V1", Clip(id="x", kind="video", track_id="V1", start=0, duration=120, asset_id="a"))
        self.assertEqual(ed.auto_cut_scenes(tl, "x", cuts), 1)
        self.assertEqual([k.start for k in tl.track("V1").clips], [0, 60])

    def test_export_audio_segment(self):
        out = FF.export_audio(self.P("song60.wav"), 10.0, 4.0, self.P("seg.mp3"), "mp3")
        self.assertAlmostEqual(FF.probe(out)["duration_s"], 4.0, delta=0.15)

    def _project_with(self, *assets):
        p = Project(name="t")
        p.timeline = Timeline.default(fps=30, width=320, height=180)
        for a in assets: p.add_asset(a)
        return p

    def test_render_layers_transform_audio(self):
        info = FF.probe(self.P("clipA.mp4"))
        a = MediaAsset(id="a", path=self.P("clipA.mp4"), kind="video", duration_s=3, has_audio=True)
        s = MediaAsset(id="s", path=self.P("song60.wav"), kind="audio", duration_s=60)
        p = self._project_with(a, s)
        tl = p.timeline
        ed.add_clip(tl, "V1", Clip(id=new_id("c"), kind="video", track_id="V1", start=0, duration=60, asset_id="a"))
        ed.add_clip(tl, "V1", Clip(id=new_id("c"), kind="placeholder", track_id="V1", start=60, duration=30, name="LIPSYNC 01"))
        c3 = ed.add_clip(tl, "V2", Clip(id=new_id("c"), kind="video", track_id="V2", start=20, duration=40, asset_id="a", speed=1.0))
        c3.transform.scale, c3.transform.opacity, c3.transform.rotation, c3.transform.x = 0.5, 0.8, 15.0, 30.0
        c3.transform.crop_l, c3.transform.flip_h = 0.1, True
        ed.add_clip(tl, "A1", Clip(id=new_id("c"), kind="audio", track_id="A1", start=0, duration=90, asset_id="s", volume=0.7))
        out = self.P("r1.mp4")
        prog = []
        FF.render(p, out, progress=prog.append, proxy=True)
        i = FF.probe(out)
        self.assertAlmostEqual(i["duration_s"], 3.0, delta=0.2)          # 90 frames @30
        self.assertTrue(i["has_audio"])
        self.assertEqual(prog[-1], 1.0)

    def test_render_speed_and_gaps(self):
        a = MediaAsset(id="a", path=self.P("clipA.mp4"), kind="video", duration_s=3, has_audio=True)
        p = self._project_with(a)
        tl = p.timeline
        ed.add_clip(tl, "V1", Clip(id=new_id("c"), kind="video", track_id="V1", start=15, duration=30, asset_id="a", speed=2.0))
        out = self.P("r2.mp4")
        FF.render(p, out, proxy=True)
        self.assertAlmostEqual(FF.probe(out)["duration_s"], 1.5, delta=0.2)   # hueco inicial 0.5s + 1s

    def test_empty_timeline_error(self):
        with self.assertRaises(ValueError):
            build_filtergraph(self._project_with(), proxy=True)

    def test_end_to_end_template_to_audio_and_render(self):
        # plantilla La Voz con clips diminutos reales
        tpl = Template(name="e2e", style="la_voz", far_s=3.0, intro_s=30.0)
        small = []
        for k in range(3):
            fn = self.P(f"s{k}.mp4")
            ff("-f", "lavfi", "-i", f"testsrc2=size=96x54:rate=30:duration={2 + k}", "-c:v", "libx264", "-pix_fmt", "yuv420p", fn)
            small.append(PoolItem(fn, 2.0 + k))
        for key in ["far", "react2"] + [f"j{j}_{s}" for j in range(1, 5) for s in ("antes", "girando", "girado")]:
            tpl.add_files(key, small)
        # canción de 170 s
        song_path = self.P("song170.wav")
        ff("-f", "lavfi", "-i", "sine=frequency=220:duration=170", song_path)
        p = Project(name="e2e"); p.timeline = Timeline.default(fps=30, width=192, height=108)
        song = MediaAsset(id=new_id("m"), path=song_path, kind="audio", name="song170.wav", duration_s=170.0)
        plan = create_timeline_from_template(p, tpl, song, seed=5)
        tl = p.timeline
        self.assertEqual(tl.duration, 170 * 30)                           # exacto al frame
        v1 = sorted(tl.track("V1").clips, key=lambda c: c.start)
        self.assertEqual(v1[0].start, 0)
        for a, b in zip(v1, v1[1:]): self.assertEqual(a.end, b.start)      # sin huecos ni solapes
        slots = lipsync_ranges(p)
        self.assertEqual(len(slots), plan.info["lipsyncs"])
        self.assertGreaterEqual(len(slots), 3)
        self.assertEqual(len(tl.markers), len(slots))
        # exportar todos + uno seleccionado
        outs = export_audio_ranges(p, FF, slots[:2], os.path.join(self.d, "ls"), "mp3")
        self.assertEqual(len(outs), 2)
        for o in outs: self.assertAlmostEqual(FF.probe(o)["duration_s"], 10.0, delta=0.2)
        with open(os.path.join(self.d, "ls", "manifest.json")) as fh:
            man = json.load(fh)
        self.assertEqual(len(man), 2)
        sel = selection_ranges(p, [slots[0].clip_id])
        self.assertEqual(sel[0].start_f, slots[0].start_f)
        # editar el timeline generado (cuchilla) y renderizar un proxy completo
        mid = v1[len(v1) // 2]
        self.assertTrue(ed.split_clip(tl, mid.id, mid.start + mid.duration // 2))
        out = os.path.join(self.d, "e2e.mp4")
        FF.render(p, out, proxy=True)
        self.assertAlmostEqual(FF.probe(out)["duration_s"], 170.0, delta=0.5)


if __name__ == "__main__":
    unittest.main()
