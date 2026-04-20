# ============================================================
# install.py
# Edit PACKAGES below — just provide the repo URL.
# Wheels are forced to android_aarch64 + verified after build.
# ============================================================

import subprocess
import sys
import shutil
import os
import re
import zipfile

# ----------------------------------------------------------------
# CONFIGURE YOUR PACKAGES HERE
# ----------------------------------------------------------------
PACKAGES = [
    {"repo": "https://github.com/scikit-learn/scikit-learn.git"},
    # {"repo": "https://github.com/huggingface/tokenizers.git"},
]
# ----------------------------------------------------------------

OUTPUT_DIR = "wheelhouse"
TARGET_TAG = "cp313-cp313-linux_aarch64"  # forced wheel tag
os.makedirs(OUTPUT_DIR, exist_ok=True)


def run(cmd, cwd=None):
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd)
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

    pyproject = os.path.join(clone_dir, "pyproject.toml")
    setup_cfg = os.path.join(clone_dir, "setup.cfg")
    setup_py  = os.path.join(clone_dir, "setup.py")
    requirements = os.path.join(clone_dir, "requirements.txt")

    if os.path.exists(pyproject):
        with open(pyproject) as f:
            content = f.read()
        match = re.search(r'dependencies\s*=\s*\[(.*?)\]', content, re.DOTALL)
        if match:
            deps += re.findall(r'["\']([^"\']+)["\']', match.group(1))

    if os.path.exists(setup_cfg):
        with open(setup_cfg) as f:
            content = f.read()
        match = re.search(r'install_requires\s*=\s*((?:\n\s+\S.*)+)', content)
        if match:
            deps += [l.strip() for l in match.group(1).splitlines() if l.strip()]

    if os.path.exists(setup_py):
        with open(setup_py) as f:
            content = f.read()
        match = re.search(r'install_requires\s*=\s*\[(.*?)\]', content, re.DOTALL)
        if match:
            deps += re.findall(r'["\']([^"\']+)["\']', match.group(1))

    if os.path.exists(requirements):
        with open(requirements) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    deps.append(line)

    return deps


def rename_to_android(whl_path):
    """Rename wheel file and patch WHEEL metadata inside to android tag."""
    filename = os.path.basename(whl_path)
    # wheel filename: {dist}-{version}-{pytag}-{abitag}-{platformtag}.whl
    parts = filename[:-4].split("-")  # strip .whl, split by -
    if len(parts) < 5:
        print(f"  WARNING: unexpected wheel filename format: {filename}")
        return whl_path

    parts[2] = "cp313"         # python tag
    parts[3] = "cp313"         # abi tag
    parts[4] = "linux_aarch64" # platform tag
    new_filename = "-".join(parts) + ".whl"
    new_path = os.path.join(os.path.dirname(whl_path), new_filename)

    # Repack zip with patched WHEEL metadata
    tmp_path = whl_path + ".tmp"
    with zipfile.ZipFile(whl_path, "r") as zin, \
         zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.endswith("/WHEEL"):
                text = data.decode("utf-8")
                text = re.sub(r"Tag:.*", f"Tag: {TARGET_TAG}", text)
                data = text.encode("utf-8")
            zout.writestr(item, data)

    os.remove(whl_path)
    os.rename(tmp_path, new_path)
    print(f"  Renamed: {filename}")
    print(f"       --> {new_filename}")
    return new_path


def verify_wheel(whl_path):
    """
    Two checks:
    1. Filename contains linux_aarch64
    2. WHEEL metadata inside confirms Tag: cp313-cp313-linux_aarch64
    """
    print(f"\n  Verifying: {os.path.basename(whl_path)}")
    errors = []

    # Check 1: filename
    if "linux_aarch64" not in whl_path:
        errors.append("FAIL filename: 'linux_aarch64' not in filename")
    else:
        print(f"  ✓ Filename tag OK")

    # Check 2: WHEEL metadata inside zip
    try:
        with zipfile.ZipFile(whl_path, "r") as z:
            wheel_files = [n for n in z.namelist() if n.endswith("/WHEEL")]
            if not wheel_files:
                errors.append("FAIL metadata: no WHEEL file found inside archive")
            else:
                content = z.read(wheel_files[0]).decode("utf-8")
                tag_match = re.search(r"^Tag:\s*(.+)$", content, re.MULTILINE)
                if not tag_match:
                    errors.append("FAIL metadata: no Tag line found in WHEEL file")
                elif "linux_aarch64" not in tag_match.group(1):
                    errors.append(f"FAIL metadata: Tag is '{tag_match.group(1).strip()}', expected linux_aarch64")
                else:
                    print(f"  ✓ Metadata tag OK ({tag_match.group(1).strip()})")
    except Exception as e:
        errors.append(f"FAIL metadata: could not read zip — {e}")

    if errors:
        print()
        for e in errors:
            print(f"  ✗ {e}")
        print()
        sys.exit(1)

    print(f"  ✓ Wheel verified as android aarch64")


def build_package(pkg):
    repo = pkg["repo"]
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
    if deps:
        for d in deps:
            print(f"    {d}")
    else:
        print("    (none found)")

    print(f"\n[3/4] Building wheel...")
    run(["python", "-m", "build", "--wheel", "--outdir", os.path.abspath(OUTPUT_DIR), clone_dir])

    print(f"\n[4/4] Renaming + verifying wheel...")
    built = [
        os.path.join(OUTPUT_DIR, f)
        for f in os.listdir(OUTPUT_DIR)
        if f.endswith(".whl") and name.replace("-", "_").lower() in f.lower()
    ]
    if not built:
        print("ERROR: no wheel found in output dir after build")
        sys.exit(1)

    for whl in built:
        final = rename_to_android(whl)
        verify_wheel(final)

    print(f"\n✓ Done: {name}")


if __name__ == "__main__":
    try:
        import build  # noqa
    except ImportError:
        print("Installing 'build'...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "build"])

    for pkg in PACKAGES:
        build_package(pkg)

    print(f"\n{'='*55}")
    print(f"All wheels saved to: {os.path.abspath(OUTPUT_DIR)}/")
    for w in sorted(f for f in os.listdir(OUTPUT_DIR) if f.endswith(".whl")):
        print(f"  {w}")
