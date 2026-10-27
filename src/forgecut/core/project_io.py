"""Guardar / abrir proyectos (.fcproj = JSON legible)."""
from __future__ import annotations

import json
import os
import tempfile

from .models import Project

EXT = ".fcproj"


def save_project(project: Project, path: str) -> str:
    if not path.lower().endswith(EXT):
        path += EXT
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    # escritura atómica: nunca deja un proyecto a medias si algo falla
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(project.to_dict(), f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return path


def load_project(path: str) -> Project:
    with open(path, "r", encoding="utf-8") as f:
        return Project.from_dict(json.load(f))
