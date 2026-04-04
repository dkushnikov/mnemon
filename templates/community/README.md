# Community Templates

Drop-in extraction templates compatible with Mnemon.

## Adding a Template

1. Create a `.md` file in this directory
2. Follow the Mnemon template format: IDENTITY → STEPS → OUTPUT INSTRUCTIONS → INPUT
3. The gateway loads templates by `source_type` name: `templates/core/{source_type}.md`
4. Community templates override core templates if placed in `templates/core/`

## Fabric Compatibility

Fabric `system.md` pattern files can be placed here. The gateway will use them if referenced by `--source-type` matching the filename (without extension).

Note: Fabric patterns produce freeform prose. Mnemon core templates produce structured output (frontmatter + Key Ideas + domain tags). Community templates may produce either format.

## Contributing

PRs welcome. Include a brief description of what your template extracts and for what source types.
