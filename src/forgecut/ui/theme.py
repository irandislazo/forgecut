"""Paleta oscura estilo DaVinci Resolve."""
BG = "#1b1b1d"
PANEL = "#232326"
PANEL_2 = "#2b2b2f"
PANEL_3 = "#34343a"
LINE = "#0f0f10"
TEXT = "#d8d8dc"
TEXT_DIM = "#8b8b93"
ACCENT = "#e0522d"          # naranja de selección (como la barra de páginas de Resolve)
ACCENT_2 = "#3b82f6"
PLAYHEAD = "#ff3b30"
TRACK_V = "#26262a"
TRACK_A = "#222a26"
RULER = "#1f1f22"
CLIP_DEFAULT = "#64748b"

# Escala de la interfaz (pantallas pequeñas: 1366×768, 1280×720…). La fija app.main antes de construir los paneles.
SCALE = 1.0


def sc(n: float) -> int:
    """Tamaño (px) escalado."""
    return max(1, int(round(n * SCALE)))


def fs(n: float) -> int:
    """Tamaño de letra escalado (nunca menor de 8)."""
    return max(8, int(round(n * SCALE)))
