<#
  Compila ForgeCut para Windows en TU PC y deja dist\ForgeCut-windows.zip (con ffmpeg incluido).
  Requisitos (una sola vez):
    1) Python 3.10+ y este proyecto con su entorno virtual activado.
    2) Visual Studio 2022 «Build Tools» con la carga de trabajo «Desarrollo para el escritorio con C++».
    3) Windows: Configuración → Privacidad y seguridad → Para programadores → «Modo desarrollador» ACTIVADO
       (Flutter lo necesita para crear enlaces simbólicos).
  Uso:   powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 [-Version 0.2.0] [-SinFfmpeg]
#>
param([string]$Version = "0.2.0", [switch]$SinFfmpeg)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

python -m pip install --upgrade pip | Out-Null
python -m pip install "flet==0.86.0"
flet build windows --build-version $Version --verbose
if ($LASTEXITCODE -ne 0) { throw "flet build windows falló (revisa los requisitos de arriba)" }

if (-not $SinFfmpeg) {
  $tmp = Join-Path $env:TEMP "forgecut_ffmpeg"
  Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
  New-Item -ItemType Directory $tmp | Out-Null
  Invoke-WebRequest -Uri "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip" -OutFile "$tmp\ffmpeg.zip"
  Expand-Archive "$tmp\ffmpeg.zip" -DestinationPath "$tmp\x"
  $bin = Get-ChildItem "$tmp\x" -Recurse -Filter ffmpeg.exe | Select-Object -First 1
  Copy-Item $bin.FullName build\windows\ffmpeg.exe
  Copy-Item (Join-Path $bin.DirectoryName "ffprobe.exe") build\windows\ffprobe.exe
}

New-Item -ItemType Directory dist -Force | Out-Null
Compress-Archive -Path build\windows\* -DestinationPath dist\ForgeCut-windows.zip -Force
Write-Host "`nListo: dist\ForgeCut-windows.zip  (descomprime y ejecuta ForgeCut.exe)" -ForegroundColor Green
