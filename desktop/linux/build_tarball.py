#!/usr/bin/env python3
"""build_tarball.py — the Linux release tarball (#962).

    python3 desktop/linux/build_tarball.py [--ref v2.1.7] [--out dist]

Writes ``SlyLED-<ver>-linux.tar.gz`` and ``SlyLED-<ver>-linux.tar.gz.sha256``
(``sha256sum -c`` format). The contents are exactly what ``install.sh``
installs, under a top-level ``SlyLED-<ver>/`` directory, plus a ``VERSION``
file and ``desktop/linux/install.sh`` at the same relative path, so

    tar xzf SlyLED-<ver>-linux.tar.gz
    sudo bash SlyLED-<ver>/desktop/linux/install.sh

works with no git checkout.

Built from **git** at ``--ref`` (default HEAD) with ``git archive`` — never the
working tree — so gitignored bulk such as ``firmware/orangepi/models/``
(~200 MB of camera models) and any local edits can't leak into a release.
The version comes from ``parent_server.py`` at that ref (same regex as
``desktop/windows/build.py``); this script never bumps it.

Architecture-neutral: the app is pure Python; wheels are installed by pip at
install time (the Docker image is the pinned/offline artifact).
"""

import argparse
import gzip
import hashlib
import io
import os
import re
import subprocess
import sys
import tarfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Kept in step with install.sh's `paths` list (tests/test_linux_packaging.py
# asserts the two agree). docs/build is produced by tools/docs/build.py and is
# never tracked; /help falls back to scanning USER_MANUAL.md without it.
PATHS = [
    "desktop/shared", "desktop/linux", "firmware/registry.json", "firmware/orangepi",
    "docs/help", "docs/schema",
    "docs/USER_MANUAL.md", "docs/USER_MANUAL_fr.md",
    "docs/USER_MANUAL.pdf", "docs/USER_MANUAL_fr.pdf",
    "docs/USER_MANUAL.docx", "docs/USER_MANUAL_fr.docx",
]
# Tracked but not wanted on a target host.
EXCLUDE_PREFIXES = ("desktop/shared/data/", "firmware/orangepi/models/")

VERSION_RE = re.compile(r'^VERSION\s*=\s*"(\d+\.\d+\.\d+)"', re.M)


def git(*args, binary=False):
    r = subprocess.run(["git", "-C", ROOT, *args], capture_output=True, check=True)
    return r.stdout if binary else r.stdout.decode("utf-8")


def version_at(ref):
    src = git("show", f"{ref}:desktop/shared/parent_server.py")
    m = VERSION_RE.search(src)
    if not m:
        raise SystemExit("VERSION not found in parent_server.py at " + ref)
    return m.group(1)


def build(ref="HEAD", out="dist"):
    ver = version_at(ref)
    commit = git("rev-parse", f"{ref}^{{commit}}").strip()
    mtime = int(git("show", "-s", "--format=%ct", commit).strip())
    tracked = [p for p in PATHS if git("ls-tree", "-r", "--name-only", commit, "--", p).strip()]
    raw = git("archive", "--format=tar", commit, "--", *tracked, binary=True)
    top = f"SlyLED-{ver}"
    name = f"SlyLED-{ver}-linux.tar.gz"
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, name)

    src = tarfile.open(fileobj=io.BytesIO(raw), mode="r:")
    buf = io.BytesIO()
    count = 0
    # Deterministic: gzip header mtime + member mtimes = the commit time.
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=mtime) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as dst:
            for m in src.getmembers():
                if m.name.startswith(EXCLUDE_PREFIXES) or "/__pycache__/" in m.name:
                    continue
                data = src.extractfile(m) if m.isfile() else None
                m.name = f"{top}/{m.name}"
                m.mtime = mtime
                m.uid = m.gid = 0
                m.uname = m.gname = "root"
                dst.addfile(m, data)
                count += 1
            vb = f"{ver}\n".encode()
            vi = tarfile.TarInfo(f"{top}/VERSION")
            vi.size, vi.mtime, vi.mode = len(vb), mtime, 0o644
            dst.addfile(vi, io.BytesIO(vb))
            cb = f"{commit}\n".encode()
            ci = tarfile.TarInfo(f"{top}/COMMIT")
            ci.size, ci.mtime, ci.mode = len(cb), mtime, 0o644
            dst.addfile(ci, io.BytesIO(cb))
    data = buf.getvalue()
    with open(path, "wb") as fp:
        fp.write(data)
    digest = hashlib.sha256(data).hexdigest()
    with open(path + ".sha256", "w", encoding="ascii", newline="\n") as fp:
        fp.write(f"{digest}  {name}\n")
    return {"version": ver, "commit": commit, "path": path, "sha256": digest,
            "bytes": len(data), "members": count}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ref", default="HEAD", help="git ref to package (a v<ver> tag in CI)")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    a = ap.parse_args()
    r = build(a.ref, a.out)
    print(f"{r['path']}  v{r['version']}  {r['bytes']} bytes  {r['members']} files  "
          f"commit {r['commit'][:12]}\nsha256 {r['sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
