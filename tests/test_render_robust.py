"""El render NO debe fallar por una edición rara: parámetros absurdos, audio fuera del medio, medios que faltan…"""
import os, random, re, shutil, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(__file__))
import helpers  # noqa: F401  (añade src/ al path)
from forgecut.core import editing as ed
from forgecut.core.models import Clip, MediaAsset, Project, Timeline, new_id
from forgecut.media import renderer as R
from forgecut.media.ffmpeg_backend import FFmpegBackend
from forgecut.automation.templates import Template, PoolItem
from forgecut.automation.build_timeline import create_timeline_from_template

FF = FFmpegBackend()


def ff(*a):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *a], check=True)


class TestPureHelpers(unittest.TestCase):
    def test_crop_pair_never_leaves_less_than_5_percent(self):
        for a, b in [(0.9, 0.9), (0.5, 0.6), (1.0, 1.0), (0.0, 0.0), (0.3, 0.2), (float("nan"), 0.4), (-1, 2)]:
            x, y = R._crop_pair(a, b)
            self.assertGreaterEqual(1 - x - y, 0.05 - 1e-9)
            self.assertGreaterEqual(min(x, y), 0.0)
        self.assertEqual(R._crop_pair(0.3, 0.2), (0.3, 0.2))                 # valores normales: intactos

    def test_f_sanitizes(self):
        self.assertEqual(R._f(float("nan"), 1.0), 1.0)
        self.assertEqual(R._f(float("inf"), 1.0), 1.0)
        self.assertEqual(R._f(None, 2.0), 2.0)
        self.assertEqual(R._f(99, 1.0, 0.0, 8.0), 8.0)

    def test_summary_drops_x264_noise_and_keeps_cause(self):
        noise = ("[libx264 @ 0x1] frame I:1  Avg QP:18.51 size: 77752\n[libx264 @ 0x1] kb/s:174.48\n"
                 "[aac @ 0x2] Qavg: 594.187\nframe=    0 fps=0.0 q=-1.0 Lsize=     561KiB time=N/A\n")
        cause = "[mp4 @ 0x3] Application provided invalid, non monotonically increasing dts to muxer in stream 1\nError muxing a packet\n"
        out = R.summarize_ffmpeg_error(noise + cause + "Conversion failed!\n")
        self.assertIn("non monotonically", out)
        self.assertNotIn("libx264", out)
        self.assertNotIn("Qavg", out)

    def test_crop_clamped_at_the_source_by_the_inspector(self):
        tl = Timeline.default()
        c = ed.add_clip(tl, "V1", Clip(id="c", kind="video", track_id="V1", start=0, duration=30))
        ed.set_clip_props(tl, "c", **{"transform.crop_l": 0.9})
        ed.set_clip_props(tl, "c", **{"transform.crop_r": 0.5})              # 0.9 + 0.5 = 1.4: antes ffmpeg fallaba
        self.assertLessEqual(c.transform.crop_l + c.transform.crop_r, 0.9 + 1e-9)
        ed.set_clip_props(tl, "c", **{"transform.crop_l": 0.2})
        ed.set_clip_props(tl, "c", **{"transform.crop_r": 0.6})
        self.assertAlmostEqual(c.transform.crop_r, 0.6)                      # dentro del límite: se respeta


@unittest.skipUnless(FF.available(), "ffmpeg no disponible")
class TestRobustRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp(prefix="rr_")
        cls.P = lambda self, n: os.path.join(self.d, n)
        ff("-f", "lavfi", "-i", "testsrc2=size=96x54:rate=30:duration=3", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", cls.P(cls, "a.mp4"))
        ff("-f", "lavfi", "-i", "sine=frequency=330:duration=30", cls.P(cls, "song.wav"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def project(self):
        p = Project(name="t"); p.timeline = Timeline.default(fps=30, width=192, height=108)
        p.add_asset(MediaAsset(id="a", path=self.P("a.mp4"), kind="video", duration_s=3.0, has_audio=True))
        p.add_asset(MediaAsset(id="s", path=self.P("song.wav"), kind="audio", duration_s=30.0))
        return p

    def add(self, p, track, start, dur, **kw):
        return ed.add_clip(p.timeline, track, Clip(id=new_id("c"), kind="audio" if track.startswith("A") else "video",
                                                   track_id=track, start=start, duration=dur,
                                                   asset_id="s" if track.startswith("A") else "a", **kw))

    def render(self, p, name="o.mp4"):
        out = self.P(name)
        return FF.render(p, out, proxy=True)

    def test_absurd_inspector_values_still_render(self):
        p = self.project()
        c = self.add(p, "V1", 0, 60)
        t = c.transform
        t.crop_l = t.crop_r = 0.9; t.crop_t = t.crop_b = 0.9                  # sumas > 1 (este era el fallo)
        t.scale, t.opacity, t.rotation = float("nan"), 5.0, float("inf")
        t.x, t.y = float("nan"), 1e9
        c2 = self.add(p, "V1", 60, 60); c2.speed = 0.0                       # velocidad 0 → antes: división por cero
        c3 = self.add(p, "V2", 10, 40); c3.transform.scale = 0.0
        out = self.render(p)
        self.assertAlmostEqual(FF.probe(out)["duration_s"], 4.0, delta=0.3)

    def test_audio_beyond_the_media_does_not_corrupt_timestamps(self):
        """Reproduce el fallo 'non monotonically increasing dts' (audio de clips fuera del medio + velocidades)."""
        p = self.project()
        for i, (src, sp) in enumerate([(0, 1.0), (200, 1.0), (50, 3.0), (95, 0.5), (400, 2.0)]):
            self.add(p, "V1", i * 40, 40, source_in=src, speed=sp)
        self.add(p, "A1", 0, 200)
        out = self.render(p)
        info = FF.probe(out)
        self.assertTrue(info["has_audio"])
        self.assertAlmostEqual(info["duration_s"], 200 / 30, delta=0.4)

    def test_slow_motion_audio_chain_regression(self):
        """Un clip con audio a 0.25× (cadena doble de atempo) corrompía los timestamps de la mezcla:
        'Application provided invalid, non monotonically increasing dts to muxer in stream 1' → 'Conversion failed!'."""
        for speed in (0.25, 0.3, 4.0):
            with self.subTest(speed=speed):
                p = self.project()
                self.add(p, "V1", 1740, 90, speed=speed)         # pasado el minuto 58: es cuando se corrompían los timestamps
                out = self.render(p, f"slow{speed}.mp4")
                info = FF.probe(out)
                self.assertTrue(info["has_audio"])
                self.assertAlmostEqual(info["duration_s"], 1830 / 30, delta=0.5)

    def test_missing_media_becomes_offline_not_an_error(self):
        p = self.project()
        p.assets["a"].path = self.P("no-existe.mp4")
        self.add(p, "V1", 0, 60); self.add(p, "A1", 0, 60)
        out = self.render(p)
        self.assertTrue(os.path.exists(out))
        self.assertTrue(any("no encontrado" in n.lower() for n in FF.last_render_notes), FF.last_render_notes)

    def test_safe_mode_retry_when_normal_mode_fails(self):
        p = self.project()
        self.add(p, "V1", 0, 60); self.add(p, "A1", 0, 60)
        real = R.build_filtergraph

        def flaky(project, proxy=False, short_side=None, level=0, notes=None):
            a, g, t = real(project, proxy, short_side, level, notes)
            return (a, g.replace("[vout]", "[vout];[nonexistent]nullsink") if level == 0 else g, t) if level == 0 else (a, g, t)
        R.build_filtergraph = flaky
        try:
            out = self.render(p, "retry.mp4")
        finally:
            R.build_filtergraph = real
        self.assertTrue(os.path.exists(out))
        self.assertTrue(any("sin los efectos" in n for n in FF.last_render_notes), FF.last_render_notes)
        self.assertFalse(os.path.exists(self.P("retry.part.mp4")))

    def test_total_failure_gives_readable_error_and_log_and_no_corrupt_file(self):
        p = self.project()
        self.add(p, "V1", 0, 60)
        real = R.build_filtergraph
        R.build_filtergraph = lambda project, proxy=False, short_side=None, level=0, notes=None: (
            real(project, proxy, short_side, level, notes)[0], "color=c=black:s=64x64[x]", 1.0)   # grafo sin [vout]: siempre falla
        out = self.P("fail.mp4")
        try:
            with self.assertRaises(RuntimeError) as cm:
                FF.render(p, out, proxy=True)
        finally:
            R.build_filtergraph = real
        msg = str(cm.exception)
        self.assertIn("Render falló", msg)
        self.assertNotIn("Avg QP", msg)
        m = re.search(r"Registro completo: (.+)$", msg)
        self.assertTrue(m and os.path.exists(m.group(1).strip()), msg)
        self.assertFalse(os.path.exists(out))
        self.assertFalse(os.path.exists(self.P("fail.part.mp4")))

    def test_random_edits_on_template_timelines_always_render(self):
        """Mini-fuzz: ediciones aleatorias (cortes, recortes, velocidad, efectos, superponer…) → nunca falla."""
        tpl = Template(name="f", style="la_voz", far_s=3.0, intro_s=8.0)
        items = [PoolItem(self.P("a.mp4"), 3.0, True)]
        for key in ["far", "react2"] + [f"j{j}_{s}" for j in range(1, 5) for s in ("antes", "girando", "girado")]:
            tpl.add_files(key, items)
        for seed in range(6):
            rnd = random.Random(seed)
            p = Project(name="f"); p.timeline = Timeline.default(fps=30, width=192, height=108)
            song = MediaAsset(id=new_id("m"), path=self.P("song.wav"), kind="audio", name="song.wav", duration_s=30.0)
            create_timeline_from_template(p, tpl, song, seed)
            tl = p.timeline
            for _ in range(rnd.randint(4, 12)):
                cl = [c for c in tl.all_clips() if c.kind in ("video", "placeholder")]
                c = rnd.choice(cl)
                op = rnd.choice(["crop", "speed", "trimS", "trimE", "split", "move", "tr", "mute", "dup", "del"])
                try:
                    if op == "crop":
                        t = c.transform; t.crop_l, t.crop_r, t.crop_t, t.crop_b = [rnd.choice([0, .5, .9]) for _ in range(4)]
                    elif op == "speed": c.speed = rnd.choice([0.25, 0.5, 2, 4])
                    elif op == "trimS": ed.trim_start(tl, c.id, c.start + rnd.randint(-15, c.duration))
                    elif op == "trimE": ed.trim_end(tl, c.id, c.end + rnd.randint(-c.duration, 50))
                    elif op == "split": ed.split_clip(tl, c.id, c.start + rnd.randint(1, max(1, c.duration - 1)))
                    elif op == "move": ed.move_clip(tl, c.id, max(0, c.start + rnd.randint(-60, 60)))
                    elif op == "tr":
                        t = c.transform; t.scale, t.rotation, t.opacity = rnd.choice([.1, 1, 3]), rnd.choice([0, 33, 180]), rnd.choice([0, .5, 1])
                    elif op == "mute": c.mute = not c.mute
                    elif op == "dup": ed.duplicate_clip(tl, c.id)
                    elif op == "del": ed.delete_clip(tl, c.id, ripple=True)
                except Exception:
                    pass
            if tl.duration <= 0:
                continue
            with self.subTest(seed=seed):
                out = FF.render(p, self.P(f"fz{seed}.mp4"), proxy=True)
                self.assertAlmostEqual(FF.probe(out)["duration_s"], tl.duration / 30, delta=0.6)


if __name__ == "__main__":
    unittest.main()
