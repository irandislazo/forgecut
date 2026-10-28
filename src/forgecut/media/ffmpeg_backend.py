"""Backend de escritorio basado en los binarios ffmpeg / ffprobe."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from typing import Callable, Dict, List, Optional

from .backend import BackendUnavailable

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
_NOWIN = {"creationflags": 0x08000000} if sys.platform.startswith("win") else {}   # sin ventana negra


def _android_lib_dirs(maps_text: Optional[str] = None) -> List[str]:
    """Carpeta de librerías nativas de la app en Android, deducida de /proc/self/maps (las .so cargadas viven en
    /data/app/…/lib/<abi>/). Es el único sitio desde el que Android permite ejecutar un binario (libffmpeg.so)."""
    import re
    try:
        txt = maps_text if maps_text is not None else open("/proc/self/maps").read()
    except OSError:
        return []
    return list(dict.fromkeys(re.findall(r"(/data/(?:app|data)/[^\s]*?/lib/[^/\s]+)/[^/\s]+\.so", txt)))


def _android_app_dirs(maps_text: Optional[str] = None) -> List[str]:
    """Carpetas `lib/<abi>` de la instalación, deducidas de CUALQUIER ruta /data/app/<x>/<pkg>/… que aparezca en
    /proc/self/maps (aunque las .so no estén extraídas y se carguen desde base.apk)."""
    import re
    try:
        txt = maps_text if maps_text is not None else open("/proc/self/maps").read()
    except OSError:
        return []
    out = []
    for root in dict.fromkeys(re.findall(r"(/data/app/[^/\s]+/[^/\s]+)/", txt)):
        out += [f"{root}/lib/{abi}" for abi in ("arm64", "arm64-v8a", "arm", "armeabi-v7a", "x86_64", "x86")]
    return out


def ffmpeg_diag() -> str:
    """Texto corto para saber por qué no se detecta ffmpeg en Android (se muestra en la barra superior)."""
    import re, zipfile
    try:
        maps = open("/proc/self/maps").read()
    except OSError:
        return ""
    libs = [d for d in dict.fromkeys(_android_lib_dirs(maps) + _android_app_dirs(maps)) if os.path.isdir(d)]
    found = _find("ffmpeg")
    if found:
        try:
            r = subprocess.run([found, "-version"], capture_output=True, timeout=8)
            return "ffmpeg encontrado pero " + ("no arranca: " + (r.stderr or b"").decode("utf-8", "ignore")[:60] if r.returncode else "OK")
        except Exception as ex:                                  # noqa: BLE001
            return f"ffmpeg en {found} no se puede ejecutar: {type(ex).__name__}: {str(ex)[:70]}"
    apk = next(iter(re.findall(r"(/data/app/[^\s]*?\.apk)", maps)), "")
    in_apk = False
    try:
        in_apk = bool(apk) and any("libffmpeg" in n for n in zipfile.ZipFile(apk).namelist())
    except Exception:
        pass
    if in_apk:
        return "el APK trae libffmpeg.so pero Android NO lo extrajo (falta useLegacyPackaging): usa el workflow nuevo"
    return f"libffmpeg.so no está en el APK (carpetas lib: {len(libs)})"


def _extra_dirs() -> List[str]:
    """Dónde buscar ffmpeg/ffprobe además del PATH: junto al ejecutable empaquetado (Windows/Linux/macOS), en una
    carpeta propia de ForgeCut y, en Android, en las librerías nativas de la app (el único sitio ejecutable)."""
    dirs: List[str] = []
    env = os.environ.get("FORGECUT_BIN_DIR")
    if env:
        dirs.append(env)
    for base in (sys.executable, sys.argv[0] if sys.argv else "", os.getcwd()):
        if base:
            d = base if os.path.isdir(base) else os.path.dirname(os.path.abspath(base))
            dirs += [d, os.path.join(d, "ffmpeg"), os.path.join(d, "bin")]
    dirs.append(os.path.join(os.path.expanduser("~"), ".forgecut", "bin"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(os.path.join(local, "ForgeCut", "ffmpeg"))
    if "ANDROID_ROOT" in os.environ or os.path.isdir("/data/app"):
        import glob
        dirs += _android_lib_dirs() + _android_app_dirs() + glob.glob("/data/app/*/*/lib/*") + glob.glob("/data/app/*/lib/*")
        nl = os.environ.get("ANDROID_NATIVE_LIB_DIR")
        if nl:
            dirs.append(nl)
    return [d for d in dict.fromkeys(dirs) if os.path.isdir(d)]


def _find(tool: str) -> Optional[str]:
    env = os.environ.get(f"FORGECUT_{tool.upper()}")
    if env and os.path.exists(env):
        return env
    p = shutil.which(tool)
    if p:
        return p
    for d in _extra_dirs():                    # ffmpeg.exe junto a ForgeCut.exe, lib<tool>.so en Android…
        for name in (tool, f"{tool}.exe", f"lib{tool}.so"):
            cand = os.path.join(d, name)
            if os.path.isfile(cand):
                return cand
    if tool == "ffmpeg":                       # binario embebido si está instalado imageio-ffmpeg
        try:
            import imageio_ffmpeg
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None
    return None


class FFmpegBackend:
    name = "ffmpeg"

    def __init__(self, ffmpeg: Optional[str] = None, ffprobe: Optional[str] = None):
        self.ffmpeg = ffmpeg or _find("ffmpeg")
        self.ffprobe = ffprobe or _find("ffprobe")
        self._major: Optional[int] = None

    # ── utilidades ─────────────────────────────────────────────────
    def available(self) -> bool:
        return bool(self.ffmpeg)

    def _need(self):
        if not self.ffmpeg:
            raise BackendUnavailable("ffmpeg no encontrado. Instálalo o define FORGECUT_FFMPEG.")

    def major_version(self) -> int:
        if self._major is None:
            try:
                out = subprocess.run([self.ffmpeg, "-version"], capture_output=True, text=True, **_NOWIN).stdout
                m = re.search(r"version\s+n?(\d+)", out)
                self._major = int(m.group(1)) if m else 99
            except Exception:
                self._major = 99
        return self._major

    def filter_script_args(self, path: str) -> List[str]:
        """ffmpeg ≥7 usa '-/filter_complex file'; las versiones anteriores '-filter_complex_script'."""
        return ["-/filter_complex", path] if self.major_version() >= 7 else ["-filter_complex_script", path]

    def run(self, args: List[str], check: bool = True) -> subprocess.CompletedProcess:
        self._need()
        r = subprocess.run([self.ffmpeg, "-hide_banner", "-y", *args], capture_output=True, text=True, **_NOWIN)
        if check and r.returncode != 0:
            raise RuntimeError("ffmpeg falló:\n" + (r.stderr or "")[-1500:])
        return r

    # ── probe ──────────────────────────────────────────────────────
    def probe(self, path: str) -> Dict:
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        ext = os.path.splitext(path)[1].lower()
        info = {"path": path, "name": os.path.basename(path), "kind": "video", "duration_s": 0.0,
                "width": 0, "height": 0, "fps": 0.0, "has_audio": False}
        if not self.ffprobe:                       # hay ffmpeg pero no ffprobe: metadatos en Python puro
            from .pure_probe import probe_pure
            return probe_pure(path)
        r = subprocess.run([self.ffprobe, "-v", "error", "-show_entries",
                            "format=duration:stream=codec_type,width,height,avg_frame_rate",
                            "-of", "json", path], capture_output=True, text=True, **_NOWIN)
        try:
            data = json.loads(r.stdout or "{}")
        except Exception:
            data = {}
        streams = data.get("streams", [])
        v = next((s for s in streams if s.get("codec_type") == "video"), None)
        info["has_audio"] = any(s.get("codec_type") == "audio" for s in streams)
        try:
            info["duration_s"] = float((data.get("format") or {}).get("duration") or 0.0)
        except Exception:
            pass
        if v:
            info["width"], info["height"] = int(v.get("width") or 0), int(v.get("height") or 0)
            try:
                n, d = (v.get("avg_frame_rate") or "0/1").split("/")
                info["fps"] = round(float(n) / float(d), 3) if float(d) else 0.0
            except Exception:
                pass
        if ext in AUDIO_EXT:                       # mp3/m4a/flac… con carátula incrustada traen un «vídeo» (attached_pic)
            info["kind"], info["has_audio"], info["width"], info["height"], info["fps"] = "audio", True, 0, 0, 0.0
            return info
        info["kind"] = ("image" if ext in IMAGE_EXT else "video") if v else "audio"
        return info

    # ── fotograma para la vista previa ─────────────────────────────
    def frame_jpeg(self, path: str, t_s: float, width: int = 640) -> bytes:
        self._need()
        r = subprocess.run([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, t_s):.3f}",
                            "-i", path, "-frames:v", "1", "-vf", f"scale={width}:-2",
                            "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "5", "-"],
                           capture_output=True, **_NOWIN)
        return r.stdout if r.returncode == 0 else b""

    # ── miniaturas, forma de onda y reproducción ───────────────────
    def waveform(self, path: str, out_png: str, height: int = 64) -> str:
        from .visuals import compute_peaks, waveform_png
        self._need()
        peaks = compute_peaks(self.ffmpeg, path)
        if not peaks:
            raise RuntimeError("El archivo no tiene audio que mostrar")
        return waveform_png(peaks, out_png, height)

    def filmstrip(self, path: str, out_png: str, duration_s: float, height: int = 40) -> str:
        from .visuals import filmstrip_png
        self._need()
        return filmstrip_png(self.ffmpeg, path, out_png, duration_s, height)

    def open_stream(self, path: str, start_s: float, fps: float, width: int):
        from .stream import FrameStream
        self._need()
        return FrameStream(self.ffmpeg, path, start_s, fps, width)

    # ── cambios de escena (frame exacto) ───────────────────────────
    def detect_scenes(self, path: str, threshold: float = 0.3) -> List[float]:
        """Devuelve los instantes (s) del PRIMER frame de cada nueva escena.

        Usa el filtro `select=gt(scene,thr)` + `showinfo`, cuyo pts_time es el del frame real.
        """
        self._need()
        vf = f"scale=320:-2,select='gt(scene,{threshold})',showinfo"
        r = subprocess.run([self.ffmpeg, "-hide_banner", "-i", path, "-an", "-vf", vf, "-f", "null", "-"],
                           capture_output=True, text=True, **_NOWIN)
        times = [float(x) for x in re.findall(r"pts_time:([0-9.]+)", r.stderr or "")]
        return sorted(set(round(t, 6) for t in times if t > 0.0))

    # ── audio ──────────────────────────────────────────────────────
    def export_audio(self, src: str, start_s: float, dur_s: float, out_path: str, fmt: str = "mp3") -> str:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        codec = ["-c:a", "libmp3lame", "-b:a", "192k"] if fmt == "mp3" else ["-c:a", "pcm_s16le"]
        self.run(["-ss", f"{max(0.0, start_s):.6f}", "-t", f"{dur_s:.6f}", "-i", src, "-vn",
                  "-ac", "2", "-ar", "44100", *codec, out_path])
        return out_path

    # ── render ─────────────────────────────────────────────────────
    def render(self, project, out_path: str, progress: Optional[Callable[[float], None]] = None,
               proxy: bool = False, short_side: Optional[int] = None, crf: Optional[int] = None) -> str:
        from .renderer import render_project
        return render_project(self, project, out_path, progress, proxy, short_side, crf)
