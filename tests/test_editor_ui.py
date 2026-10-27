import json, os, shutil, subprocess, sys, tempfile, time, unittest
sys.path.insert(0, os.path.dirname(__file__))
import flet_stub
if "flet" not in sys.modules:
    flet_stub.install()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from forgecut.core import editing as ed
from forgecut.core.models import Clip, new_id
from forgecut.media.ffmpeg_backend import FFmpegBackend
from forgecut.ui.state import AppState
from forgecut.ui.widgets.timeline_view import TimelineView, RULER_H, TRACK_H


class Ev:
    def __init__(self, x=0.0, y=0.0, dx=0.0, dy=0.0):
        self.local_x, self.local_y, self.delta_x, self.delta_y = x, y, dx, dy


@unittest.skipUnless(FFmpegBackend().available(), "ffmpeg no disponible")
class TestEditorUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp(prefix="eui_")
        be = FFmpegBackend()
        cls.vid, cls.wav = os.path.join(cls.d, "v.mp4"), os.path.join(cls.d, "a.wav")
        subprocess.run([be.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=96x54:r=30:d=4",
                        "-f", "lavfi", "-i", "sine=f=330:d=4", "-shortest", cls.vid], check=True)
        subprocess.run([be.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "sine=f=523:d=6", cls.wav], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def setUp(self):
        self.s = AppState(data_dir=tempfile.mkdtemp(dir=self.d))
        self.s.snap_on = False
        self.s.zoom = 1.0
        TimelineView.ASYNC_DROP = False
        self.tv = TimelineView(self.s)

    def clip_groups(self):
        return [c for c in self.tv.stack.controls if type(c).__name__ == "Stack"]

    def add(self, track, start, dur):
        k = "audio" if track.startswith("A") else "video"
        c = Clip(id=new_id("c"), kind=k, track_id=track, start=start, duration=dur, name=f"{track}-{start}")
        self.s.do(lambda tl: ed.add_clip(tl, track, c))
        return c

    # ── multiselección ─────────────────────────────────────────────
    def test_ctrl_click_toggles_selection(self):
        a, b = self.add("V1", 0, 50), self.add("V1", 100, 50)
        g_a, g_b = [g for g in self.clip_groups()][:2]
        g_a.controls[0].on_tap_down(Ev())
        self.assertEqual(self.s.selection, [a.id])
        g_b.controls[0].on_tap_down(Ev())                       # sin Ctrl → reemplaza
        self.assertEqual(self.s.selection, [b.id])
        self.s.note_ctrl()
        g_a.controls[0].on_tap_down(Ev())                       # con Ctrl → suma
        self.assertEqual(set(self.s.selection), {a.id, b.id})
        g_a.controls[0].on_tap_down(Ev())                       # Ctrl otra vez → quita
        self.assertEqual(self.s.selection, [b.id])

    def test_ctrl_window_expires(self):
        self.s.note_ctrl(); self.assertTrue(self.s.ctrl_held())
        self.s._ctrl_at -= 7; self.assertFalse(self.s.ctrl_held())

    def test_marquee_selects_touched_clips(self):
        a, b, c = self.add("V1", 0, 50), self.add("V2", 20, 50), self.add("A1", 300, 50)
        bg = [x for x in self.tv.stack.controls if type(x).__name__ == "GestureDetector" and x.on_pan_start and x.top == RULER_H][0]
        bg.on_pan_start(Ev(x=10, y=5)); bg.on_pan_update(Ev(dx=100, dy=TRACK_H))
        self.assertTrue(self.tv._marquee.visible)
        bg.on_pan_end(Ev())
        self.assertEqual(set(self.s.selection), {a.id, b.id})
        self.assertFalse(self.tv._marquee.visible)

    def test_clicking_empty_space_deselects(self):
        a = self.add("V1", 0, 50); self.s.select([a.id])
        bg = [x for x in self.tv.stack.controls if type(x).__name__ == "GestureDetector" and x.on_pan_start and x.top == RULER_H][0]
        bg.on_tap_down(Ev(x=500, y=10))
        self.assertEqual(self.s.selection, [])

    # ── arrastre de grupo con imán y guía ──────────────────────────
    def test_group_drag_moves_all_selected_together_and_is_one_undo_step(self):
        a, b = self.add("V1", 0, 40), self.add("V1", 100, 40)
        self.s.select([a.id, b.id])
        g_a = self.clip_groups()[0]
        g_a.controls[0].on_pan_start(Ev())
        g_a.controls[0].on_pan_update(Ev(dx=60))
        lefts = sorted(g.left for g in self.clip_groups())
        self.assertEqual(lefts, [60.0, 160.0])                  # se mueven EN VIVO (antes de soltar)
        g_a.controls[0].on_pan_end(Ev())
        self.assertEqual((a.start, b.start), (60, 160))
        self.s.undo()
        self.assertEqual((self.s.tl.track("V1").clips[0].start, self.s.tl.track("V1").clips[1].start), (0, 100))

    def test_drag_to_upper_track_and_snap_guide(self):
        self.s.snap_on = True
        base = self.add("V1", 200, 100)
        top = self.add("V2", 0, 50)
        self.s.select([top.id])
        g = [g for g in self.clip_groups() if g.top == RULER_H + 0 * TRACK_H + 3][0]       # fila 0 = V2
        g.controls[0].on_pan_start(Ev())
        g.controls[0].on_pan_update(Ev(dx=146, dy=TRACK_H))      # su final (196) cae a ~4 frames del inicio de `base`
        self.assertTrue(self.tv._guide.visible)
        self.assertEqual(self.tv._guide.left, 200.0)             # línea amarilla en el punto de imán
        g.controls[0].on_pan_end(Ev())
        t, c = ed.find_clip(self.s.tl, top.id)
        self.assertEqual((t.id, c.start, c.end), ("V1", 150, 200))   # pegado exacto a la esquina, bajado a V1 (sobrescribe)
        self.assertFalse(self.tv._guide.visible)

    def test_failed_drop_restores_positions(self):
        v, a = self.add("V1", 0, 40), self.add("A1", 0, 40)
        self.s.select([v.id, a.id])
        g = self.clip_groups()[0]
        g.controls[0].on_pan_start(Ev()); g.controls[0].on_pan_update(Ev(dx=30, dy=TRACK_H)); g.controls[0].on_pan_end(Ev())
        # vídeo+audio juntos: el desplazamiento vertical se ignora y se mueven los dos en horizontal
        self.assertEqual((v.start, v.track_id, a.start, a.track_id), (30, "V1", 30, "A1"))
        for grp in self.clip_groups():
            self.assertEqual(grp.left, 30.0)                     # la UI refleja el nuevo sitio

    # ── cuchilla con imán ──────────────────────────────────────────
    def test_blade_line_snaps_and_cuts(self):
        self.s.snap_on = True
        c = self.add("V1", 0, 200)
        self.add("V2", 100, 20)
        self.s.set_tool("razor")
        ov = [x for x in self.tv.stack.controls if type(x).__name__ == "GestureDetector" and x.on_hover and not x.on_pan_start and x.top == RULER_H][0]
        ov.on_hover(Ev(x=97, y=TRACK_H + 5))                     # cerca del inicio del clip de V2 (100) → imán
        self.assertEqual(self.tv._blade.left, 100.0)
        self.assertEqual(self.tv._blade.bgcolor, "#ffd60a")
        self.tv._last.clear()
        ov.on_hover(Ev(x=60, y=TRACK_H + 5))
        self.assertEqual(self.tv._blade.bgcolor, "#ffffff")
        ov.on_tap_down(Ev(x=60, y=TRACK_H + 5))
        self.assertEqual([(x.start, x.end) for x in self.s.tl.track("V1").clips], [(0, 60), (60, 200)])
        ov.on_tap_down(Ev(x=500, y=5))                           # fuera de cualquier clip → no hace nada
        self.assertEqual(len(self.s.tl.track("V1").clips), 2)

    # ── rendimiento: se reutilizan controles ───────────────────────
    def test_unchanged_clip_controls_are_reused_across_refreshes(self):
        a, b = self.add("V1", 0, 50), self.add("V1", 100, 50)
        before = {id(g) for g in self.clip_groups()}
        self.s.select([a.id])                                    # selección: no reconstruye nada
        self.assertEqual({id(g) for g in self.clip_groups()}, before)
        self.s.do(lambda tl: ed.set_clip_props(tl, a.id, name="otro"))   # cambia SOLO a → solo ese se reconstruye
        after = {id(g) for g in self.clip_groups()}
        self.assertEqual(len(before & after), 1)

    def test_selection_changes_outline_in_place(self):
        a = self.add("V1", 0, 50)
        g = self.clip_groups()[0]
        outline = g.controls[0].content
        self.s.select([a.id]); self.assertEqual(outline.bgcolor, "#ffffff")
        self.s.select([]);     self.assertIsNone(outline.bgcolor)

    # ── miniaturas y forma de onda ─────────────────────────────────
    def _wait_visual(self, aid, secs=15):
        end = time.time() + secs
        while time.time() < end:
            if self.s.asset_visual(aid):
                return self.s.asset_visual(aid)
            time.sleep(0.1)
        return None

    def test_import_generates_filmstrip_and_waveform_and_clips_show_them(self):
        va, aa = self.s.import_media([self.vid, self.wav])
        fv, fa = self._wait_visual(va.id), self._wait_visual(aa.id)
        self.assertEqual(fv[1], "film"); self.assertEqual(fa[1], "wave")
        self.assertTrue(os.path.exists(fv[0]) and os.path.exists(fa[0]))
        self.s.add_asset_to_timeline(va.id, at=0)
        self.s.add_asset_to_timeline(aa.id, at=0)
        imgs = [c for g in self.clip_groups() for c in self._walk(g) if type(c).__name__ == "Image"]
        self.assertEqual(len(imgs), 2)
        self.assertTrue(all(getattr(i, "src_base64", None) or getattr(i, "src", None) for i in imgs))   # se cargan como base64

    def _walk(self, ctl):
        yield ctl
        for c in (getattr(ctl, "controls", None) or []):
            yield from self._walk(c)
        c = getattr(ctl, "content", None)
        if isinstance(c, flet_stub.Control):
            yield from self._walk(c)

    def test_trimmed_clip_offsets_its_filmstrip(self):
        (va,) = self.s.import_media([self.vid]); self._wait_visual(va.id)
        c = self.s.add_asset_to_timeline(va.id, at=0)            # 4 s = 120 f; zoom 1 → 120 px
        self.s.do(lambda tl: ed.trim_start(tl, c.id, 30))        # recorta 30 frames de la cabeza
        img = [x for g in self.clip_groups() for x in self._walk(g) if type(x).__name__ == "Image"][0]
        self.assertEqual(img.left, -30.0)                        # la tira sigue alineada con el contenido
        self.assertEqual(img.width, 120.0)

    # ── soltar desde el Media Pool ─────────────────────────────────
    def test_drop_asset_places_at_frame_row_with_snap(self):
        self.s.snap_on = True
        (va, aa) = self.s.import_media([self.vid, self.wav])
        self.add("V1", 0, 100)
        clip = self.s.drop_asset(va.id, 97, 1)                   # fila 1 = V1; su inicio (97) se pega al final del clip (100)
        self.assertEqual((clip.track_id, clip.start), ("V1", 100))
        aclip = self.s.drop_asset(aa.id, 40, 0)                  # audio soltado sobre fila de vídeo → va a A1
        self.assertEqual((aclip.track_id, aclip.start), ("A1", 40))

    def test_drop_event_uses_dragged_asset_and_position(self):
        (va,) = self.s.import_media([self.vid])
        self.s.drag_asset = va.id
        tgt = self.tv.dropzone
        tgt.on_accept(Ev(x=50, y=RULER_H + TRACK_H + 3))                   # x=50 px a zoom 1 → frame 50, fila 1 = V1
        c = self.s.tl.track("V1").clips[0]
        self.assertEqual((c.start, self.s.drag_asset), (50, None))

    # ── exportar audio de los clips seleccionados ──────────────────
    def test_export_selected_audio_cuts(self):
        (aa,) = self.s.import_media([self.wav])
        c1 = self.s.add_asset_to_timeline(aa.id, at=0)           # 6 s = 180 f
        self.s.do(lambda tl: ed.split_clip(tl, c1.id, 60))       # 2 s | 4 s
        clips = sorted(self.s.tl.track("A1").clips, key=lambda x: x.start)
        self.s.select([clips[0].id, clips[1].id])
        out = os.path.join(self.d, "export")
        files = self.s.export_selected_audio(out, "wav")
        self.assertEqual(len(files), 2)
        durs = sorted(self.s.backend.probe(f)["duration_s"] for f in files)
        self.assertAlmostEqual(durs[0], 2.0, delta=0.05); self.assertAlmostEqual(durs[1], 4.0, delta=0.05)
        self.assertTrue(os.path.exists(os.path.join(out, "manifest.json")))
        with self.assertRaises(ValueError):
            self.s.select([]); self.s.export_selected_audio(out, "wav")

    def test_exported_audio_cut_uses_own_clip_source_even_if_other_track_overlaps(self):
        (aa,) = self.s.import_media([self.wav])
        other = os.path.join(self.d, "other.wav")
        subprocess.run([self.s.backend.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=200:d=6", other], check=True)
        (ob,) = self.s.import_media([other])
        self.s.add_asset_to_timeline(ob.id, at=0, track="A1")
        mine = self.s.add_asset_to_timeline(aa.id, at=0, track="A2")
        self.s.select([mine.id])
        (f,) = self.s.export_selected_audio(os.path.join(self.d, "export2"), "wav")
        raw = subprocess.run([self.s.backend.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", f, "-t", "1", "-ac", "1",
                              "-ar", "8000", "-f", "s16le", "-"], capture_output=True).stdout
        import array
        smp = array.array("h"); smp.frombytes(raw[: len(raw) // 2 * 2])
        crossings = sum(1 for i in range(1, len(smp)) if (smp[i - 1] < 0) != (smp[i] < 0))
        self.assertAlmostEqual(crossings / 2.0, 523, delta=15)       # 523 Hz (A2), NO los 200 Hz de A1


if __name__ == "__main__":
    unittest.main()
