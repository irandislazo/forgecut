"""Modelo de datos de ForgeCut.

Regla de oro: TODO el tiempo del timeline se guarda en FRAMES enteros (como DaVinci).
Los segundos solo existen en los bordes (probe de medios, exportación de audio).
Este módulo no depende de la UI ni de ffmpeg: es puro Python y se puede probar solo.
"""
from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field, asdict, fields
from typing import Any, Dict, List, Optional

PROJECT_VERSION = 1


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def _build(cls, data: dict):
    """Crea un dataclass ignorando claves desconocidas (compatibilidad hacia delante)."""
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


# ── Medios ─────────────────────────────────────────────────────────
@dataclass
class MediaAsset:
    id: str
    path: str
    kind: str = "video"            # video | audio | image
    name: str = ""
    duration_s: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_audio: bool = False
    bin_id: Optional[str] = None   # carpeta del Media Pool (None = raíz)

    @classmethod
    def from_dict(cls, d: dict) -> "MediaAsset":
        return _build(cls, d)


# ── Inspector: transformación de un clip ───────────────────────────
@dataclass
class Transform:
    x: float = 0.0                 # desplazamiento en px del proyecto
    y: float = 0.0
    scale: float = 1.0             # zoom (1.0 = ajustado al encuadre)
    rotation: float = 0.0          # grados
    opacity: float = 1.0           # 0..1
    crop_l: float = 0.0            # recorte 0..1 (fracción del ancho/alto)
    crop_r: float = 0.0
    crop_t: float = 0.0
    crop_b: float = 0.0
    flip_h: bool = False
    flip_v: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "Transform":
        return _build(cls, d)

    def is_identity(self) -> bool:
        return self == Transform()


# ── Clip / Pista / Marcador ────────────────────────────────────────
@dataclass
class Clip:
    id: str
    kind: str                      # video | audio | image | placeholder
    track_id: str
    start: int                     # frame de inicio en el timeline
    duration: int                  # duración en frames (>= 1)
    asset_id: Optional[str] = None
    source_in: int = 0             # frame de entrada dentro del medio (a fps del timeline)
    speed: float = 1.0
    name: str = ""
    color: str = ""
    volume: float = 1.0
    mute: bool = False
    enabled: bool = True
    transform: Transform = field(default_factory=Transform)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def end(self) -> int:
        return self.start + self.duration

    @classmethod
    def from_dict(cls, d: dict) -> "Clip":
        d = dict(d)
        d["transform"] = Transform.from_dict(d.get("transform") or {})
        return _build(cls, d)


@dataclass
class Track:
    id: str
    name: str
    kind: str                      # video | audio
    clips: List[Clip] = field(default_factory=list)
    locked: bool = False
    muted: bool = False
    hidden: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "Track":
        d = dict(d)
        d["clips"] = [Clip.from_dict(c) for c in d.get("clips", [])]
        return _build(cls, d)


@dataclass
class Marker:
    frame: int
    name: str = ""
    color: str = "#e5484d"

    @classmethod
    def from_dict(cls, d: dict) -> "Marker":
        return _build(cls, d)


@dataclass
class Timeline:
    fps: int = 30
    width: int = 1920
    height: int = 1080
    tracks: List[Track] = field(default_factory=list)   # orden: de abajo a arriba por tipo
    markers: List[Marker] = field(default_factory=list)

    @classmethod
    def default(cls, fps: int = 30, width: int = 1920, height: int = 1080) -> "Timeline":
        return cls(fps=fps, width=width, height=height, tracks=[
            Track("V1", "V1", "video"), Track("V2", "V2", "video"),
            Track("A1", "A1", "audio"), Track("A2", "A2", "audio"),
        ])

    # utilidades de tiempo
    def to_frames(self, seconds: float) -> int:
        return int(round(seconds * self.fps))

    def to_seconds(self, frames: int) -> float:
        return frames / float(self.fps)

    def track(self, track_id: str) -> Track:
        for t in self.tracks:
            if t.id == track_id:
                return t
        raise KeyError(f"Pista inexistente: {track_id}")

    def all_clips(self) -> List[Clip]:
        return [c for t in self.tracks for c in t.clips]

    @property
    def duration(self) -> int:
        return max((c.end for c in self.all_clips()), default=0)

    def timecode(self, frame: int) -> str:
        fps = max(1, int(self.fps))
        s, f = divmod(max(0, int(frame)), fps)
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Timeline":
        return cls(fps=d.get("fps", 30), width=d.get("width", 1920), height=d.get("height", 1080),
                   tracks=[Track.from_dict(t) for t in d.get("tracks", [])],
                   markers=[Marker.from_dict(m) for m in d.get("markers", [])])

    def clone(self) -> "Timeline":
        return copy.deepcopy(self)


@dataclass
class MediaBin:
    """Carpeta del Media Pool (se pueden anidar)."""
    id: str
    name: str
    parent: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "MediaBin":
        return _build(cls, d)


@dataclass
class Project:
    name: str = "Proyecto sin título"
    version: int = PROJECT_VERSION
    assets: Dict[str, MediaAsset] = field(default_factory=dict)
    timeline: Timeline = field(default_factory=Timeline.default)
    bins: Dict[str, MediaBin] = field(default_factory=dict)

    def add_asset(self, asset: MediaAsset) -> MediaAsset:
        """Registra un medio (reutiliza el existente si la ruta ya está)."""
        for a in self.assets.values():
            if a.path == asset.path:
                return a
        self.assets[asset.id] = asset
        return asset

    def to_dict(self) -> dict:
        return {"name": self.name, "version": self.version,
                "assets": {k: asdict(v) for k, v in self.assets.items()},
                "timeline": self.timeline.to_dict(),
                "bins": {k: asdict(v) for k, v in self.bins.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        return cls(name=d.get("name", "Proyecto"), version=d.get("version", PROJECT_VERSION),
                   assets={k: MediaAsset.from_dict(v) for k, v in d.get("assets", {}).items()},
                   timeline=Timeline.from_dict(d.get("timeline", {})) if d.get("timeline") else Timeline.default(),
                   bins={k: MediaBin.from_dict(v) for k, v in d.get("bins", {}).items()})
