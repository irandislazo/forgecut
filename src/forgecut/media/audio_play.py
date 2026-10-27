"""Audio de la reproducción: mezcla (ffmpeg) de los clips con audio del timeline en un WAV por tramos y lo reproduce.

Windows: winsound (incluido en Python). Otros sistemas: ffplay / afplay / aplay si existen. Sin dependencias extra.
"""
from __future__ import annotations

import shutil
import subprocess
import sys

from ..core.models import Project
from .renderer import _atempo

_NOWIN = {"creationflags": 0x08000000} if sys.platform.startswith("win") else {}


def build_audio_mix(ffmpeg: str, project: Project, sf: int, ef: int, out_path: str) -> bool:
    """Mezcla el audio de los frames [sf, ef) (vídeos con audio + clips de audio; respeta mute, volumen, velocidad,
    pistas silenciadas). Devuelve False si no hay nada que sonar."""
    tl = project.timeline
    fps = float(tl.fps)
    args, parts, labels, n = [], [], [], 0
    for c in tl.all_clips():
        a = project.assets.get(c.asset_id) if c.asset_id else None
        if a is None or c.kind not in ("video", "audio") or not c.enabled or c.mute or not a.has_audio and a.kind != "audio":
            continue
        tr = tl.track(c.track_id)
        if tr.muted:
            continue
        o0, o1 = max(c.start, sf), min(c.end, ef)
        if o1 <= o0:
            continue
        speed = max(0.01, float(c.speed))
        src_off = max(0.0, (c.source_in + (o0 - c.start) * speed) / fps)
        src_len = (o1 - o0) / fps * speed
        ms = int(round((o0 - sf) / fps * 1000))
        chain = [f"[{n}:a]aresample=44100", "aformat=channel_layouts=stereo"]
        if abs(speed - 1.0) > 1e-3:
            chain.append(_atempo(speed))
        chain += [f"volume={float(c.volume):.4f}", f"adelay={ms}|{ms}"]
        parts.append(",".join(chain) + f"[a{n}]")
        labels.append(f"[a{n}]")
        args += ["-ss", f"{src_off:.6f}", "-t", f"{src_len:.6f}", "-i", a.path]
        n += 1
    if not n:
        return False
    total = (ef - sf) / fps
    mix = "".join(labels) + f"amix=inputs={n}:normalize=0:duration=longest,apad=whole_dur={total:.6f}[aout]"
    fc = ";".join(parts + [mix])
    r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args, "-filter_complex", fc,
                        "-map", "[aout]", "-t", f"{total:.6f}", "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", out_path],
                       capture_output=True, **_NOWIN)
    return r.returncode == 0


class AudioPlayer:
    def __init__(self, ffmpeg: str = ""):
        self._proc = None
        self._ffplay = shutil.which("ffplay")

    def play(self, wav: str) -> None:
        self.stop()
        if sys.platform.startswith("win"):
            import winsound
            winsound.PlaySound(wav, winsound.SND_FILENAME | winsound.SND_ASYNC)
            return
        cmd = ([self._ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", wav] if self._ffplay else
               ["afplay", wav] if shutil.which("afplay") else ["aplay", "-q", wav] if shutil.which("aplay") else None)
        if cmd:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self) -> None:
        if sys.platform.startswith("win"):
            try:
                import winsound
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None
