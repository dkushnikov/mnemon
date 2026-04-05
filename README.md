# Mnemon

AI-assisted personal knowledge extraction. Ingest sources (articles, papers, YouTube, podcasts, ideas); an LLM produces structured extracts under a reader-context profile you control. Your vault stays in plain Markdown under your Obsidian vault — Mnemon is the engine, not a storage silo.

**Principle:** *AI does filing, human does understanding.* Extraction is mechanical; synthesis is not.

## Architecture at a glance

```
URL / file / text → knowledge-gateway.sh → claude -p → <your vault>/Sources/
                          ↓                                 ├── source.md   (immutable raw)
                    mnemon.yaml                              └── extract.md  (AI-generated, structured)
                    reader-context.md
                    templates/core/<type>.md
```

- **Gateway** (`bin/knowledge-gateway.sh`) — shell tool that loads config, assembles a prompt from the extraction template + your reader context, and invokes `claude -p` inside the vault directory.
- **Vault layout** — `Sources/YYYY-MM-DD_<hash8>/{source.md, extract.md}`. `source.md` is immutable once written; `extract.md` can be re-generated.
- **Templates** — Fabric-inspired extraction prompts per source type (article, video, podcast, paper, book, idea, conversation). Live in `templates/core/`.
- **Reader context** — a markdown profile of the reader that personalizes every extract's framing, key ideas, and ranking.

## Install

```bash
git clone https://github.com/dkushnikov/mnemon.git ~/Mnemon
~/Mnemon/setup.sh ~/path/to/your/vault
```

`setup.sh` scaffolds the vault, generates `mnemon.yaml`, and checks for optional dependencies (`yt-dlp`, `whisper`, `ffprobe`, [QMD](https://github.com/tobi/qmd)).

## Claude Code plugin

Mnemon itself is a tool; the Claude Code integration (slash commands, skills, prompts) lives in a **separate repo**: [`dkushnikov/mnemon-plugin`](https://github.com/dkushnikov/mnemon-plugin).

Install the plugin after `setup.sh`:

```bash
claude plugin marketplace add https://github.com/dkushnikov/mnemon-plugin
claude plugin install mnemon@mnemon-plugin
```

Then from any Claude Code session:

```
/source-add https://example.com/article
/source-search "organizational design"
/source-status
```

## Documentation

- [`docs/README.md`](docs/README.md) — full developer reference (architecture, templates, conventions)
- [`CLAUDE.md`](CLAUDE.md) — developer notes for working on Mnemon itself
- [`protocols/`](protocols/) — schema and convention docs

## License

[MIT](LICENSE)
