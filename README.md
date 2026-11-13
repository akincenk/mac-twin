# mac-twin

[![CI](https://github.com/akincenk/mac-twin/actions/workflows/ci.yml/badge.svg)](https://github.com/akincenk/mac-twin/actions/workflows/ci.yml)

A small command line tool that saves the setup of one Mac into a plain folder you can keep in git, and applies that folder on another Mac. It shows you what would change before it changes anything.

## Who it is for

People who set up more than one Mac (a new laptop, a work and a home machine, a reinstall) and want their Homebrew packages, a few system settings, their dotfiles and their App Store apps back without clicking through everything by hand.

## What it captures

| Section    | Saved as                     | Source                                   |
|------------|------------------------------|------------------------------------------|
| `brew`     | `Brewfile`                   | `brew bundle dump --no-mas`              |
| `defaults` | `defaults/<domain>.plist`    | `defaults export <domain> -` (XML plist) |
| `dotfiles` | `dotfiles/<path>`            | files you list, relative to `~`          |
| `mas`      | `mas.txt` (`<id> <name>`)    | `mas list`, if `mas` is installed        |

App Store apps live in their own file so you can skip them with `--only` on a Mac where you are not signed in to the App Store.

## Stack

Python 3.11+ standard library only (`argparse`, `tomllib`, `plistlib`, `difflib`, `subprocess`). Tests use pytest with a fake `brew`/`defaults`/`mas`, so they never touch the machine they run on. CI runs on GitHub Actions.

## Install

```sh
git clone https://github.com/akincenk/mac-twin.git
cd mac-twin
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

## Use

```sh
mac-twin init ~/my-mac          # writes ~/my-mac/mac-twin.toml
$EDITOR ~/my-mac/mac-twin.toml  # pick defaults domains and dotfiles
mac-twin capture ~/my-mac       # save this Mac into the folder
```

On the other Mac:

```sh
mac-twin diff ~/my-mac            # what differs; exit code 1 if anything does
mac-twin apply ~/my-mac --dry-run # what apply would do
mac-twin apply ~/my-mac           # do it
mac-twin apply ~/my-mac --only brew,dotfiles
```

`mac-twin.toml`:

```toml
domains = ["com.apple.dock", "com.apple.finder"]
dotfiles = [".zshrc", ".gitconfig", ".config/git/ignore"]
```

## Safety

- Dotfile paths must stay inside your home folder. Absolute paths and `..` are rejected.
- Paths that usually hold credentials (`.ssh`, `.gnupg`, `.aws`, `.netrc`, `.npmrc`, `.pypirc`, `.kube`, Docker and GitHub CLI auth files) are refused.
- Before `apply` overwrites a dotfile it copies the old one to `~/.mac-twin-backup/<timestamp>/`.
- Commands are run without a shell, and defaults domain names are checked against a strict pattern.
- Settings exports and dotfiles can still contain personal data (paths with your user name, tokens in a `.zshrc`). Read the folder, or run a scanner such as `gitleaks dir ~/my-mac`, before you push it anywhere public.

## Develop

```sh
.venv/bin/pytest
.venv/bin/ruff check .
```

See [ROADMAP.md](ROADMAP.md) for planned work.

## License

MIT, see [LICENSE](LICENSE).
