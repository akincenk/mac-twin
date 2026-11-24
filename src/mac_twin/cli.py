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
from collections.abc import Callable
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


def run(cmd: list[str], text: bool = True, ok: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    """Run without a shell; exit codes outside `ok` raise CalledProcessError."""
    res = subprocess.run(cmd, check=False, capture_output=True, text=text)
    if res.returncode not in ok:
        raise subprocess.CalledProcessError(res.returncode, cmd, res.stdout, res.stderr)
    return res


def check_dotfile(rel: object) -> str:
    if not isinstance(rel, str) or not rel.strip():
        raise ConfigError(f"dotfile entry must be a non-empty string: {rel!r}")
    p = Path(rel.removeprefix("~/"))
    if not p.parts or p.parts[0].startswith("~"):
        raise ConfigError(f"dotfile must be a path inside your home folder, like .zshrc or ~/.zshrc: {rel}")
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


def plan_apply(folder: Path, only: set[str]) -> list[tuple[str, Callable[[], object]]]:
    """Work out what apply would change; return (description, action) pairs. Changes nothing."""
    domains, dotfiles = load_config(folder)
    home = Path.home()
    steps: list[tuple[str, Callable[[], object]]] = []
    brewfile = folder / "Brewfile"
    if "brew" in only and brewfile.is_file() and available("brew"):
        # `brew bundle check` exits 1 and lists what is missing on stderr; that is the answer, not an error.
        res = run(["brew", "bundle", "check", "--file", str(brewfile), "--verbose"], ok=(0, 1))
        if res.returncode == 0:
            print("brew: nothing to install")
        else:
            print("\n".join(s for s in (res.stdout.strip(), res.stderr.strip()) if s))
            steps.append(("run brew bundle install",
                          lambda: run(["brew", "bundle", "install", "--file", str(brewfile)])))
    if "defaults" in only:
        for d in domains:
            plist = folder / "defaults" / f"{d}.plist"
            if not plist.is_file():
                print(f"skip: {plist} missing")
                continue
            current = plistlib.loads(run(["defaults", "export", d, "-"], text=False).stdout)
            if plistlib.loads(plist.read_bytes()) == current:
                continue
            steps.append((f"import defaults {d}", lambda d=d, plist=plist: run(["defaults", "import", d, str(plist)])))
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
            steps.append((f"write ~/{rel}", lambda rel=rel, src=src, dest=dest: write_dotfile(src, dest, backup_root / rel)))
    if "mas" in only and (folder / "mas.txt").is_file() and available("mas"):
        installed = parse_mas_list(run(["mas", "list"]).stdout)
        for app_id, name in read_mas_file(folder / "mas.txt").items():
            if app_id not in installed:
                steps.append((f"install {name} ({app_id})", lambda app_id=app_id: run(["mas", "install", app_id])))
    return steps


def write_dotfile(src: Path, dest: Path, backup: Path) -> None:
    if dest.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dest, backup)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)


def cmd_apply(folder: Path, only: set[str], dry_run: bool, yes: bool) -> int:
    steps = plan_apply(folder, only)
    for text, _ in steps:
        print(f"would {text}")
    if dry_run:
        return 0
    if not steps:
        print("nothing to apply")
        return 0
    if not yes:
        if not sys.stdin.isatty():
            sys.stdout.flush()  # keep the summary above the error when output is piped
            raise ConfigError("not asking for confirmation without a terminal; pass --yes to apply")
        try:
            answer = input(f"apply {len(steps)} change(s)? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("nothing changed")
            return 1
    for text, action in steps:
        action()
        print(text)
    if any(text.startswith("import defaults ") for text, _ in steps):
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
            p.add_argument("-y", "--yes", action="store_true", help="apply without asking for confirmation")
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
            return cmd_apply(args.folder, only, args.dry_run, args.yes)
        return cmd_diff(args.folder, only)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        print(f"error: {' '.join(e.cmd)} failed: {err.strip()}", file=sys.stderr)
        return 2
