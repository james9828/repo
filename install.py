# ============================================================
# install.py
# Edit PACKAGES below — just provide the repo URL.
# ============================================================

import subprocess
import sys
import shutil
import os
import re

# ----------------------------------------------------------------
# CONFIGURE YOUR PACKAGES HERE
# ----------------------------------------------------------------
PACKAGES = [
    {"repo": "https://github.com/huggingface/datasets.git"},
    # {"repo": "https://github.com/huggingface/tokenizers.git"},
]
# ----------------------------------------------------------------

OUTPUT_DIR = "wheelhouse"
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
    setup_py = os.path.join(clone_dir, "setup.py")
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


def build_package(pkg):
    repo = pkg["repo"]
    clone_dir = "_src_" + repo.rstrip("/").split("/")[-1].replace(".git", "")

    print(f"\n{'='*55}")

    if os.path.exists(clone_dir):
        shutil.rmtree(clone_dir)

    print(f"[1/3] Cloning {repo} ...")
    run(["git", "clone", "--depth=1", repo, clone_dir])

    name = get_package_name(clone_dir)
    print(f"      Package name: {name}")

    print(f"\n[2/3] Dependencies:")
    deps = read_deps(clone_dir)
    if deps:
        for d in deps:
            print(f"    {d}")
    else:
        print("    (none found)")

    print(f"\n[3/3] Building wheel...")
    run(["python", "-m", "build", "--wheel", "--outdir", os.path.abspath(OUTPUT_DIR), clone_dir])

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
    for w in [f for f in os.listdir(OUTPUT_DIR) if f.endswith(".whl")]:
        print(f"  {w}")
