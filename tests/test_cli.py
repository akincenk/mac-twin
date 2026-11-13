import plistlib
import subprocess
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

    def run(self, cmd, text=True):
        self.calls.append(cmd)
        out = ""
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


def test_apply_backs_up_and_writes(mac):
    (mac.folder / "dotfiles").mkdir()
    (mac.folder / "dotfiles/.zshrc").write_text("new\n")
    (mac.home / ".zshrc").write_text("old\n")
    plist = mac.folder / "defaults/com.apple.dock.plist"
    plist.parent.mkdir()
    plist.write_bytes(plistlib.dumps({"autohide": False}))

    assert cli.main(["apply", str(mac.folder), "--only", "dotfiles,defaults"]) == 0

    assert (mac.home / ".zshrc").read_text() == "new\n"
    backups = list((mac.home / ".mac-twin-backup").glob("*/.zshrc"))
    assert [b.read_text() for b in backups] == ["old\n"]
    assert ["defaults", "import", "com.apple.dock", str(plist)] in mac.calls


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


@pytest.mark.parametrize("entry", ["/etc/hosts", "../outside", ".ssh/id_ed25519", ".aws", ".netrc", ""])
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
