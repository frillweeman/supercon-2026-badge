#!/usr/bin/env python3
"""Sync this directory to the badge's MicroPython filesystem (create/update/delete).

Compares SHA-256 of every file on the badge against the local copy and only
transfers what differs. Dry run by default; nothing is written without --apply.

  tools/sync.py                 # show what would change
  tools/sync.py --apply         # perform it
  tools/sync.py --apply --reset # ...then reset the badge
  tools/sync.py --no-delete     # never remove remote-only files

Only SYNC_ROOTS are considered, on both sides. Files the badge rewrites itself
are in IGNORE and are never copied or deleted (panel_config.json holds the boot
counter; user_apps.py and app_list are regenerated every boot).

Note: listing the badge uses `mpremote exec`, which interrupts main.py's running
tasks (no reset). Run --reset afterwards, or press the reset button.
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYNC_ROOTS = ["main.py", "boot.py", "apps", "lib", "data"]
IGNORE = {
    "/data/panel_config.json",
    "/data/user_apps.py",
    "/data/app_list",
}
SKIP_NAMES = {"__pycache__", ".DS_Store"}
DEFAULT_PORT = "/dev/cu.usbmodem1101"

REMOTE_LISTER = r"""
import os, gc
try:
    import hashlib, binascii
except ImportError:
    hashlib = None
def walk(d):
    for e in os.ilistdir(d):
        n, t = e[0], e[1]
        p = d.rstrip('/') + '/' + n
        if t == 0x4000:
            print('D', p)
            walk(p)
        else:
            sz = os.stat(p)[6]
            hx = '-'
            if hashlib:
                h = hashlib.sha256()
                f = open(p, 'rb')
                while True:
                    b = f.read(512)
                    if not b:
                        break
                    h.update(b)
                f.close()
                hx = binascii.hexlify(h.digest()).decode()
                gc.collect()
            print('F', p, sz, hx)
for r in %r:
    try:
        os.stat('/' + r)
    except OSError:
        continue
    if os.stat('/' + r)[0] & 0x4000:
        print('D', '/' + r)
        walk('/' + r)
    else:
        walk_file = '/' + r
        sz = os.stat(walk_file)[6]
        hx = '-'
        if hashlib:
            h = hashlib.sha256()
            f = open(walk_file, 'rb')
            while True:
                b = f.read(512)
                if not b:
                    break
                h.update(b)
            f.close()
            hx = binascii.hexlify(h.digest()).decode()
        print('F', walk_file, sz, hx)
"""


def find_mpremote():
    exe = shutil.which("mpremote") or os.path.expanduser("~/.local/bin/mpremote")
    if not os.path.exists(exe):
        sys.exit("mpremote not found (expected on PATH or ~/.local/bin)")
    return exe


def local_files():
    """{'/apps/x.py': (size, sha256)} and set of dirs, for SYNC_ROOTS."""
    files, dirs = {}, set()

    def add(path):
        rel = "/" + os.path.relpath(path, ROOT).replace(os.sep, "/")
        with open(path, "rb") as f:
            data = f.read()
        files[rel] = (len(data), hashlib.sha256(data).hexdigest())

    for r in SYNC_ROOTS:
        p = os.path.join(ROOT, r)
        if os.path.isfile(p):
            add(p)
        elif os.path.isdir(p):
            dirs.add("/" + r)
            for dp, dn, fn in os.walk(p):
                dn[:] = [d for d in dn if d not in SKIP_NAMES]
                for d in dn:
                    dirs.add("/" + os.path.relpath(os.path.join(dp, d), ROOT).replace(os.sep, "/"))
                for f in fn:
                    if f not in SKIP_NAMES:
                        add(os.path.join(dp, f))
    return files, dirs


def remote_files(mpremote, port):
    code = REMOTE_LISTER % (SYNC_ROOTS,)
    res = subprocess.run(
        [mpremote, "connect", port, "exec", code], capture_output=True, text=True
    )
    if res.returncode != 0:
        sys.exit(f"mpremote exec failed:\n{res.stdout}{res.stderr}")
    files, dirs = {}, set()
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "D":
            dirs.add(parts[1])
        elif len(parts) == 4 and parts[0] == "F":
            files[parts[1]] = (int(parts[2]), parts[3])
    return files, dirs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=DEFAULT_PORT)
    ap.add_argument("--apply", action="store_true", help="actually change the badge")
    ap.add_argument("--no-delete", action="store_true", help="skip deleting remote-only files")
    ap.add_argument("--reset", action="store_true", help="reset the badge afterwards (with --apply)")
    args = ap.parse_args()

    mpremote = find_mpremote()
    lf, ldirs = local_files()
    rf, rdirs = remote_files(mpremote, args.port)
    lf = {p: v for p, v in lf.items() if p not in IGNORE}
    rf = {p: v for p, v in rf.items() if p not in IGNORE}

    create = sorted(p for p in lf if p not in rf)
    update = []
    for p in sorted(lf):
        if p in rf:
            (ls, lh), (rs, rh) = lf[p], rf[p]
            if ls != rs or (rh != "-" and lh != rh):
                update.append(p)
    delete = [] if args.no_delete else sorted(p for p in rf if p not in lf)
    mkdirs = sorted(d for d in ldirs if d not in rdirs)  # parents sort before children
    rmdirs = [] if args.no_delete else sorted((d for d in rdirs if d not in ldirs), reverse=True)

    for tag, items in (("mkdir ", mkdirs), ("create", create), ("update", update),
                       ("delete", delete), ("rmdir ", rmdirs)):
        for p in items:
            print(f"{tag}  {p}")
    unchanged = len(lf) - len(create) - len(update)
    print(f"\n{len(create)} create, {len(update)} update, {len(delete)} delete, {unchanged} unchanged")

    if not (mkdirs or create or update or delete or rmdirs):
        return
    if not args.apply:
        print("dry run: re-run with --apply to make these changes")
        return

    cmd = [mpremote, "connect", args.port]
    first = True

    def add(*c):
        nonlocal first
        if not first:
            cmd.append("+")
        cmd.extend(c)
        first = False

    for d in mkdirs:
        add("fs", "mkdir", ":" + d)
    for p in create + update:
        add("fs", "cp", os.path.join(ROOT, p.lstrip("/")), ":" + p)
    for p in delete:
        add("fs", "rm", ":" + p)
    for d in rmdirs:
        add("fs", "rmdir", ":" + d)
    if args.reset:
        add("reset")
    sys.exit(subprocess.run(cmd).returncode)


if __name__ == "__main__":
    main()
