#!/usr/bin/env bash
# Uso: inject_ffmpeg.sh <apk arm64> <libffmpeg.so> <keystore> <alias> <pass>
# Añade lib/arm64-v8a/libffmpeg.so al APK ya compilado por Flet (no se toca nada más) y lo vuelve a firmar.
set -euo pipefail
APK="$1"; LIB="$2"; KS="$3"; ALIAS="$4"; PASS="$5"
BT="$(ls -d "${ANDROID_HOME:-$ANDROID_SDK_ROOT}"/build-tools/* | sort -V | tail -1)"
S="$(mktemp -d)"; mkdir -p "$S/lib/arm64-v8a"; cp "$LIB" "$S/lib/arm64-v8a/libffmpeg.so"
( cd "$S" && zip -q -r "$OLDPWD/$APK" lib/arm64-v8a/libffmpeg.so )
zip -q -d "$APK" 'META-INF/*.SF' 'META-INF/*.RSA' 'META-INF/*.DSA' 'META-INF/*.MF' || true   # firma antigua
"$BT/zipalign" -f -p 4 "$APK" "$APK.aligned" && mv "$APK.aligned" "$APK"
"$BT/apksigner" sign --ks "$KS" --ks-key-alias "$ALIAS" --ks-pass "pass:$PASS" --key-pass "pass:$PASS" "$APK"
"$BT/apksigner" verify "$APK"
echo "--- libffmpeg.so dentro del APK:"; unzip -l "$APK" | grep libffmpeg
echo "--- extractNativeLibs (debe ser true, o no aparecer si Flet ya extrae las .so):"
"$BT/aapt2" dump xmltree --file AndroidManifest.xml "$APK" | grep -i extractNativeLibs || echo "(atributo no presente)"
