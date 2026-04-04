# Domains

Domains are tags that categorize Key Ideas by area of interest. They connect extracts to the reader's personal context and enable filtered search.

## Default Domains (Life Capital Framework)

| Domain | Description | Example Key Idea |
|--------|-------------|-----------------|
| `learning` | What changes how I think | `- **Spaced repetition** #learning — ...` |
| `health` | Evidence-based optimization | `- **Zone 2 cardio** #health — ...` |
| `relationships` | Social capital, peers, network | `- **Weak ties** #relationships — ...` |
| `home` | Environment, space, routines | `- **Deep work space** #home — ...` |
| `finance` | Wealth, investment, tax | `- **Tax-loss harvesting** #finance — ...` |
| `career` | Professional growth, leadership | `- **Skip-level 1:1s** #career — ...` |
| `culture` | Joy, travel, hobbies | `- **Wabi-sabi aesthetic** #culture — ...` |
| `influence` | Personal brand, community | `- **Building in public** #influence — ...` |
| `inner-work` | Coaching, self-awareness | `- **Values alignment** #inner-work — ...` |

## Custom Domains

Add custom domains in `reader-context.md` under "Domains of Interest":

```markdown
## Domains of Interest
- `learning` — what changes how I think
- `my-company/product` — product decisions at my company
- `ai-agents` — AI agent architectures and patterns
```

The extraction template picks up domain tags from the reader context.

## Domain Naming Conventions

- Lowercase, hyphenated: `inner-work`, not `Inner Work`
- Namespaced with `/` for work contexts: `mycompany/engineering`, `mycompany/product`
- Keep the list to 7-15 domains — too many defeats the purpose
