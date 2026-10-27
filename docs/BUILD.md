# Compilar ForgeCut: Android (GitHub Actions) y Windows

## 0. Probar antes en el PC (recomendado)
```powershell
.\.venv\Scripts\activate
pip install -r requirements.txt
python -m unittest discover -s tests          # 100+ tests; los de medios usan ffmpeg real
cd src ; flet run main.py
```

## 1. Windows

### Opción A — en tu PC
Requisitos (una vez): **Visual Studio 2022 Build Tools** con «Desarrollo para el escritorio con C++» y el **Modo desarrollador** de Windows activado.
```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 -Version 0.2.0
```
Resultado: `dist\ForgeCut-windows.zip` (ForgeCut.exe + ffmpeg.exe + ffprobe.exe). Descomprime y ejecuta `ForgeCut.exe`.
ForgeCut busca ffmpeg/ffprobe junto al `.exe`, en el PATH, en `FORGECUT_BIN_DIR` o en `%LOCALAPPDATA%\ForgeCut\ffmpeg`.

### Opción B — GitHub Actions (sin instalar nada)
Mismo flujo que Android (apartado 2): el job `windows` genera `ForgeCut-windows.zip`.

## 2. Android desde GitHub Actions

1. Crea un repositorio en GitHub y sube **el contenido de la carpeta `forgecut/`** (la raíz del repo debe contener `pyproject.toml`, `src/`, `.github/`).
   ```powershell
   git init
   git add .
   git commit -m "ForgeCut v0.2.0"
   git branch -M main
   git remote add origin https://github.com/TU_USUARIO/forgecut.git
   git push -u origin main
   ```
2. **Lanzar la compilación** (elige una):
   - *Manual:* pestaña **Actions → Build ForgeCut → Run workflow** (marca Android y/o Windows).
   - *Release:* `git tag v0.2.0 ; git push origin v0.2.0` → compila todo y crea una **Release** con los APK y el zip de Windows.
3. **Descargar:** en la ejecución terminada, apartado **Artifacts** → `forgecut-android-apk`. Verás 3 APK:
   `app-arm64-v8a-release.apk` (la mayoría de móviles actuales), `app-armeabi-v7a-release.apk` (móviles antiguos de 32 bits) y `app-x86_64-release.apk` (emulador).
4. **Instalar:** pasa el APK al móvil → ábrelo → permite «instalar apps de origen desconocido». (Primera compilación: 15–25 min; las siguientes, menos gracias a la caché de Flutter.)

### Firma de release (opcional)
Sin configurar nada, el APK se firma con la clave de depuración y se instala sin problema. Para publicar en Play Store o poder **actualizar** siempre sobre la misma instalación:
`powershell scripts\make_android_keystore.ps1` y crea los secretos `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_PASSWORD`, `ANDROID_KEY_ALIAS` en *Settings → Secrets and variables → Actions*.

## 3. Qué funciona hoy en Android (honesto)
Android no permite lanzar el ejecutable de ffmpeg desde la app como en el PC, así que la primera versión móvil funciona en **modo editor**:

| Función | Android v0.2 |
|---|---|
| Crear/abrir/guardar proyectos, Media Pool con carpetas, importar medios (duración y tamaño leídos en Python puro) | ✅ |
| Timeline: mover, recortar, cuchilla, multiselección, deshacer, marcadores, inspector | ✅ |
| Plantillas La Voz / Got Talent y generación automática del timeline | ✅ |
| Vista previa del vídeo, miniaturas/onda, auto-corte por escenas, exportar audio, render | ⚠️ necesitan ffmpeg: desactivadas (la app lo avisa arriba) |

Cuando exista un `libffmpeg.so` ejecutable dentro del APK, `_find()` en `media/ffmpeg_backend.py` ya lo busca en las librerías nativas y esas funciones se activan sin más cambios. Mientras tanto, el flujo móvil recomendado es **editar y guardar el proyecto en el móvil y renderizar en el PC**.

## 4. Si la compilación falla
- Abre el job fallido → mira el paso **Compilar APK / Compilar Windows** (corre con `--verbose`).
- *Windows local:* casi siempre falta «Desarrollo para el escritorio con C++» o el Modo desarrollador.
- Los flags `--split-per-abi`, `--build-number`, `--build-version` y los de firma son de `flet build`; si tu versión de Flet los renombrara, quítalos del paso y vuelve a lanzar.
