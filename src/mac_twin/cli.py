"""Capture a Mac's setup into a folder and apply it on another Mac."""

from __future__ import annotations

import argparse
import difflib
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

CONFIG_NAME = "mac-twin.toml"
DEFAULT_CONFIG = """\
# What mac-twin captures. Dotfile paths are relative to your home folder.
domains = ["com.apple.dock", "com.apple.finder"]
dotfiles = [".zshrc", ".gitconfig"]
"""
DOMAIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# Files that usually hold credentials. mac-twin refuses to copy them into a folder meant for git.
SECRET_PATHS = (
    ".ssh", ".gnupg", ".aws", ".kube", ".netrc", ".npmrc", ".pypirc",
    ".docker/config.json", ".config/gh/hosts.yml",
)
SECTIONS = ("brew", "defaults", "dotfiles", "mas")


class ConfigError(Exception):
    pass


def run(cmd: list[str], text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=text)


def check_dotfile(rel: object) -> str:
    if not isinstance(rel, str) or not rel.strip():
        raise ConfigError(f"dotfile entry must be a non-empty string: {rel!r}")
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts:
        raise ConfigError(f"dotfile must be a path inside your home folder: {rel}")
    norm = p.as_posix()
    if any(norm == s or norm.startswith(s + "/") for s in SECRET_PATHS):
        raise ConfigError(f"refusing {rel}: it usually contains secrets")
    return norm


def load_config(folder: Path) -> tuple[list[str], list[str]]:
    path = folder / CONFIG_NAME
    if not path.is_file():
        raise ConfigError(f"{path} not found; run 'mac-twin init {folder}' first")
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e
    domains = data.get("domains", [])
    dotfiles = data.get("dotfiles", [])
    if not isinstance(domains, list) or not isinstance(dotfiles, list):
        raise ConfigError(f"{path}: domains and dotfiles must be lists")
    for d in domains:
        if not isinstance(d, str) or not DOMAIN_RE.match(d):
            raise ConfigError(f"invalid defaults domain: {d!r}")
    return domains, [check_dotfile(f) for f in dotfiles]


def parse_mas_list(output: str) -> dict[str, str]:
    """Turn `mas list` output ("497799835  Xcode  (15.0)") into {id: name}."""
    apps = {}
    for line in output.splitlines():
        m = re.match(r"^\s*(\d+)\s+(.+?)\s+\(.*\)\s*$", line)
        if m:
            apps[m.group(1)] = m.group(2)
    return apps


def read_mas_file(path: Path) -> dict[str, str]:
    apps = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            app_id, _, name = line.partition(" ")
            if app_id.isdigit():
                apps[app_id] = name
    return apps


def brewfile_lines(text: str) -> set[str]:
    return {ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")}


def dump_brewfile(dest: Path) -> None:
    run(["brew", "bundle", "dump", "--file", str(dest), "--force", "--no-mas"])


def available(tool: str) -> bool:
    if shutil.which(tool):
        return True
    print(f"skip: {tool} not found")
    return False


# --- commands ---------------------------------------------------------------

def cmd_init(folder: Path) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    cfg = folder / CONFIG_NAME
    if cfg.exists():
        print(f"{cfg} already exists")
        return 0
    cfg.write_text(DEFAULT_CONFIG)
    print(f"wrote {cfg}; edit it, then run 'mac-twin capture {folder}'")
    return 0


def cmd_capture(folder: Path, only: set[str]) -> int:
    domains, dotfiles = load_config(folder)
    home = Path.home()
    if "brew" in only and available("brew"):
        dump_brewfile(folder / "Brewfile")
        print("captured Brewfile")
    if "defaults" in only:
        (folder / "defaults").mkdir(exist_ok=True)
        for d in domains:
            out = run(["defaults", "export", d, "-"], text=False).stdout
            (folder / "defaults" / f"{d}.plist").write_bytes(out)
            print(f"captured defaults {d}")
    if "dotfiles" in only:
        for rel in dotfiles:
            src = home / rel
            if not src.is_file():
                print(f"skip: ~/{rel} is not a file")
                continue
            dest = folder / "dotfiles" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            print(f"captured ~/{rel}")
    if "mas" in only and available("mas"):
        apps = parse_mas_list(run(["mas", "list"]).stdout)
        (folder / "mas.txt").write_text("".join(f"{i} {n}\n" for i, n in sorted(apps.items())))
        print(f"captured {len(apps)} App Store apps")
    return 0


def cmd_apply(folder: Path, only: set[str], dry_run: bool) -> int:
    domains, dotfiles = load_config(folder)
    home = Path.home()
    prefix = "would " if dry_run else ""
    brewfile = folder / "Brewfile"
    if "brew" in only and brewfile.is_file() and available("brew"):
        if dry_run:
            # `brew bundle check` exits non-zero when something is missing; that is the answer, not an error.
            res = subprocess.run(["brew", "bundle", "check", "--file", str(brewfile), "--verbose"],
                                 check=False, capture_output=True, text=True)
            print(res.stdout.strip() or "brew: nothing to install")
        else:
            run(["brew", "bundle", "install", "--file", str(brewfile)])
            print("brew bundle install done")
    if "defaults" in only:
        for d in domains:
            plist = folder / "defaults" / f"{d}.plist"
            if not plist.is_file():
                print(f"skip: {plist} missing")
                continue
            if not dry_run:
                run(["defaults", "import", d, str(plist)])
            print(f"{prefix}import defaults {d}")
    if "dotfiles" in only:
        backup_root = home / ".mac-twin-backup" / time.strftime("%Y%m%d-%H%M%S")
        for rel in dotfiles:
            src = folder / "dotfiles" / rel
            dest = home / rel
            if not src.is_file():
                print(f"skip: {src} missing")
                continue
            if dest.is_file() and dest.read_bytes() == src.read_bytes():
                continue
            if not dry_run:
                if dest.exists():
                    backup = backup_root / rel
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(dest, backup)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest)
            print(f"{prefix}write ~/{rel}")
    if "mas" in only and (folder / "mas.txt").is_file() and available("mas"):
        installed = parse_mas_list(run(["mas", "list"]).stdout)
        for app_id, name in read_mas_file(folder / "mas.txt").items():
            if app_id in installed:
                continue
            if not dry_run:
                run(["mas", "install", app_id])
            print(f"{prefix}install {name} ({app_id})")
    if "defaults" in only and domains and not dry_run:
        print("some defaults only take effect after logging out or restarting the app (e.g. killall Dock)")
    return 0


def diff_dicts(a: dict, b: dict) -> list[str]:
    """Key-level differences between the saved plist (a) and this Mac (b)."""
    out = []
    for k in sorted(a.keys() | b.keys()):
        if k not in b:
            out.append(f"  + {k} = {a[k]!r}")
        elif k not in a:
            out.append(f"  - {k} = {b[k]!r}")
        elif a[k] != b[k]:
            out.append(f"  ~ {k}: {b[k]!r} -> {a[k]!r}")
    return out


def cmd_diff(folder: Path, only: set[str]) -> int:
    domains, dotfiles = load_config(folder)
    home = Path.home()
    changes = 0
    brewfile = folder / "Brewfile"
    if "brew" in only and brewfile.is_file() and available("brew"):
        with tempfile.TemporaryDirectory() as tmp:
            current = Path(tmp) / "Brewfile"
            dump_brewfile(current)
            here = brewfile_lines(current.read_text())
        saved = brewfile_lines(brewfile.read_text())
        for ln in sorted(saved - here):
            print(f"brew + {ln}")
        for ln in sorted(here - saved):
            print(f"brew - {ln}")
        changes += len(saved ^ here)
    if "defaults" in only:
        for d in domains:
            plist = folder / "defaults" / f"{d}.plist"
            if not plist.is_file():
                continue
            saved = plistlib.loads(plist.read_bytes())
            current = plistlib.loads(run(["defaults", "export", d, "-"], text=False).stdout)
            lines = diff_dicts(saved, current)
            if lines:
                print(f"defaults {d}:")
                print("\n".join(lines))
                changes += len(lines)
    if "dotfiles" in only:
        for rel in dotfiles:
            src = folder / "dotfiles" / rel
            if not src.is_file():
                continue
            dest = home / rel
            mine = dest.read_text(errors="replace").splitlines(True) if dest.is_file() else []
            saved = src.read_text(errors="replace").splitlines(True)
            lines = list(difflib.unified_diff(mine, saved, f"~/{rel} (this Mac)", f"{rel} (saved)"))
            if lines:
                sys.stdout.writelines(lines)
                changes += 1
    if "mas" in only and (folder / "mas.txt").is_file() and available("mas"):
        installed = parse_mas_list(run(["mas", "list"]).stdout)
        for app_id, name in read_mas_file(folder / "mas.txt").items():
            if app_id not in installed:
                print(f"mas + {name} ({app_id})")
                changes += 1
    print(f"{changes} difference(s)")
    return 1 if changes else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mac-twin", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "capture", "apply", "diff"):
        p = sub.add_parser(name)
        p.add_argument("folder", type=Path)
        if name != "init":
            p.add_argument("--only", default=",".join(SECTIONS),
                           help=f"comma-separated sections: {','.join(SECTIONS)}")
        if name == "apply":
            p.add_argument("--dry-run", action="store_true", help="print what would change, change nothing")
    args = parser.parse_args(argv)
    if args.command == "init":
        return cmd_init(args.folder)
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    if unknown := only - set(SECTIONS):
        parser.error(f"unknown section(s): {', '.join(sorted(unknown))}")
    try:
        if args.command == "capture":
            return cmd_capture(args.folder, only)
        if args.command == "apply":
            return cmd_apply(args.folder, only, args.dry_run)
        return cmd_diff(args.folder, only)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        print(f"error: {' '.join(e.cmd)} failed: {err.strip()}", file=sys.stderr)
        return 2
