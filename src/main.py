"""Punto de entrada de Flet (`flet run` en escritorio, `flet build apk` en Android)."""
import flet as ft

from forgecut.ui.app import main

if __name__ == "__main__":
    ft.run(main)
