# Contributing

Thanks for looking at mac-twin. Issues and pull requests are welcome.

## Set up

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
```

## Before you open a pull request

```sh
ruff check .
pytest -q
gitleaks dir .
```

CI runs the same checks on macOS and Ubuntu with Python 3.11 and 3.13, plus a gitleaks scan of the history and zizmor on the workflows.

## Guidelines

- One change per pull request, with tests. Bug fixes start with a test that fails.
- Tests must never touch the machine they run on. Use the `FakeMac` fixture in `tests/test_cli.py` for `brew`, `defaults` and `mas`, and a temporary home folder for dotfiles.
- Every external command goes through `run()` with a list of arguments, never a shell string.
- `apply` must stay safe to try: anything it changes shows up in `--dry-run` first, and a dotfile it overwrites is backed up.
- Check new `brew`, `defaults` or `mas` flags against the installed version's `--help` before relying on them.
- Sample data must be made up. Never commit a real Brewfile, plist or dotfile from your own Mac.
- The planned work is in [ROADMAP.md](ROADMAP.md); each item has a GitHub issue. Comment on the issue before starting a large item.
