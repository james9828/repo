# ============================================================
# install.py
# Just set ONE repo. Everything else is automatic.
# Fetches genuine Android arm64 wheels from Chaquopy for
# native packages; builds pure-Python wheels locally.
# ============================================================

import subprocess
import sys
import shutil
import os
import re
import zipfile
import urllib.request

# ----------------------------------------------------------------
REPO = "https://github.com/scikit-learn/scikit-learn.git"
# ----------------------------------------------------------------

OUTPUT_DIR     = "wheelhouse"
CHAQUOPY_INDEX = "https://chaquo.com/pypi-13.1"
ANDROID_API    = 33   # Android 13 = API 33

os.makedirs(OUTPUT_DIR, exist_ok=True)


# ================================================================
# Helpers
# ================================================================

def run(cmd, cwd=None, env=None):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    e = {**os.environ, **(env or {})}
    result = subprocess.run(cmd, cwd=cwd, env=e)
    if result.returncode != 0:
        print(f"ERROR: command failed with exit code {result.returncode}")
        sys.exit(result.returncode)


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

    # pyproject.toml [project] dependencies
    pyproject = os.path.join(clone_dir, "pyproject.toml")
    if os.path.exists(pyproject):
        with open(pyproject) as f:
            content = f.read()
        match = re.search(r'dependencies\s*=\s*\[(.*?)\]', content, re.DOTALL)
        if match:
            deps += re.findall(r'["\']([^"\']+)["\']', match.group(1))

    # setup.cfg install_requires
    setup_cfg = os.path.join(clone_dir, "setup.cfg")
    if os.path.exists(setup_cfg):
        with open(setup_cfg) as f:
            content = f.read()
        match = re.search(r'install_requires\s*=\s*((?:\n\s+\S.*)+)', content)
        if match:
            deps += [l.strip() for l in match.group(1).splitlines() if l.strip()]

    # setup.py install_requires
    setup_py = os.path.join(clone_dir, "setup.py")
    if os.path.exists(setup_py):
        with open(setup_py) as f:
            content = f.read()
        match = re.search(r'install_requires\s*=\s*\[(.*?)\]', content, re.DOTALL)
        if match:
            deps += re.findall(r'["\']([^"\']+)["\']', match.group(1))

    # requirements.txt
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
        "xbuild_tools": [],
        "before_build": "",
        "native":       False,
    }

    pyproject = os.path.join(clone_dir, "pyproject.toml")
    setup_py  = os.path.join(clone_dir, "setup.py")
    setup_cfg = os.path.join(clone_dir, "setup.cfg")

    pyproject_content = ""
    if os.path.exists(pyproject):
        with open(pyproject) as f:
            pyproject_content = f.read()

    setup_py_content = ""
    if os.path.exists(setup_py):
        with open(setup_py) as f:
            setup_py_content = f.read()

    setup_cfg_content = ""
    if os.path.exists(setup_cfg):
        with open(setup_cfg) as f:
            setup_cfg_content = f.read()

    combined = (pyproject_content + setup_py_content + setup_cfg_content).lower()

    # ── Detect build backend ──────────────────────────────────────
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

    # ── Detect extension languages / tools ───────────────────────

    if re.search(r'cython', combined):
        info["has_cython"] = True
    for root, dirs, files in os.walk(clone_dir):
        dirs[:] = [d for d in dirs if d not in
                   ('.git', 'docs', 'doc', 'tests', 'test', 'benchmarks')]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in ('.pyx', '.pxd'):
                info["has_cython"] = True
            if ext in ('.f', '.f90', '.f95', '.f03', '.for'):
                info["has_fortran"] = True
            if ext in ('.c', '.cpp', '.cxx', '.cc'):
                info["native"] = True

    if re.search(r'cffi', combined):
        info["has_cffi"] = True

    if re.search(r'pybind11', combined):
        info["has_pybind11"] = True

    if re.search(r'swig', combined):
        info["has_swig"] = True
    for root, dirs, files in os.walk(clone_dir):
        dirs[:] = [d for d in dirs if d not in ('.git', 'docs', 'doc')]
        for f in files:
            if f.endswith('.i'):
                info["has_swig"] = True

    if os.path.exists(os.path.join(clone_dir, "Cargo.toml")):
        info["has_rust"] = True
    if re.search(r'cargo|maturin|rustc', combined):
        info["has_rust"] = True

    # Propagate native flag
    if info["has_fortran"]:
        info["native"] = True
    if any([info["has_cython"], info["has_cffi"],
            info["has_pybind11"], info["has_swig"], info["has_rust"]]):
        info["native"] = True
    if info["backend"] in ("meson", "cmake", "rust"):
        info["native"] = True

    # ── Build xbuild_tools + before_build string ─────────────────
    xtools = []
    pkgs   = []

    if info["backend"] == "meson":
        xtools += ["meson", "ninja"]
        pkgs   += ["meson-python", "meson", "ninja"]
        if info["has_cython"]:
            pkgs += ["cython"]
        if info["has_pybind11"]:
            pkgs += ["pybind11"]

    elif info["backend"] == "cmake":
        xtools += ["cmake", "ninja"]
        pkgs   += ["scikit-build-core", "cmake", "ninja"]
        if info["has_cython"]:
            pkgs += ["cython"]
        if info["has_pybind11"]:
            pkgs += ["pybind11"]

    elif info["backend"] == "rust":
        xtools += ["rustc", "cargo"]
        pkgs   += ["maturin"]

    else:
        pkgs += ["setuptools", "wheel"]
        if info["has_cython"]:
            pkgs += ["cython"]
        if info["has_cffi"]:
            pkgs += ["cffi"]
        if info["has_pybind11"]:
            pkgs += ["pybind11"]
        if info["has_swig"]:
            xtools += ["swig"]
        if info["has_fortran"]:
            xtools += ["gfortran"]

    info["xbuild_tools"] = list(dict.fromkeys(xtools))
    info["before_build"] = f"pip install {' '.join(pkgs)}" if pkgs else ""

    return info


# ================================================================
# Wheel verify  (no rename needed — Chaquopy tags are correct)
# ================================================================

def verify_wheel(whl_path):
    """
    Checks:
      1. Filename contains a recognised arm64 tag
      2. Internal WHEEL metadata Tag line contains a recognised arm64 tag
      3. All .so ELF binaries are aarch64
    """
    print(f"\n  Verifying: {os.path.basename(whl_path)}")
    errors = []

    valid_arm64_tags = ("linux_aarch64", "android_arm64", "arm64_v8a")

    if not any(t in whl_path for t in valid_arm64_tags):
        errors.append("FAIL filename: no recognised arm64 tag in filename")
    else:
        print(f"  ✓ Filename tag OK")

    try:
        with zipfile.ZipFile(whl_path, "r") as z:
            # ── WHEEL metadata ────────────────────────────────────
            wheel_files = [n for n in z.namelist() if n.endswith("/WHEEL")]
            if not wheel_files:
                errors.append("FAIL metadata: no WHEEL file inside archive")
            else:
                content = z.read(wheel_files[0]).decode()
                # Collect ALL Tag lines (a wheel can have several)
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

            # ── ELF binary check ──────────────────────────────────
            so_files = [n for n in z.namelist() if n.endswith(".so")]
            if so_files:
                print(f"  Checking {len(so_files)} .so file(s)...")
                arch_map = {
                    0xB7: "aarch64 ✓",
                    0x3E: "x86_64  ✗ WRONG ARCH — will CRASH on Android",
                    0x28: "arm32   ✗ WRONG ARCH",
                    0x08: "mips    ✗ WRONG ARCH",
                    0x16: "ppc     ✗ WRONG ARCH",
                }
                for so in so_files:
                    data = z.read(so)
                    if len(data) >= 20 and data[:4] == b'\x7fELF':
                        e_machine = data[18]
                        desc = arch_map.get(
                            e_machine, f"unknown (0x{e_machine:02X}) ✗"
                        )
                        if "✗" in desc:
                            errors.append(
                                f"FAIL binary: {os.path.basename(so)} is {desc}"
                            )
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
# Retag Chaquopy wheel to cp313 so pip accepts it
# ================================================================

def retag_for_cp313(whl_path):
    """
    Chaquopy wheels are built with an older CPython (e.g. cp38) but the
    .so binaries are ABI-compatible with cp313 on Android.  pip refuses
    to install them because the filename/metadata tags don't match the
    running interpreter.

    This function rewrites:
      • The wheel filename  (cpXX-cpXX  →  cp313-cp313)
      • Every Tag: line inside the WHEEL metadata file

    The platform tag (android_21_arm64_v8a, etc.) and all other metadata
    are left untouched.
    """
    filename  = os.path.basename(whl_path)
    parts     = filename[:-4].split("-")   # strip .whl, split on -

    # Standard wheel name: {dist}-{ver}-{pyver}-{abi}-{platform}.whl
    # Chaquopy adds a build tag:  {dist}-{ver}-{build}-{pyver}-{abi}-{plat}.whl
    # Detect which layout we have and find the pyver/abi indices.
    if len(parts) == 5:
        pyver_idx, abi_idx = 2, 3
    elif len(parts) == 6:
        pyver_idx, abi_idx = 3, 4
    else:
        print(f"  WARNING: unexpected wheel name format, skipping retag: {filename}")
        return whl_path

    old_pyver = parts[pyver_idx]
    old_abi   = parts[abi_idx]

    if old_pyver == "cp313" and old_abi == "cp313":
        print(f"  Tags already cp313, no retag needed.")
        return whl_path

    parts[pyver_idx] = "cp313"
    parts[abi_idx]   = "cp313"
    new_filename = "-".join(parts) + ".whl"
    new_path     = os.path.join(os.path.dirname(whl_path), new_filename)

    print(f"  Retagging:  {filename}")
    print(f"         -->  {new_filename}")

    tmp_path = whl_path + ".tmp"
    with zipfile.ZipFile(whl_path, "r") as zin, \
         zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)

            # Rewrite the WHEEL metadata file
            if item.filename.endswith("/WHEEL"):
                text = data.decode()
                # Replace every Tag: cpXX-cpXX-<plat> with cp313-cp313-<plat>
                text = re.sub(
                    r"(Tag:\s*)cp\d+-(cp\d+|abi3|none)-",
                    r"\g<1>cp313-cp313-",
                    text
                )
                data = text.encode()

            # Rename the .dist-info directory if it contains the old pyver
            new_name = item.filename
            if old_pyver in item.filename:
                new_name = item.filename.replace(old_pyver, "cp313", 1)
            item.filename = new_name

            zout.writestr(item, data)

    os.remove(whl_path)
    os.rename(tmp_path, new_path)
    return new_path


# ================================================================
# Pure-Python wheel rename + verify
# ================================================================

def rename_to_android(whl_path):
    """
    Rewrites filename and WHEEL metadata to cp313-cp313-linux_aarch64.
    Only used for the pure-Python build path — Chaquopy wheels are
    already correctly tagged and must never be renamed.
    """
    TARGET_TAG = "cp313-cp313-linux_aarch64"
    filename = os.path.basename(whl_path)
    parts = filename[:-4].split("-")
    if len(parts) < 5:
        print(f"  WARNING: unexpected wheel filename: {filename}")
        return whl_path

    parts[2] = "cp313"
    parts[3] = "cp313"
    parts[4] = "linux_aarch64"
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
# Chaquopy fetch  (native packages)
# ================================================================

def _fetch_index_html(name):
    """
    Tries both hyphenated and underscored package name variants.
    Returns (html_text, index_slug) or exits on failure.
    """
    pkg_hyphen = name.lower()
    pkg_under  = name.replace("-", "_").lower()

    for slug in [pkg_hyphen, pkg_under]:
        url = f"{CHAQUOPY_INDEX}/{slug}/"
        print(f"  Fetching index: {url}")
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "install.py/1.0"}
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode(), slug
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            print(f"ERROR: HTTP {e.code} fetching Chaquopy index for '{name}'")
            sys.exit(1)
        except Exception as e:
            print(f"ERROR: network error fetching Chaquopy index for '{name}': {e}")
            sys.exit(1)

    print(f"ERROR: '{name}' not found on Chaquopy "
          f"(tried '{pkg_hyphen}' and '{pkg_under}')")
    sys.exit(1)


def _parse_api_level(wheel_filename):
    """
    Extracts the Android API level from a Chaquopy wheel filename.
    e.g. scikit_learn-1.3.2-0-cp310-cp310-android_21_arm64_v8a.whl -> 21
    Returns 0 if the pattern is absent.
    """
    m = re.search(r'android_(\d+)_arm64', wheel_filename)
    return int(m.group(1)) if m else 0


def chaquopy_fetch(name):
    """
    Downloads the best matching Android arm64 wheel from Chaquopy.

    Selection logic:
      1. Keep only arm64_v8a wheels.
      2. Discard wheels whose API level > ANDROID_API (would not run on device).
      3. Among the remaining, pick the highest API level
         (closest match without exceeding the device level).
      4. Python version tag is intentionally ignored — Chaquopy wheels are
         ABI-stable across CPython versions so cp310 runs fine on cp313.
         Filtering by Python version would only shrink the candidate pool.
    """
    html, index_slug = _fetch_index_html(name)

    # Parse all wheel hrefs, strip query strings / hash fragments
    all_wheels = re.findall(r'href="([^"]*\.whl[^"]*)"', html)
    all_wheels = [w.split("?")[0].split("#")[0] for w in all_wheels]

    # Keep only arm64_v8a wheels
    arm64_wheels = [w for w in all_wheels if "arm64_v8a" in w]
    if not arm64_wheels:
        print(f"ERROR: No arm64_v8a wheels found for '{name}' on Chaquopy")
        print(f"  All wheels listed: {[os.path.basename(w) for w in all_wheels[:15]]}")
        sys.exit(1)

    # Filter: API level must be <= device API
    compatible = [
        w for w in arm64_wheels
        if _parse_api_level(os.path.basename(w)) <= ANDROID_API
    ]
    if not compatible:
        print(f"ERROR: No arm64_v8a wheel with API level <= {ANDROID_API} "
              f"found for '{name}'")
        print(f"  arm64 wheels available: "
              f"{[os.path.basename(w) for w in arm64_wheels]}")
        sys.exit(1)

    # Pick highest API level that fits (best match for the device)
    compatible.sort(
        key=lambda w: _parse_api_level(os.path.basename(w)), reverse=True
    )
    chosen       = compatible[0]
    chosen_fname = os.path.basename(chosen)
    chosen_api   = _parse_api_level(chosen_fname)

    print(f"  Selected:       {chosen_fname}")
    print(f"  API level:      {chosen_api}  (device: Android {ANDROID_API})")

    # Build absolute download URL
    if chosen.startswith("http"):
        whl_url = chosen
    else:
        whl_url = f"{CHAQUOPY_INDEX}/{index_slug}/{chosen_fname}"

    whl_path = os.path.join(OUTPUT_DIR, chosen_fname)

    print(f"  Downloading ...")

    def _progress(block_num, block_size, total_size):
        if total_size > 0:
            pct = min(block_num * block_size * 100 // total_size, 100)
            print(f"\r  Progress:       {pct}%  ", end="", flush=True)

    urllib.request.urlretrieve(whl_url, whl_path, reporthook=_progress)
    print()  # newline after progress bar
    print(f"  Saved to:       {whl_path}")
    return whl_path


# ================================================================
# Build paths
# ================================================================

def build_pure(clone_dir, name):
    print(f"\n[3/4] Building pure-Python wheel...")
    run(["python", "-m", "build", "--wheel",
         "--outdir", os.path.abspath(OUTPUT_DIR), clone_dir])

    built = [
        os.path.join(OUTPUT_DIR, f) for f in os.listdir(OUTPUT_DIR)
        if f.endswith(".whl") and name.replace("-", "_").lower() in f.lower()
    ]
    if not built:
        print("ERROR: no wheel found after build")
        sys.exit(1)

    print(f"\n[4/4] Renaming + verifying...")
    for whl in built:
        final = rename_to_android(whl)
        verify_wheel(final)


def build_native(clone_dir, name, binfo):
    """
    Fetches a genuine Android arm64 wheel from Chaquopy.
    No cross-compiler, NDK, or cibuildwheel required.
    """
    print(f"\n[3/4] Fetching pre-built Android arm64 wheel from Chaquopy...")
    print(f"  Package:      {name}")
    print(f"  Backend:      {binfo['backend']}")
    print(f"  cython:       {binfo['has_cython']}")
    print(f"  cffi:         {binfo['has_cffi']}")
    print(f"  pybind11:     {binfo['has_pybind11']}")
    print(f"  swig:         {binfo['has_swig']}")
    print(f"  fortran:      {binfo['has_fortran']}")
    print(f"  rust:         {binfo['has_rust']}")

    whl_path = chaquopy_fetch(name)

    print(f"\n  Retagging wheel to cp313 so pip accepts it...")
    whl_path = retag_for_cp313(whl_path)

    print(f"\n[4/4] Verifying...")
    verify_wheel(whl_path)


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
        build_native(clone_dir, name, binfo)
    else:
        build_pure(clone_dir, name)

    print(f"\n✓ Done: {name}")


if __name__ == "__main__":
    for pkg_name in ["build"]:
        try:
            __import__(pkg_name.replace("-", "_"))
        except ImportError:
            print(f"Installing '{pkg_name}'...")
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", pkg_name]
            )

    build_package(REPO)

    print(f"\n{'='*55}")
    print(f"All wheels saved to: {os.path.abspath(OUTPUT_DIR)}/")
    for w in sorted(f for f in os.listdir(OUTPUT_DIR) if f.endswith(".whl")):
        print(f"  {w}")
