# Roadmap

Planned work after the first version. Estimates are rough. Each item has a GitHub issue.

| # | Item | Estimate |
|---|------|----------|
| 1 | Confirm before `apply`: print the dry-run summary, ask once, `--yes` to skip | 1.5 h |
| 2 | `restore` command: put dotfiles back from a `~/.mac-twin-backup/<timestamp>` folder, with `--dry-run` | 2 h |
| 3 | Route `brew bundle check` through the shared `run` helper (allowed exit codes) so the brew dry run is covered by tests | 1 h |
| 4 | Dotfile directories: allow entries like `.config/nvim/` with an `exclude` list of glob patterns | 3 h |
| 5 | `--link` mode: symlink dotfiles from the folder instead of copying, for people who edit them in the repo | 3 h |
| 6 | Secret check on capture: warn (and skip unless `--force`) when a captured file has lines that look like tokens or private keys | 3 h |
| 7 | Per-domain ignore list for volatile defaults keys (window positions, timestamps, recent items) so `diff` stays quiet | 3 h |
| 8 | Nested defaults diff: show changes inside dictionaries and arrays by key path instead of the whole value | 2 h |
| 9 | VS Code extensions section (`code --list-extensions`, `code --install-extension`) | 2 h |
| 10 | Global package section for `pipx` and `npm -g` | 2 h |
| 11 | `diff --json` output for scripts and CI | 2 h |
| 12 | Machine-specific overrides: `[machine.<name>]` tables in `mac-twin.toml` merged over the base lists | 3 h |
| 13 | macOS integration test in CI: capture and apply a throwaway defaults domain for real | 2 h |
| 14 | Packaging: build a wheel in CI on tags and publish a Homebrew tap formula | 2 h |
| 15 | `status` command: one line per section with the number of differences, no details | 1.5 h |

Total: about 33 hours.
