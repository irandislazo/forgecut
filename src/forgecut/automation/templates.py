"""Plantillas: conjuntos de clips del PC que alimentan el timeline automático.

Estilos:
  la_voz      : 4 jueces × (antes | girando | girado) + reacciones 2ª mitad + planos lejanos
  got_talent  : reacciones 1ª mitad + reacciones 2ª mitad + planos lejanos
Una plantilla solo guarda RUTAS y duraciones (no copia los vídeos).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

STYLES = {"la_voz": "La Voz", "got_talent": "Got Talent"}
JUDGE_STATES = ("antes", "girando", "girado")


@dataclass
class PoolItem:
    path: str
    duration_s: float = 0.0
    has_audio: bool = False


def pool_keys(style: str) -> List[str]:
    """Lista ordenada de grupos que admite cada estilo."""
    keys = ["far"]
    if style == "la_voz":
        keys += [f"j{j}_{s}" for j in range(1, 5) for s in JUDGE_STATES] + ["react2"]
    else:
        keys += ["react1", "react2"]
    return keys


def pool_label(key: str) -> str:
    if key == "far":
        return "Planos lejanos"
    if key == "react1":
        return "Reacciones 1ª mitad"
    if key == "react2":
        return "Reacciones 2ª mitad"
    m = re.fullmatch(r"j(\d)_(\w+)", key)
    if m:
        return f"Juez {m.group(1)} · {m.group(2)}"
    return key


@dataclass
class Template:
    name: str
    style: str = "la_voz"
    pools: Dict[str, List[PoolItem]] = field(default_factory=dict)
    far_s: float = 3.0               # duración usada de cada plano lejano
    intro_s: float = 30.0            # intro solo de reacciones (La Voz)
    lipsync_s: float = 10.0          # duración de cada hueco de lipsync

    def pool(self, key: str) -> List[PoolItem]:
        return self.pools.setdefault(key, [])

    def add_files(self, key: str, items: List[PoolItem]) -> int:
        have = {i.path for i in self.pool(key)}
        new = [i for i in items if i.path not in have]
        self.pool(key).extend(new)
        return len(new)

    def remove(self, key: str, path: str) -> None:
        self.pools[key] = [i for i in self.pool(key) if i.path != path]

    def missing(self) -> List[str]:
        """Qué falta para poder generar un timeline (mensajes legibles)."""
        out = []
        if not self.pool("far"):
            out.append("Planos lejanos: sube al menos 1 clip")
        if self.style == "la_voz":
            for j in range(1, 5):
                if not self.pool(f"j{j}_girando"):
                    out.append(f"Juez {j} · girando: sube al menos 1 clip")
            if not any(self.pool(f"j{j}_antes") for j in range(1, 5)):
                out.append("Jueces · antes: sube clips de reacción antes de girar")
        else:
            if not self.pool("react1") and not self.pool("react2"):
                out.append("Reacciones: sube al menos 1 clip")
        return out

    def counts(self) -> Dict[str, int]:
        return {k: len(self.pool(k)) for k in pool_keys(self.style)}

    # persistencia
    def to_dict(self) -> dict:
        return {"name": self.name, "style": self.style, "far_s": self.far_s, "intro_s": self.intro_s,
                "lipsync_s": self.lipsync_s,
                "pools": {k: [asdict(i) for i in v] for k, v in self.pools.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "Template":
        return cls(name=d["name"], style=d.get("style", "la_voz"), far_s=d.get("far_s", 3.0),
                   intro_s=d.get("intro_s", 30.0), lipsync_s=d.get("lipsync_s", 10.0),
                   pools={k: [PoolItem(**i) for i in v] for k, v in d.get("pools", {}).items()})


# ── almacén en disco ───────────────────────────────────────────────
def _slug(name: str) -> str:
    return re.sub(r"[^\w\-]+", "_", name.strip()).strip("_").lower() or "plantilla"


class TemplateStore:
    def __init__(self, folder: str):
        self.folder = folder
        os.makedirs(folder, exist_ok=True)

    def path(self, name: str) -> str:
        return os.path.join(self.folder, _slug(name) + ".fctpl.json")

    def save(self, tpl: Template) -> str:
        p = self.path(tpl.name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(tpl.to_dict(), f, ensure_ascii=False, indent=1)
        return p

    def load(self, name: str) -> Template:
        with open(self.path(name), "r", encoding="utf-8") as f:
            return Template.from_dict(json.load(f))

    def names(self) -> List[str]:
        out = []
        for fn in sorted(os.listdir(self.folder)):
            if fn.endswith(".fctpl.json"):
                try:
                    with open(os.path.join(self.folder, fn), encoding="utf-8") as f:
                        out.append(json.load(f)["name"])
                except Exception:
                    pass
        return out

    def delete(self, name: str) -> None:
        p = self.path(name)
        if os.path.exists(p):
            os.remove(p)
