# ============================================================
# install.py
# Just set ONE repo. Everything else is automatic.
#
# For native packages: cross-compiles with the Android NDK
# against a locally-built cp313 aarch64 Python sysroot.
# For pure-Python packages: builds + retags the wheel.
#
# Host: Linux x86_64
# Target: Android API 33, aarch64, cp313
# ============================================================

import subprocess
import sys
import shutil
import os
import re
import zipfile
import tarfile
import urllib.request
import urllib.error

# ----------------------------------------------------------------
REPO = "https://github.com/scikit-learn/scikit-learn.git"
# ----------------------------------------------------------------

OUTPUT_DIR   = "wheelhouse"
WORK_DIR     = "_work"
ANDROID_API  = 33
TARGET_TAG   = "cp313-cp313-linux_aarch64"
TARGET_PY    = "cp313"
TARGET_PLAT  = "linux_aarch64"

# Android NDK r27c — latest stable LTS, Linux x86_64
NDK_VERSION  = "r27c"
NDK_URL      = f"https://dl.google.com/android/repository/android-ndk-{NDK_VERSION}-linux.zip"
NDK_DIR      = os.path.join(WORK_DIR, f"android-ndk-{NDK_VERSION}")

# CPython 3.13 source
CPYTHON_VER     = "3.13.3"
CPYTHON_URL     = f"https://www.python.org/ftp/python/{CPYTHON_VER}/Python-{CPYTHON_VER}.tar.xz"
CPYTHON_SRC     = os.path.join(WORK_DIR, f"Python-{CPYTHON_VER}")
CPYTHON_HOST    = os.path.join(WORK_DIR, "cpython-host")     # native build for cross-build tools
CPYTHON_XBLD    = os.path.join(WORK_DIR, "cpython-aarch64")  # cross-build output
CPYTHON_SYSROOT = os.path.join(WORK_DIR, "python-sysroot")   # installed headers + libpython

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(WORK_DIR,   exist_ok=True)


# ================================================================
# Helpers
# ================================================================

def run(cmd, cwd=None, env=None, check=True):
    # Filter empty strings that can sneak in from conditional flags
    cmd = [c for c in cmd if c]
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    e = {**os.environ, **(env or {})}
    result = subprocess.run(cmd, cwd=cwd, env=e)
    if check and result.returncode != 0:
        print(f"ERROR: command failed with exit code {result.returncode}")
        sys.exit(result.returncode)
    return result.returncode


def download(url, dest, label=None):
    if os.path.exists(dest):
        print(f"  Already downloaded: {os.path.basename(dest)}")
        return
    print(f"  Downloading {label or os.path.basename(dest)} ...")

    def _progress(block_num, block_size, total_size):
        if total_size > 0:
            pct = min(block_num * block_size * 100 // total_size, 100)
            print(f"\r  {pct}%  ", end="", flush=True)

    tmp = dest + ".part"
    urllib.request.urlretrieve(url, tmp, reporthook=_progress)
    print()
    os.rename(tmp, dest)
    print(f"  Saved: {dest}")


# ================================================================
# NDK bootstrap
# ================================================================

def ensure_ndk():
    if os.path.isdir(NDK_DIR):
        print(f"  NDK already present: {NDK_DIR}")
        return NDK_DIR

    zip_path = os.path.join(WORK_DIR, f"android-ndk-{NDK_VERSION}-linux.zip")
    download(NDK_URL, zip_path, f"Android NDK {NDK_VERSION} (~650 MB)")

    print(f"  Extracting NDK (this takes a minute)...")
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(WORK_DIR)

    if not os.path.isdir(NDK_DIR):
        print(f"ERROR: expected NDK dir not found after extraction: {NDK_DIR}")
        sys.exit(1)

    print(f"  NDK ready: {NDK_DIR}")
    return NDK_DIR


def ndk_toolchain(ndk_dir):
    """
    Returns a dict of cross-compiler env vars for aarch64-linux-android{API}.
    """
    llvm_bin = os.path.join(
        ndk_dir, "toolchains", "llvm", "prebuilt", "linux-x86_64", "bin"
    )
    triple = f"aarch64-linux-android{ANDROID_API}"
    return {
        "CC":            os.path.join(llvm_bin, f"{triple}-clang"),
        "CXX":           os.path.join(llvm_bin, f"{triple}-clang++"),
        "AR":            os.path.join(llvm_bin, "llvm-ar"),
        "RANLIB":        os.path.join(llvm_bin, "llvm-ranlib"),
        "STRIP":         os.path.join(llvm_bin, "llvm-strip"),
        "READELF":       os.path.join(llvm_bin, "llvm-readelf"),
        "LD":            os.path.join(llvm_bin, "ld"),
        "ANDROID_NDK":   ndk_dir,
        "CROSS_COMPILE": triple + "-",
    }


# ================================================================
# CPython 3.13 aarch64 sysroot
# ================================================================

def ensure_cpython_source():
    if os.path.isdir(CPYTHON_SRC):
        print(f"  CPython source already present: {CPYTHON_SRC}")
        return
    tar_path = os.path.join(WORK_DIR, f"Python-{CPYTHON_VER}.tar.xz")
    download(CPYTHON_URL, tar_path, f"CPython {CPYTHON_VER} source")
    print(f"  Extracting CPython source...")
    with tarfile.open(tar_path, "r:xz") as t:
        t.extractall(WORK_DIR)
    print(f"  Source ready: {CPYTHON_SRC}")


def ensure_cpython_host():
    """
    Build a native host CPython so the cross-build has the pgen/freeze
    tools it needs. Skipped if already built.
    """
    stamp = os.path.join(CPYTHON_HOST, ".built")
    if os.path.exists(stamp):
        print(f"  Host CPython already built: {CPYTHON_HOST}")
        return os.path.join(CPYTHON_HOST, "bin", "python3")

    os.makedirs(CPYTHON_HOST, exist_ok=True)
    print(f"\n  Building host CPython (required by cross-build)...")

    run([
        os.path.join(os.path.abspath(CPYTHON_SRC), "configure"),
        f"--prefix={os.path.abspath(CPYTHON_HOST)}",
        "--with-ensurepip=no",
        "--disable-test-modules",
    ], cwd=os.path.abspath(CPYTHON_HOST))

    cpu_count = os.cpu_count() or 4
    run(["make", f"-j{cpu_count}"], cwd=os.path.abspath(CPYTHON_HOST))
    run(["make", "install"],        cwd=os.path.abspath(CPYTHON_HOST))

    open(stamp, "w").close()
    return os.path.join(CPYTHON_HOST, "bin", "python3")


def ensure_cpython_sysroot(ndk_dir):
    """
    Cross-compile CPython 3.13 for aarch64-linux-android and install
    headers + libpython into CPYTHON_SYSROOT.
    """
    stamp = os.path.join(CPYTHON_SYSROOT, ".built")
    if os.path.exists(stamp):
        print(f"  aarch64 CPython sysroot already built: {CPYTHON_SYSROOT}")
        return

    ensure_cpython_source()
    host_python = ensure_cpython_host()

    os.makedirs(os.path.abspath(CPYTHON_SYSROOT), exist_ok=True)
    os.makedirs(os.path.abspath(CPYTHON_XBLD),    exist_ok=True)

    tc = ndk_toolchain(ndk_dir)
    ndk_sysroot = os.path.join(
        ndk_dir, "toolchains", "llvm", "prebuilt", "linux-x86_64", "sysroot"
    )

    cflags  = f"--sysroot={ndk_sysroot} -fPIC"
    ldflags = f"--sysroot={ndk_sysroot}"

    print(f"\n  Cross-compiling CPython {CPYTHON_VER} → aarch64-linux-android{ANDROID_API} ...")

    cross_env = {
        **tc,
        "CFLAGS":  cflags,
        "LDFLAGS": ldflags,
        # autoconf cross-compile cache values — nothing can be executed
        "ac_cv_file__dev_ptmx":    "yes",
        "ac_cv_file__dev_tty":     "yes",
        "ac_cv_buggy_getaddrinfo": "no",
    }

    run([
        os.path.join(os.path.abspath(CPYTHON_SRC), "configure"),
        "--host=aarch64-linux-android",
        "--build=x86_64-linux-gnu",
        f"--prefix={os.path.abspath(CPYTHON_SYSROOT)}",
        f"--with-build-python={os.path.abspath(host_python)}",
        "--enable-shared",
        "--without-ensurepip",
        "--disable-test-modules",
        "--with-system-ffi=no",
        "--with-system-expat=no",
        "--without-pymalloc",
        f"--with-sysroot={ndk_sysroot}",
    ], cwd=os.path.abspath(CPYTHON_XBLD), env=cross_env)

    cpu_count = os.cpu_count() or 4
    run(["make", f"-j{cpu_count}"], cwd=os.path.abspath(CPYTHON_XBLD), env=cross_env)
    run(["make", "install"],        cwd=os.path.abspath(CPYTHON_XBLD), env=cross_env)

    open(stamp, "w").close()
    print(f"  aarch64 CPython sysroot ready: {CPYTHON_SYSROOT}")


# ================================================================
# Package name detection
# ================================================================

def get_package_name(clone_dir):
    for path in ["pyproject.toml", "setup.cfg", "setup.py"]:
        full = os.path.join(clone_dir, path)
        if not os.path.exists(full):
            continue
        with open(full) as f:
            content = f.read()
        match = re.search(r'name\s*=\s*["\']([^"\']+)["\']', content)
        if match:
            return match.group(1)
    return os.path.basename(clone_dir).lstrip("_src_")


# ================================================================
# Dependency reader
# ================================================================

def read_deps(clone_dir):
    deps = []

    pyproject = os.path.join(clone_dir, "pyproject.toml")
    if os.path.exists(pyproject):
        with open(pyproject) as f:
            content = f.read()
        match = re.search(r'dependencies\s*=\s*\[(.*?)\]', content, re.DOTALL)
        if match:
            deps += re.findall(r'["\']([^"\']+)["\']', match.group(1))

    setup_cfg = os.path.join(clone_dir, "setup.cfg")
    if os.path.exists(setup_cfg):
        with open(setup_cfg) as f:
            content = f.read()
        match = re.search(r'install_requires\s*=\s*((?:\n\s+\S.*)+)', content)
        if match:
            deps += [l.strip() for l in match.group(1).splitlines() if l.strip()]

    setup_py = os.path.join(clone_dir, "setup.py")
    if os.path.exists(setup_py):
        with open(setup_py) as f:
            content = f.read()
        match = re.search(r'install_requires\s*=\s*\[(.*?)\]', content, re.DOTALL)
        if match:
            deps += re.findall(r'["\']([^"\']+)["\']', match.group(1))

    requirements = os.path.join(clone_dir, "requirements.txt")
    if os.path.exists(requirements):
        with open(requirements) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    deps.append(line)

    return deps


# ================================================================
# Build system detection
# ================================================================

def detect_build_system(clone_dir):
    info = {
        "backend":      "unknown",
        "has_cython":   False,
        "has_cffi":     False,
        "has_pybind11": False,
        "has_swig":     False,
        "has_fortran":  False,
        "has_rust":     False,
        "native":       False,
    }

    def _read(p):
        if os.path.exists(p):
            with open(p) as f:
                return f.read()
        return ""

    combined = (
        _read(os.path.join(clone_dir, "pyproject.toml")) +
        _read(os.path.join(clone_dir, "setup.py")) +
        _read(os.path.join(clone_dir, "setup.cfg"))
    ).lower()

    if re.search(r'mesonpy|meson-python|meson\.build', combined):
        info["backend"] = "meson"
    elif re.search(r'scikit.build|skbuild|cmake', combined):
        info["backend"] = "cmake"
    elif re.search(r'maturin', combined):
        info["backend"] = "rust"
    elif re.search(r'flit', combined):
        info["backend"] = "flit"
    elif re.search(r'hatchling|hatch', combined):
        info["backend"] = "hatchling"
    elif re.search(r'pdm', combined):
        info["backend"] = "pdm"
    elif re.search(r'poetry', combined):
        info["backend"] = "poetry"
    elif re.search(r'setuptools', combined):
        info["backend"] = "setuptools"

    if re.search(r'cython',   combined): info["has_cython"]   = True
    if re.search(r'cffi',     combined): info["has_cffi"]     = True
    if re.search(r'pybind11', combined): info["has_pybind11"] = True
    if re.search(r'swig',     combined): info["has_swig"]     = True
    if re.search(r'cargo|maturin|rustc', combined): info["has_rust"] = True
    if os.path.exists(os.path.join(clone_dir, "Cargo.toml")): info["has_rust"] = True

    SKIP_DIRS = {'.git', 'docs', 'doc', 'tests', 'test', 'benchmarks'}
    for root, dirs, files in os.walk(clone_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext in ('.pyx', '.pxd'):                        info["has_cython"]  = True
            if ext in ('.f', '.f90', '.f95', '.f03', '.for'): info["has_fortran"] = True
            if ext in ('.c', '.cpp', '.cxx', '.cc'):           info["native"]      = True
            if ext == '.i':                                     info["has_swig"]    = True

    if info["has_fortran"]: info["native"] = True
    if any([info["has_cython"], info["has_cffi"],
            info["has_pybind11"], info["has_swig"], info["has_rust"]]):
        info["native"] = True
    if info["backend"] in ("meson", "cmake", "rust"):
        info["native"] = True

    return info


# ================================================================
# Wheel verify
# ================================================================

def verify_wheel(whl_path):
    print(f"\n  Verifying: {os.path.basename(whl_path)}")
    errors = []
    valid_arm64_tags = ("linux_aarch64", "android_arm64", "arm64_v8a")

    if not any(t in whl_path for t in valid_arm64_tags):
        errors.append("FAIL filename: no recognised arm64 tag in filename")
    else:
        print(f"  ✓ Filename tag OK")

    try:
        with zipfile.ZipFile(whl_path, "r") as z:
            wheel_files = [n for n in z.namelist() if n.endswith("/WHEEL")]
            if not wheel_files:
                errors.append("FAIL metadata: no WHEEL file inside archive")
            else:
                content = z.read(wheel_files[0]).decode()
                tag_lines = re.findall(r"^Tag:\s*(.+)$", content, re.MULTILINE)
                if not tag_lines:
                    errors.append("FAIL metadata: no Tag line in WHEEL file")
                else:
                    matched = [t for t in tag_lines
                               if any(a in t for a in valid_arm64_tags)]
                    if not matched:
                        errors.append(
                            f"FAIL metadata: no arm64 Tag found "
                            f"(tags: {', '.join(t.strip() for t in tag_lines)})"
                        )
                    else:
                        print(f"  ✓ Metadata tag OK ({matched[0].strip()})")

            so_files = [n for n in z.namelist() if n.endswith(".so")]
            if so_files:
                print(f"  Checking {len(so_files)} .so file(s)...")
                arch_map = {
                    0xB7: "aarch64 ✓",
                    0x3E: "x86_64  ✗ WRONG ARCH",
                    0x28: "arm32   ✗ WRONG ARCH",
                    0x08: "mips    ✗ WRONG ARCH",
                    0x16: "ppc     ✗ WRONG ARCH",
                }
                for so in so_files:
                    data = z.read(so)
                    if len(data) >= 20 and data[:4] == b'\x7fELF':
                        e_machine = data[18]
                        desc = arch_map.get(e_machine, f"unknown (0x{e_machine:02X}) ✗")
                        if "✗" in desc:
                            errors.append(f"FAIL binary: {os.path.basename(so)} is {desc}")
                        else:
                            print(f"  ✓ {os.path.basename(so)} → {desc}")
            else:
                print(f"  ✓ No .so files (pure Python)")

    except Exception as exc:
        errors.append(f"FAIL: could not read zip — {exc}")

    if errors:
        print()
        for err in errors:
            print(f"  ✗ {err}")
        print()
        sys.exit(1)

    print(f"  ✓ Wheel fully verified as Android arm64")


# ================================================================
# Pure-Python wheel rename
# ================================================================

def rename_to_android(whl_path):
    filename = os.path.basename(whl_path)
    parts = filename[:-4].split("-")
    if len(parts) < 5:
        print(f"  WARNING: unexpected wheel filename: {filename}")
        return whl_path

    parts[2] = TARGET_PY
    parts[3] = TARGET_PY
    parts[4] = TARGET_PLAT
    new_filename = "-".join(parts) + ".whl"
    new_path = os.path.join(os.path.dirname(whl_path), new_filename)

    tmp_path = whl_path + ".tmp"
    with zipfile.ZipFile(whl_path, "r") as zin, \
         zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.endswith("/WHEEL"):
                text = re.sub(r"Tag:.*", f"Tag: {TARGET_TAG}", data.decode())
                data = text.encode()
            zout.writestr(item, data)

    os.remove(whl_path)
    os.rename(tmp_path, new_path)
    print(f"  Renamed:  {filename}")
    print(f"        --> {new_filename}")
    return new_path


# ================================================================
# Build paths
# ================================================================

def build_pure(clone_dir, name):
    print(f"\n[3/4] Building pure-Python wheel with pip wheel...")
    out_abs = os.path.abspath(OUTPUT_DIR)
    run([
        sys.executable, "-m", "pip", "wheel",
        "--no-deps",
        "--wheel-dir", out_abs,
        clone_dir,
    ])

    norm_name = name.replace("-", "_").lower()
    built = [
        os.path.join(OUTPUT_DIR, f)
        for f in os.listdir(OUTPUT_DIR)
        if f.endswith(".whl") and norm_name in f.replace("-", "_").lower()
    ]
    if not built:
        print("ERROR: no wheel found after build")
        sys.exit(1)

    print(f"\n[4/4] Renaming + verifying...")
    for whl in built:
        final = rename_to_android(whl)
        verify_wheel(final)


def build_native(clone_dir, name, binfo, ndk_dir):
    """
    Cross-compiles a native wheel for aarch64 Android using:
      - Android NDK r27c clang toolchain
      - Locally cross-compiled CPython 3.13 headers + libpython
    No Chaquopy, no pre-built wheels, no cibuildwheel.
    """
    print(f"\n[3/4] Cross-compiling native wheel for aarch64 Android cp313...")
    print(f"  Package:  {name}")
    print(f"  Backend:  {binfo['backend']}")

    ensure_cpython_sysroot(ndk_dir)

    tc = ndk_toolchain(ndk_dir)
    ndk_sysroot = os.path.join(
        ndk_dir, "toolchains", "llvm", "prebuilt", "linux-x86_64", "sysroot"
    )
    sysroot_inc = os.path.abspath(
        os.path.join(CPYTHON_SYSROOT, "include", "python3.13")
    )
    sysroot_lib = os.path.abspath(os.path.join(CPYTHON_SYSROOT, "lib"))

    cflags  = f"--sysroot={ndk_sysroot} -fPIC -I{sysroot_inc}"
    ldflags = f"--sysroot={ndk_sysroot} -L{sysroot_lib} -lpython3.13"

    # _PYTHON_HOST_PLATFORM makes pip/setuptools tag the output wheel
    # for the target platform rather than the host
    cross_env = {
        **tc,
        "CFLAGS":                cflags,
        "CXXFLAGS":              cflags,
        "LDFLAGS":               ldflags,
        "_PYTHON_HOST_PLATFORM": "linux-aarch64",
        "PYTHON_INCLUDE_DIR":    sysroot_inc,
        "PYTHON_LIB_DIR":        sysroot_lib,
        "PYTHONPATH":            "",  # prevent host site-packages bleeding in
    }

    # Cython: pre-generate .c files on the host so the cross-compiler
    # only needs to compile plain C — no Cython runtime needed on target
    if binfo["has_cython"]:
        print("  Pre-generating Cython .c files on host...")
        run([sys.executable, "-m", "pip", "install", "--quiet", "cython"])
        SKIP_DIRS = {'.git', 'docs', 'doc'}
        for root, dirs, files in os.walk(clone_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fname in files:
                if fname.endswith(".pyx"):
                    pyx_path = os.path.join(root, fname)
                    run(["cython", "--3str", pyx_path])

    out_abs = os.path.abspath(OUTPUT_DIR)
    run([
        sys.executable, "-m", "pip", "wheel",
        "--no-deps",
        "--wheel-dir", out_abs,
        clone_dir,
    ], env=cross_env)

    norm_name = name.replace("-", "_").lower()
    built = [
        os.path.join(OUTPUT_DIR, f)
        for f in os.listdir(OUTPUT_DIR)
        if f.endswith(".whl") and norm_name in f.replace("-", "_").lower()
    ]
    if not built:
        print("ERROR: no wheel found after cross-build")
        sys.exit(1)

    # Retag to cp313-cp313-linux_aarch64 in case pip stamped the host tag
    print(f"\n[4/4] Retagging + verifying...")
    for whl in built:
        final = rename_to_android(whl)
        verify_wheel(final)


# ================================================================
# Main
# ================================================================

def build_package(repo):
    clone_dir = "_src_" + repo.rstrip("/").split("/")[-1].replace(".git", "")

    print(f"\n{'='*55}")

    if os.path.exists(clone_dir):
        shutil.rmtree(clone_dir)

    print(f"[1/4] Cloning {repo} ...")
    run(["git", "clone", "--depth=1", repo, clone_dir])

    name = get_package_name(clone_dir)
    print(f"      Package name: {name}")

    print(f"\n[2/4] Analysing project...")
    binfo = detect_build_system(clone_dir)

    deps = read_deps(clone_dir)
    print(f"  Dependencies ({len(deps)}):")
    for d in deps:
        print(f"    {d}")
    if not deps:
        print("    (none found)")

    print(f"\n  Build system: {binfo['backend']}")
    print(f"  Native:       {binfo['native']}")

    if binfo["native"]:
        print(f"\n[*] Native package — bootstrapping NDK + cp313 aarch64 sysroot...")
        ndk_dir = ensure_ndk()
        build_native(clone_dir, name, binfo, ndk_dir)
    else:
        build_pure(clone_dir, name)

    print(f"\n✓ Done: {name}")


if __name__ == "__main__":
    build_package(REPO)

    print(f"\n{'='*55}")
    print(f"All wheels saved to: {os.path.abspath(OUTPUT_DIR)}/")
    for w in sorted(f for f in os.listdir(OUTPUT_DIR) if f.endswith(".whl")):
        print(f"  {w}")
