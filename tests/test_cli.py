import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from mac_twin import cli

MAS_LIST = "497799835  Xcode       (15.0)\n409183694  Keynote     (14.1)\n"


class FakeMac:
    """Stands in for brew, defaults and mas so tests never touch the real system."""

    def __init__(self):
        self.calls = []
        self.defaults = {"com.apple.dock": {"autohide": True, "tilesize": 48}}
        self.brewfile = 'tap "homebrew/core"\nbrew "git"\ncask "firefox"\n'
        self.mas = MAS_LIST
        self.bundle_check = (0, "")  # (exit code, stderr) for `brew bundle check`

    def run(self, cmd, text=True, ok=(0,)):
        self.calls.append(cmd)
        out = ""
        if cmd[:3] == ["brew", "bundle", "check"]:
            code, err = self.bundle_check
            if code not in ok:
                raise subprocess.CalledProcessError(code, cmd, "", err)
            return subprocess.CompletedProcess(cmd, code, "", err)
        if cmd[:3] == ["brew", "bundle", "dump"]:
            Path(cmd[cmd.index("--file") + 1]).write_text(self.brewfile)
        elif cmd[:2] == ["defaults", "export"]:
            out = plistlib.dumps(self.defaults.get(cmd[2], {}))
        elif cmd == ["mas", "list"]:
            out = self.mas
        return subprocess.CompletedProcess(cmd, 0, out, "")


@pytest.fixture
def mac(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    fake = FakeMac()
    monkeypatch.setattr(cli, "run", fake.run)
    monkeypatch.setattr(cli.shutil, "which", lambda tool: f"/opt/homebrew/bin/{tool}")
    fake.home = home
    fake.folder = tmp_path / "setup"
    cli.main(["init", str(fake.folder)])
    (fake.folder / cli.CONFIG_NAME).write_text(
        'domains = ["com.apple.dock"]\ndotfiles = [".zshrc", ".config/git/ignore"]\n'
    )
    return fake


def test_capture_writes_everything(mac):
    (mac.home / ".zshrc").write_text("export EDITOR=vim\n")
    (mac.home / ".config/git").mkdir(parents=True)
    (mac.home / ".config/git/ignore").write_text(".DS_Store\n")

    assert cli.main(["capture", str(mac.folder)]) == 0

    f = mac.folder
    assert 'brew "git"' in (f / "Brewfile").read_text()
    assert ["brew", "bundle", "dump", "--file", str(f / "Brewfile"), "--force", "--no-mas"] in mac.calls
    assert plistlib.loads((f / "defaults/com.apple.dock.plist").read_bytes())["tilesize"] == 48
    assert (f / "dotfiles/.zshrc").read_text() == "export EDITOR=vim\n"
    assert (f / "dotfiles/.config/git/ignore").read_text() == ".DS_Store\n"
    assert (f / "mas.txt").read_text() == "409183694 Keynote\n497799835 Xcode\n"


def test_capture_skips_missing_dotfile(mac, capsys):
    assert cli.main(["capture", str(mac.folder), "--only", "dotfiles"]) == 0
    assert "skip: ~/.zshrc is not a file" in capsys.readouterr().out


def test_apply_dry_run_changes_nothing(mac, capsys):
    (mac.folder / "dotfiles").mkdir()
    (mac.folder / "dotfiles/.zshrc").write_text("new\n")
    (mac.home / ".zshrc").write_text("old\n")
    (mac.folder / "mas.txt").write_text("1111 Pages\n497799835 Xcode\n")

    assert cli.main(["apply", str(mac.folder), "--only", "dotfiles,mas", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "would write ~/.zshrc" in out
    assert "would install Pages (1111)" in out
    assert "Xcode" not in out
    assert (mac.home / ".zshrc").read_text() == "old\n"
    assert ["mas", "install", "1111"] not in mac.calls


BUNDLE_MISSING = (
    "brew bundle can't satisfy your Brewfile's dependencies.\n"
    "→ Formula hello needs to be installed or updated.\n"
)


def test_apply_dry_run_lists_missing_brew_packages(mac, capsys):
    (mac.folder / "Brewfile").write_text('brew "hello"\n')
    mac.bundle_check = (1, BUNDLE_MISSING)

    assert cli.main(["apply", str(mac.folder), "--only", "brew", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "Formula hello needs to be installed" in out
    assert "nothing to install" not in out
    assert ["brew", "bundle", "check", "--file", str(mac.folder / "Brewfile"), "--verbose", "--no-upgrade"] in mac.calls
    assert not any(c[:3] == ["brew", "bundle", "install"] for c in mac.calls)


def test_apply_installs_missing_brew_packages_without_upgrading(mac, capsys):
    (mac.folder / "Brewfile").write_text('brew "hello"\n')
    mac.bundle_check = (1, BUNDLE_MISSING)

    assert cli.main(["apply", str(mac.folder), "--only", "brew", "--yes"]) == 0

    assert "run brew bundle install --no-upgrade" in capsys.readouterr().out
    assert ["brew", "bundle", "install", "--file", str(mac.folder / "Brewfile"), "--no-upgrade"] in mac.calls


def test_apply_upgrade_checks_and_upgrades_outdated(mac, capsys):
    (mac.folder / "Brewfile").write_text('brew "git"\n')
    mac.bundle_check = (1, "→ Formula git needs to be installed or updated.\n")

    assert cli.main(["apply", str(mac.folder), "--only", "brew", "--upgrade", "--yes"]) == 0

    brewfile = str(mac.folder / "Brewfile")
    assert "would run brew bundle install --upgrade" in capsys.readouterr().out
    assert ["brew", "bundle", "check", "--file", brewfile, "--verbose"] in mac.calls
    assert ["brew", "bundle", "install", "--file", brewfile, "--upgrade"] in mac.calls


def test_apply_dry_run_brew_satisfied(mac, capsys):
    (mac.folder / "Brewfile").write_text('brew "git"\n')
    assert cli.main(["apply", str(mac.folder), "--only", "brew", "--dry-run"]) == 0
    assert "brew: nothing to install" in capsys.readouterr().out


def test_apply_dry_run_brew_failure_is_an_error(mac, capsys):
    (mac.folder / "Brewfile").write_text('brew "git"\n')
    mac.bundle_check = (2, "Error: something broke")
    assert cli.main(["apply", str(mac.folder), "--only", "brew", "--dry-run"]) == 2
    assert "something broke" in capsys.readouterr().err


def test_run_allows_listed_exit_codes():
    exit_1 = [sys.executable, "-c", "import sys; sys.exit(1)"]
    assert cli.run(exit_1, ok=(0, 1)).returncode == 1
    with pytest.raises(subprocess.CalledProcessError):
        cli.run(exit_1)


def test_apply_backs_up_and_writes(mac):
    (mac.folder / "dotfiles").mkdir()
    (mac.folder / "dotfiles/.zshrc").write_text("new\n")
    (mac.home / ".zshrc").write_text("old\n")
    plist = mac.folder / "defaults/com.apple.dock.plist"
    plist.parent.mkdir()
    plist.write_bytes(plistlib.dumps({"autohide": False}))

    assert cli.main(["apply", str(mac.folder), "--only", "dotfiles,defaults", "--yes"]) == 0

    assert (mac.home / ".zshrc").read_text() == "new\n"
    backups = list((mac.home / ".mac-twin-backup").glob("*/.zshrc"))
    assert [b.read_text() for b in backups] == ["old\n"]
    assert ["defaults", "import", "com.apple.dock", str(plist)] in mac.calls


def save_dock(mac, values):
    plist = mac.folder / "defaults/com.apple.dock.plist"
    plist.parent.mkdir(exist_ok=True)
    plist.write_bytes(plistlib.dumps(values))
    return plist


def test_apply_skips_defaults_that_already_match(mac, capsys):
    save_dock(mac, dict(mac.defaults["com.apple.dock"]))

    assert cli.main(["apply", str(mac.folder), "--only", "defaults", "--dry-run"]) == 0
    assert "com.apple.dock" not in capsys.readouterr().out

    assert cli.main(["apply", str(mac.folder), "--only", "defaults"]) == 0
    assert "nothing to apply" in capsys.readouterr().out
    assert not any(c[:2] == ["defaults", "import"] for c in mac.calls)


def stage_zshrc(mac):
    (mac.folder / "dotfiles").mkdir()
    (mac.folder / "dotfiles/.zshrc").write_text("new\n")
    (mac.home / ".zshrc").write_text("old\n")
    save_dock(mac, {"autohide": False})


@pytest.mark.parametrize("answer, code, zshrc", [("y", 0, "new\n"), ("n", 1, "old\n"), ("", 1, "old\n")])
def test_apply_asks_once(mac, monkeypatch, capsys, answer, code, zshrc):
    stage_zshrc(mac)
    prompts = []
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda msg: prompts.append(msg) or answer)

    assert cli.main(["apply", str(mac.folder), "--only", "dotfiles,defaults"]) == code

    assert prompts == ["apply 2 change(s)? [y/N] "]
    out = capsys.readouterr().out
    assert "would import defaults com.apple.dock\nwould write ~/.zshrc\n" in out
    assert (mac.home / ".zshrc").read_text() == zshrc
    assert any(c[:2] == ["defaults", "import"] for c in mac.calls) == (code == 0)


def test_apply_without_terminal_needs_yes(mac, monkeypatch, capsys):
    stage_zshrc(mac)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)

    assert cli.main(["apply", str(mac.folder), "--only", "dotfiles,defaults"]) == 2

    assert "--yes" in capsys.readouterr().err
    assert (mac.home / ".zshrc").read_text() == "old\n"
    assert not any(c[:2] == ["defaults", "import"] for c in mac.calls)


def test_diff_reports_changes(mac, capsys):
    (mac.folder / "Brewfile").write_text('tap "homebrew/core"\nbrew "git"\nbrew "jq"\n')
    plist = mac.folder / "defaults/com.apple.dock.plist"
    plist.parent.mkdir()
    plist.write_bytes(plistlib.dumps({"autohide": True, "tilesize": 64}))

    assert cli.main(["diff", str(mac.folder), "--only", "brew,defaults"]) == 1

    out = capsys.readouterr().out
    assert 'brew + brew "jq"' in out
    assert 'brew - cask "firefox"' in out
    assert "~ tilesize: 48 -> 64" in out
    assert "autohide" not in out


def test_diff_clean_returns_zero(mac):
    (mac.folder / "Brewfile").write_text(mac.brewfile)
    assert cli.main(["diff", str(mac.folder), "--only", "brew"]) == 0


def test_dotfile_with_home_prefix(mac):
    (mac.folder / cli.CONFIG_NAME).write_text('dotfiles = ["~/.vimrc"]\n')
    (mac.home / ".vimrc").write_text("set number\n")
    assert cli.main(["capture", str(mac.folder), "--only", "dotfiles"]) == 0
    assert (mac.folder / "dotfiles/.vimrc").read_text() == "set number\n"


@pytest.mark.parametrize("entry", [
    "/etc/hosts", "../outside", ".ssh/id_ed25519", "~/.ssh/config", ".aws", ".netrc", "",
    "~", "~/", "~other/.zshrc", "~/~/.vimrc",
])
def test_rejects_unsafe_dotfiles(mac, entry, capsys):
    (mac.folder / cli.CONFIG_NAME).write_text(f'dotfiles = ["{entry}"]\n')
    assert cli.main(["capture", str(mac.folder)]) == 2
    assert "error:" in capsys.readouterr().err


def test_rejects_bad_domain(mac):
    (mac.folder / cli.CONFIG_NAME).write_text('domains = ["com.apple.dock; rm -rf ~"]\n')
    assert cli.main(["capture", str(mac.folder)]) == 2


def test_missing_config(tmp_path, capsys):
    assert cli.main(["capture", str(tmp_path)]) == 2
    assert "mac-twin init" in capsys.readouterr().err


def test_unknown_section(mac):
    with pytest.raises(SystemExit):
        cli.main(["capture", str(mac.folder), "--only", "brew,nope"])


def test_parse_mas_list_ignores_noise():
    assert cli.parse_mas_list(MAS_LIST + "No installed apps found\n") == {
        "497799835": "Xcode",
        "409183694": "Keynote",
    }
