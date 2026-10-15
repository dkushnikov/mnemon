# mnemon (plugin)

Claude Code plugin for [Mnemon](../../README.md): the skills `/source-add`, `/source-search` and `/source-status`. It is a thin layer — every skill runs the installed engine's dispatcher, `"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon"`, so install the engine first ([docs/install.md](../../docs/install.md)). The plugin used to live in the separate `mnemon-plugin` repository, now archived.

## Install

```bash
claude plugin marketplace add ~/.local/share/mnemon/engine
claude plugin install mnemon@mnemon
```

The marketplace points at the engine directory, so the skills you run are the ones checked out there (the `release` branch).

## Update

```bash
git -C ~/.local/share/mnemon/engine pull --ff-only
claude plugin marketplace update mnemon && claude plugin update mnemon@mnemon
```
Migrating from the archived repo: `claude plugin uninstall mnemon@mnemon-plugin && claude plugin marketplace remove mnemon-plugin`, then install as above.
