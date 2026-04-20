# ============================================================
# install.py
# Just set ONE repo. Everything else is automatic.
# ============================================================

import subprocess
import sys
import shutil
import os
import re
import zipfile

# ----------------------------------------------------------------
REPO = "https://github.com/scikit-learn/scikit-learn.git"
# ----------------------------------------------------------------

OUTPUT_DIR = "wheelhouse"
TARGET_TAG = "cp313-cp313-linux_aarch64"
CIBW_BUILD = "cp313-android_arm64_v8a"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def run(cmd, cwd=None, env=None):
    print(f"  $ {' '.join(cmd)}")
    e = {**os.environ, **(env or {})}
    result = subprocess.run(cmd, cwd=cwd, env=e)
    if result.returncode != 0:
        print(f"ERROR: command failed with exit code {result.returncode}")
        sys.exit(result.returncode)


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


def read_deps(clone_dir):
    deps = []
    for path, pattern in [
        ("pyproject.toml", r'dependencies\s*=\s*\[(.*?)\]'),
        ("setup.py",       r'install_requires\s*=\s*\[(.*?)\]'),
    ]:
        full = os.path.join(clone_dir, path)
        if os.path.exists(full):
            with open(full) as f:
                content = f.read()
            match = re.search(pattern, content, re.DOTALL)
            if match:
                deps += re.findall(r'["\']([^"\']+)["\']', match.group(1))

    setup_cfg = os.path.join(clone_dir, "setup.cfg")
    if os.path.exists(setup_cfg):
        with open(setup_cfg) as f:
            content = f.read()
        match = re.search(r'install_requires\s*=\s*((?:\n\s+\S.*)+)', content)
        if match:
            deps += [l.strip() for l in match.group(1).splitlines() if l.strip()]

    requirements = os.path.join(clone_dir, "requirements.txt")
    if os.path.exists(requirements):
        with open(requirements) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    deps.append(line)
    return deps


def has_c_extensions(clone_dir):
    for root, dirs, files in os.walk(clone_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.')
                   and d not in ('docs', 'doc', 'tests', 'test')]
        for f in files:
            if f.endswith(('.c', '.cpp', '.cxx', '.pyx', '.pxd')):
                rel = os.path.relpath(os.path.join(root, f), clone_dir)
                return True, f"found source file: {rel}"

    setup_py = os.path.join(clone_dir, "setup.py")
    if os.path.exists(setup_py):
        with open(setup_py) as f:
            content = f.read()
        if re.search(r'Extension\s*\(|Cython|cffi|ctypes', content):
            return True, "setup.py references Extension/Cython/cffi"

    pyproject = os.path.join(clone_dir, "pyproject.toml")
    if os.path.exists(pyproject):
        with open(pyproject) as f:
            content = f.read()
        if re.search(r'meson|cmake|scikit.build|ninja|cython|cffi|pybind11',
                     content, re.IGNORECASE):
            return True, "pyproject.toml references native build system"

    return False, "no C extensions detected"


def rename_to_android(whl_path):
    filename = os.path.basename(whl_path)
    parts = filename[:-4].split("-")
    if len(parts) < 5:
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


def verify_wheel(whl_path):
    print(f"\n  Verifying: {os.path.basename(whl_path)}")
    errors = []

    if "linux_aarch64" not in whl_path:
        errors.append("FAIL filename: 'linux_aarch64' not in filename")
    else:
        print(f"  ✓ Filename tag OK")

    try:
        with zipfile.ZipFile(whl_path, "r") as z:
            wheel_files = [n for n in z.namelist() if n.endswith("/WHEEL")]
            if not wheel_files:
                errors.append("FAIL metadata: no WHEEL file inside archive")
            else:
                content = z.read(wheel_files[0]).decode()
                tag_match = re.search(r"^Tag:\s*(.+)$", content, re.MULTILINE)
                if not tag_match:
                    errors.append("FAIL metadata: no Tag line in WHEEL file")
                elif "linux_aarch64" not in tag_match.group(1):
                    errors.append(f"FAIL metadata: Tag is '{tag_match.group(1).strip()}'")
                else:
                    print(f"  ✓ Metadata tag OK ({tag_match.group(1).strip()})")

            so_files = [n for n in z.namelist() if n.endswith(".so")]
            if so_files:
                print(f"  Checking {len(so_files)} .so file(s)...")
                for so in so_files:
                    data = z.read(so)
                    if len(data) >= 20 and data[:4] == b'\x7fELF':
                        e_machine = data[18]
                        if e_machine == 0xB7:
                            print(f"  ✓ {os.path.basename(so)} → aarch64")
                        elif e_machine == 0x3E:
                            errors.append(
                                f"FAIL binary: {os.path.basename(so)} is x86_64 — "
                                f"this will CRASH on Android. "
                                f"Package needs proper cross-compilation."
                            )
                        else:
                            errors.append(
                                f"FAIL binary: {os.path.basename(so)} "
                                f"unknown arch (0x{e_machine:02X})"
                            )
            else:
                print(f"  ✓ No .so files (pure Python)")

    except Exception as e:
        errors.append(f"FAIL: could not read zip — {e}")

    if errors:
        print()
        for err in errors:
            print(f"  ✗ {err}")
        print()
        sys.exit(1)

    print(f"  ✓ Wheel fully verified as android aarch64")


def build_pure(clone_dir, name):
    print(f"\n[3/4] Building pure Python wheel...")
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


def build_native(clone_dir, name):
    print(f"\n[3/4] Cross-compiling native wheel via cibuildwheel...")
    run(
        ["cibuildwheel", "--platform", "android", "--archs", "arm64_v8a", clone_dir],
        env={
            "CIBW_BUILD":          CIBW_BUILD,
            "CIBW_ARCHS_ANDROID":  "arm64_v8a",
            "CIBW_BUILD_FRONTEND": "build",
            "CIBW_OUTPUT_DIR":     os.path.abspath(OUTPUT_DIR),
        }
    )

    built = [
        os.path.join(OUTPUT_DIR, f) for f in os.listdir(OUTPUT_DIR)
        if f.endswith(".whl") and name.replace("-", "_").lower() in f.lower()
    ]
    if not built:
        print("ERROR: no wheel found after cibuildwheel build")
        sys.exit(1)

    print(f"\n[4/4] Verifying...")
    for whl in built:
        verify_wheel(whl)


def build_package(repo):
    clone_dir = "_src_" + repo.rstrip("/").split("/")[-1].replace(".git", "")

    print(f"\n{'='*55}")

    if os.path.exists(clone_dir):
        shutil.rmtree(clone_dir)

    print(f"[1/4] Cloning {repo} ...")
    run(["git", "clone", "--depth=1", repo, clone_dir])

    name = get_package_name(clone_dir)
    print(f"      Package name: {name}")

    print(f"\n[2/4] Dependencies:")
    deps = read_deps(clone_dir)
    for d in deps:
        print(f"    {d}")
    if not deps:
        print("    (none found)")

    native, reason = has_c_extensions(clone_dir)
    print(f"\n      Mode: {'native (cibuildwheel)' if native else 'pure Python'} — {reason}")

    if native:
        build_native(clone_dir, name)
    else:
        build_pure(clone_dir, name)

    print(f"\n✓ Done: {name}")


if __name__ == "__main__":
    for pkg_name in ["build", "cibuildwheel"]:
        try:
            __import__(pkg_name.replace("-", "_"))
        except ImportError:
            print(f"Installing '{pkg_name}'...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", pkg_name])

    build_package(REPO)

    print(f"\n{'='*55}")
    print(f"All wheels saved to: {os.path.abspath(OUTPUT_DIR)}/")
    for w in sorted(f for f in os.listdir(OUTPUT_DIR) if f.endswith(".whl")):
        print(f"  {w}")
