import os, shutil, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from forgecut.media.ffmpeg_backend import FFmpegBackend
from forgecut.media.pure_probe import probe_pure
from forgecut.media.backend import NullBackend

BE = FFmpegBackend()


@unittest.skipUnless(BE.available() and BE.ffprobe, "ffmpeg/ffprobe no disponibles")
class TestPureProbe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp(prefix="pp_")
        cls.files = {}

        def gen(name, args):
            p = os.path.join(cls.d, name)
            subprocess.run([BE.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args, p], check=True)
            cls.files[name] = p
        v = ["-f", "lavfi", "-i", "testsrc=s=128x72:r=25:d=3.2"]
        a = ["-f", "lavfi", "-i", "sine=f=440:d=3.2"]
        gen("v.mp4", v + a + ["-shortest"]); gen("vnoa.mp4", v); gen("v.mov", v + a + ["-shortest"])
        gen("v.mkv", v + a + ["-shortest"])
        try:
            gen("v.webm", v + a + ["-shortest", "-c:v", "libvpx", "-c:a", "libvorbis"])
        except Exception:
            pass                                              # este ffmpeg no trae libvpx: se omite WebM
        gen("v.avi", v + a + ["-shortest"])
        gen("a.wav", a); gen("a.mp3", a); gen("a.flac", a); gen("a.ogg", a + ["-c:a", "libvorbis"]); gen("a.m4a", a)
        gen("i.png", ["-f", "lavfi", "-i", "color=c=red:s=90x50:d=1", "-frames:v", "1"])
        gen("i.jpg", ["-f", "lavfi", "-i", "color=c=blue:s=64x48:d=1", "-frames:v", "1"])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def cmp(self, name, tol=0.08, size=True, fps=True, audio=True):
        p = self.files[name]
        a, b = probe_pure(p), BE.probe(p)
        self.assertEqual(a["kind"], b["kind"], name)
        self.assertAlmostEqual(a["duration_s"], b["duration_s"], delta=tol, msg=f"{name}: {a['duration_s']} vs {b['duration_s']}")
        if size:
            self.assertEqual((a["width"], a["height"]), (b["width"], b["height"]), name)
        if fps:
            self.assertAlmostEqual(a["fps"], b["fps"], delta=0.6, msg=name)
        if audio:
            self.assertEqual(a["has_audio"], b["has_audio"], name)

    def test_mp4_with_and_without_audio(self):
        self.cmp("v.mp4"); self.cmp("vnoa.mp4")

    def test_mov(self): self.cmp("v.mov")
    def test_mkv(self): self.cmp("v.mkv", tol=0.1)

    def test_webm(self):
        if "v.webm" not in self.files:
            self.skipTest("sin libvpx")
        self.cmp("v.webm", tol=0.1)

    def test_avi(self): self.cmp("v.avi", tol=0.1)
    def test_wav(self): self.cmp("a.wav", 0.02)
    def test_flac(self): self.cmp("a.flac", 0.02)
    def test_mp3(self): self.cmp("a.mp3", 0.1)
    def test_ogg(self): self.cmp("a.ogg", 0.1)
    def test_m4a(self): self.cmp("a.m4a", 0.1)

    def test_images(self):
        for n in ("i.png", "i.jpg"):
            a = probe_pure(self.files[n])
            self.assertEqual(a["kind"], "image")
            self.assertEqual((a["width"], a["height"]), {"i.png": (90, 50), "i.jpg": (64, 48)}[n])

    def test_garbage_and_missing(self):
        bad = os.path.join(self.d, "bad.mp4"); 
        with open(bad, "wb") as fh:
            fh.write(b"\x00" * 50)
        self.assertEqual(probe_pure(bad)["duration_s"], 0.0)       # no lanza
        with self.assertRaises(FileNotFoundError):
            probe_pure(os.path.join(self.d, "nope.mp4"))

    def test_null_backend_can_import_without_ffmpeg(self):
        info = NullBackend().probe(self.files["v.mp4"])
        self.assertAlmostEqual(info["duration_s"], 3.2, delta=0.1)
        self.assertEqual((info["width"], info["height"]), (128, 72))

    def test_ffmpeg_without_ffprobe_still_gets_real_duration(self):
        be = FFmpegBackend(); be.ffprobe = None
        self.assertAlmostEqual(be.probe(self.files["v.mp4"])["duration_s"], 3.2, delta=0.1)


if __name__ == "__main__":
    unittest.main()
