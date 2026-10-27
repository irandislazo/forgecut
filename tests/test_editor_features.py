import os, shutil, struct, subprocess, sys, tempfile, time, unittest, zlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from forgecut.core import editing as ed
from forgecut.core.models import Clip, Timeline, new_id
from forgecut.media.ffmpeg_backend import FFmpegBackend
from forgecut.media import visuals


def mk(tl, track, start, dur, name="", kind=None):
    k = kind or ("audio" if track.startswith("A") else "video")
    return ed.add_clip(tl, track, Clip(id=new_id("c"), kind=k, track_id=track, start=start, duration=dur, name=name))


class TestGroupEditing(unittest.TestCase):
    def setUp(self):
        self.tl = Timeline.default()

    def test_row_order(self):
        self.assertEqual([t.id for t in ed.row_order(self.tl)], ["V2", "V1", "A1", "A2"])

    def test_group_move_keeps_relative_offsets(self):
        a, b = mk(self.tl, "V1", 10, 20), mk(self.tl, "V1", 50, 10)
        n = ed.move_clips(self.tl, [a.id, b.id], 100)
        self.assertEqual(n, 2)
        self.assertEqual((a.start, b.start), (110, 150))

    def test_group_move_overwrites_what_is_underneath_but_not_itself(self):
        a, b = mk(self.tl, "V1", 0, 30), mk(self.tl, "V1", 30, 30)
        under = mk(self.tl, "V1", 100, 60)
        ed.move_clips(self.tl, [a.id, b.id], 90)            # el grupo cae sobre `under`
        self.assertEqual((a.start, b.start), (90, 120))
        starts = sorted((c.start, c.end) for c in self.tl.track("V1").clips)
        self.assertEqual(starts, [(90, 120), (120, 150), (150, 160)])   # under recortado, el grupo intacto

    def test_cannot_leave_left_edge(self):
        a = mk(self.tl, "V1", 20, 10)
        ed.move_clips(self.tl, [a.id], -500)
        self.assertEqual(a.start, 0)

    def test_row_move_up_and_atomic_failure(self):
        a = mk(self.tl, "V1", 0, 10)
        au = mk(self.tl, "A1", 0, 10)
        ed.move_clips(self.tl, [a.id], 0, row_delta=-1)       # V1 → V2 (una fila arriba)
        self.assertEqual(a.track_id, "V2")
        # vídeo + audio juntos una fila abajo: el audio no puede ir a vídeo → no se mueve NADA
        with self.assertRaises(ValueError):
            ed.move_clips(self.tl, [a.id, au.id], 5, row_delta=1) if False else ed.move_clips(self.tl, [au.id, a.id], 5, row_delta=-1)
        self.assertEqual((a.start, a.track_id, au.start), (0, "V2", 0))

    def test_locked_track_blocks_whole_group(self):
        a, b = mk(self.tl, "V1", 0, 10), mk(self.tl, "V2", 0, 10)
        self.tl.track("V2").locked = True
        with self.assertRaises(PermissionError):
            ed.move_clips(self.tl, [a.id, b.id], 50)
        self.assertEqual(a.start, 0)

    def test_group_snap_to_edge_and_report_point(self):
        mk(self.tl, "V1", 200, 50)
        a = mk(self.tl, "V2", 0, 30)
        d, p = ed.group_snap(self.tl, [a.id], 168, 5)          # a.end → 198 ≈ 200
        self.assertEqual((d, p), (170, 200))
        d, p = ed.group_snap(self.tl, [a.id], 120, 5)          # lejos de todo
        self.assertEqual((d, p), (120, None))

    def test_group_snap_ignores_moving_clips_and_left_edge(self):
        a, b = mk(self.tl, "V1", 40, 10), mk(self.tl, "V2", 60, 10)
        d, p = ed.group_snap(self.tl, [a.id, b.id], -39, 5)    # no hay otro punto salvo 0 → pega a 0
        self.assertEqual(d, -40)
        d, p = ed.group_snap(self.tl, [a.id], -100, 5)
        self.assertEqual(d, -40)                               # nunca por la izquierda de 0

    def test_marquee(self):
        a, b, c = mk(self.tl, "V2", 0, 50), mk(self.tl, "V1", 20, 50), mk(self.tl, "A1", 200, 50)
        got = set(ed.clips_in_rect(self.tl, 10, 60, 0, 1))
        self.assertEqual(got, {a.id, b.id})
        self.assertEqual(set(ed.clips_in_rect(self.tl, 190, 260, 0, 3)), {c.id})

    def test_blade_snaps_to_edges_playhead_and_markers(self):
        mk(self.tl, "V1", 0, 100)
        mk(self.tl, "V2", 100, 40)
        self.assertEqual(ed.blade_frame(self.tl, 97, 5), (100, 100))
        self.assertEqual(ed.blade_frame(self.tl, 60, 5, playhead=62), (62, 62))
        self.assertEqual(ed.blade_frame(self.tl, 60, 5), (60, None))
        self.assertEqual(ed.blade_frame(self.tl, 97, 5, snap_on=False), (97, None))
        self.assertEqual(ed.clip_at_row(self.tl, 1, 50).track_id, "V1")
        self.assertIsNone(ed.clip_at_row(self.tl, 0, 50))


@unittest.skipUnless(FFmpegBackend().available(), "ffmpeg no disponible")
class TestVisualsAndStream(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp(prefix="vis_")
        cls.be = FFmpegBackend()
        cls.vid = os.path.join(cls.d, "v.mp4")
        cls.wav = os.path.join(cls.d, "a.wav")
        subprocess.run([cls.be.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc=s=160x90:r=30:d=6", "-f", "lavfi", "-i", "sine=f=440:d=6", "-shortest", cls.vid], check=True)
        # 2 s silencio + 2 s tono + 2 s silencio
        subprocess.run([cls.be.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "aevalsrc='if(between(t,2,4),0.8*sin(2*PI*440*t),0)':d=6:s=8000", cls.wav], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_peaks_show_silence_tone_silence(self):
        p = visuals.compute_peaks(self.be.ffmpeg, self.wav, buckets=60)
        self.assertEqual(len(p), 60)
        self.assertLess(max(p[:15]), 0.05)            # 0–1.5 s: silencio
        self.assertGreater(min(p[22:38]), 0.5)        # 2.2–3.8 s: tono
        self.assertLess(max(p[46:]), 0.05)

    def test_waveform_png_is_valid(self):
        out = self.be.waveform(self.wav, os.path.join(self.d, "w.png"))
        with open(out, "rb") as fh:
            data = fh.read()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        w, h = struct.unpack(">II", data[16:24])
        self.assertEqual((w, h), (2400, 64))

    def test_filmstrip_has_n_thumbnails(self):
        out = self.be.filmstrip(self.vid, os.path.join(self.d, "f.png"), 6.0, 40)
        with open(out, "rb") as fh:
            data = fh.read()
        w, h = struct.unpack(">II", data[16:24])
        self.assertEqual(h, 40)
        n = visuals.thumbs_count(6.0)
        self.assertEqual(n, 8)
        self.assertEqual(w, n * 72)                    # 160x90 → 72x40 por miniatura (ancho par)

    def test_stream_delivers_frames_in_order_much_faster_than_per_frame_ffmpeg(self):
        st = self.be.open_stream(self.vid, 1.0, 30, 160)
        try:
            t0 = time.perf_counter()
            got = [st.get(i, wait_s=2.0) for i in range(60)]
            dt = time.perf_counter() - t0
        finally:
            st.close()
        self.assertTrue(all(g and g[:2] == b"\xff\xd8" for g in got))
        self.assertEqual(len({g for g in got}) > 30, True)          # frames distintos (testsrc cambia)
        self.assertLess(dt, 2.5)                                    # 60 frames (2 s de vídeo) en < 2.5 s

    def test_stream_seek_backwards_and_eof(self):
        st = self.be.open_stream(self.vid, 4.0, 30, 160)
        try:
            self.assertIsNotNone(st.get(30, wait_s=2.0))
            self.assertIsNone(st.get(10, wait_s=0.2))               # ya descartado: la UI debe reabrir el stream
            self.assertGreater(st.earliest(), 10)
            late = st.get(500, wait_s=1.0)                          # más allá del final → último disponible
            self.assertTrue(late is None or late[:2] == b"\xff\xd8")
        finally:
            st.close()


if __name__ == "__main__":
    unittest.main()
