#!/usr/bin/env bash
# build-python-android.sh
# Cross-compile CPython 3.14 for Android ARM64 on Ubuntu using Android NDK.
# Because manually doing this line by line is how people lose weekends.

set -euo pipefail

### CONFIG ###
PY_VER="3.14.0"
NDK_VER="r27c"
API="24"
TARGET="aarch64-linux-android"

WORKDIR="$HOME/android-python-build"
PREFIX="/data/data/com.termux/files/usr"
JOBS="$(nproc)"

### PATHS ###
NDK_ZIP="android-ndk-${NDK_VER}-linux.zip"
NDK_DIR="$WORKDIR/android-ndk-${NDK_VER}"
PY_TAR="Python-${PY_VER}.tgz"
PY_SRC="$WORKDIR/Python-${PY_VER}"
OUTDIR="$WORKDIR/python-android-out"

mkdir -p "$WORKDIR"
cd "$WORKDIR"

echo "[*] Installing Ubuntu build dependencies..."
sudo apt update
sudo apt install -y \
  build-essential \
  wget \
  curl \
  unzip \
  git \
  tar \
  xz-utils \
  pkg-config \
  libssl-dev \
  zlib1g-dev \
  libbz2-dev \
  libffi-dev \
  libsqlite3-dev \
  libreadline-dev

echo "[*] Downloading Android NDK ${NDK_VER}..."
if [ ! -f "$NDK_ZIP" ]; then
    wget "https://dl.google.com/android/repository/${NDK_ZIP}"
fi

if [ ! -d "$NDK_DIR" ]; then
    unzip "$NDK_ZIP"
fi

echo "[*] Downloading Python ${PY_VER}..."
if [ ! -f "$PY_TAR" ]; then
    wget "https://www.python.org/ftp/python/${PY_VER}/${PY_TAR}"
fi

if [ -d "$PY_SRC" ]; then
    rm -rf "$PY_SRC"
fi

tar xf "$PY_TAR"

export NDK="$NDK_DIR"
export TOOLCHAIN="$NDK/toolchains/llvm/prebuilt/linux-x86_64"

export CC="$TOOLCHAIN/bin/${TARGET}${API}-clang"
export CXX="$TOOLCHAIN/bin/${TARGET}${API}-clang++"
export AR="$TOOLCHAIN/bin/llvm-ar"
export AS="$CC"
export LD="$TOOLCHAIN/bin/ld"
export RANLIB="$TOOLCHAIN/bin/llvm-ranlib"
export STRIP="$TOOLCHAIN/bin/llvm-strip"
export READELF="$TOOLCHAIN/bin/llvm-readelf"

cd "$PY_SRC"

echo "[*] Cleaning old build files..."
make distclean >/dev/null 2>&1 || true

echo "[*] Configuring CPython..."
./configure \
    --host="$TARGET" \
    --build="x86_64-pc-linux-gnu" \
    --prefix="$PREFIX" \
    --enable-shared \
    ac_cv_file__dev_ptmx=yes \
    ac_cv_file__dev_ptc=no \
    ac_cv_buggy_getaddrinfo=no

echo "[*] Building Python..."
make -j"$JOBS"

echo "[*] Installing into staging directory..."
rm -rf "$OUTDIR"
make install DESTDIR="$OUTDIR"

echo
echo "[+] Build complete."
echo "[+] Output directory:"
echo "    $OUTDIR$PREFIX"
echo
echo "[+] Example binary path:"
echo "    $OUTDIR$PREFIX/bin/python3"
echo
echo "[+] Copy to Android/Termux or package it yourself, as civilization intended."
