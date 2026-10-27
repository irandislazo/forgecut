#!/usr/bin/env bash
# Compila ffmpeg (con x264 + mp3lame) para Android arm64 y lo deja como out/libffmpeg.so.
# Es un EJECUTABLE PIE con nombre de librería: Android solo deja ejecutar binarios que viven en la carpeta
# de librerías nativas de la app (nativeLibraryDir), y solo extrae allí archivos llamados lib*.so.
set -euo pipefail
NDK="${ANDROID_NDK_LATEST_HOME:-${ANDROID_NDK_HOME:?define ANDROID_NDK_HOME}}"
TC="$NDK/toolchains/llvm/prebuilt/linux-x86_64"; API=24
T=aarch64-linux-android; CC="$TC/bin/${T}${API}-clang"; AR="$TC/bin/llvm-ar"; RANLIB="$TC/bin/llvm-ranlib"; STRIP="$TC/bin/llvm-strip"
W="$(pwd)/work"; P="$W/prefix"; OUT="$(pwd)/out"; mkdir -p "$W" "$P" "$OUT"; cd "$W"
JOBS=$(nproc)

[ -d x264 ] || git clone --depth 1 https://code.videolan.org/videolan/x264.git
( cd x264 && CC="$CC" AR="$AR" RANLIB="$RANLIB" STRIP="$STRIP" ./configure --host=aarch64-linux --prefix="$P" \
    --enable-static --disable-cli --enable-pic --disable-opencl && make -j"$JOBS" && make install )

[ -d lame-3.100 ] || { curl -fL "https://downloads.sourceforge.net/project/lame/lame/3.100/lame-3.100.tar.gz" | tar xz; }
( cd lame-3.100 && CC="$CC" AR="$AR" RANLIB="$RANLIB" ./configure --host=aarch64-linux --prefix="$P" \
    --enable-static --disable-shared --disable-frontend --disable-decoder && make -j"$JOBS" && make install )

[ -d ffmpeg ] || git clone --depth 1 --branch n7.1 https://github.com/FFmpeg/FFmpeg.git ffmpeg
cd ffmpeg
export PKG_CONFIG_LIBDIR="$P/lib/pkgconfig" PKG_CONFIG_PATH="$P/lib/pkgconfig"
./configure --prefix="$P/ff" --target-os=android --arch=aarch64 --enable-cross-compile \
  --cc="$CC" --ar="$AR" --ranlib="$RANLIB" --strip="$STRIP" --nm="$TC/bin/llvm-nm" \
  --enable-gpl --enable-libx264 --enable-libmp3lame --enable-pic --enable-pie \
  --disable-shared --enable-static --disable-doc --disable-ffplay --disable-ffprobe --disable-debug \
  --pkg-config=pkg-config --pkg-config-flags=--static \
  --extra-cflags="-I$P/include -fPIE" --extra-ldflags="-L$P/lib -pie" --extra-libs="-lm"
make -j"$JOBS" ffmpeg
"$STRIP" ffmpeg
cp ffmpeg "$OUT/libffmpeg.so"
file "$OUT/libffmpeg.so" || true; ls -la "$OUT"
