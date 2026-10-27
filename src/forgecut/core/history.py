"""Deshacer / rehacer por instantáneas del timeline (simple y robusto)."""
from __future__ import annotations

import copy
from typing import List, Optional

from .models import Timeline


class History:
    def __init__(self, limit: int = 200):
        self.limit = limit
        self._undo: List[dict] = []
        self._redo: List[dict] = []

    def commit(self, tl: Timeline) -> None:
        """Guarda el estado ANTES de una modificación."""
        self._undo.append(copy.deepcopy(tl.to_dict()))
        if len(self._undo) > self.limit:
            self._undo.pop(0)
        self._redo.clear()

    def undo(self, current: Timeline) -> Optional[Timeline]:
        if not self._undo:
            return None
        self._redo.append(copy.deepcopy(current.to_dict()))
        return Timeline.from_dict(self._undo.pop())

    def redo(self, current: Timeline) -> Optional[Timeline]:
        if not self._redo:
            return None
        self._undo.append(copy.deepcopy(current.to_dict()))
        return Timeline.from_dict(self._redo.pop())

    def discard(self) -> None:
        """Descarta la última instantánea (una edición que falló) sin tocar el rehacer."""
        if self._undo:
            self._undo.pop()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
