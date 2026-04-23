# Mnemon Seed — Reader Profile Builder

Build your `reader-context.md` — the profile that makes every Mnemon extract personal to you.

**Time:** 5-10 minutes.
**Result:** a `reader-context.md` file in this vault that personalizes every future extraction.

---

## Instructions for Claude

Follow this guide step by step. Do not skip ahead. Write nothing until the conversation completes.

### Step 1 — Detect existing context

Check if this vault (or a linked vault) already has identity artifacts. Look for:

1. `reader-context.md` in this vault — if it exists AND has content beyond template placeholders, show it and ask: "You already have a reader context. Want to review and refine it, or start fresh?"
2. `Me.md`, `Areas.md`, `Goals.md`, `Values.md` in this vault — identity artifacts from Obsidian Seed
3. `CLAUDE.md` — may contain professional context, communication preferences

If identity artifacts found → go to **Step 2a** (assembly).
If nothing found → go to **Step 2b** (discovery).

### Step 2a — Assemble from existing profile

Read the identity artifacts. Extract:
- Professional role and background (from Me.md or CLAUDE.md)
- Domains of interest (from Areas.md, mapped to reader-context domain tags)
- Current goals and focus (from Goals.md)
- Communication preferences (from CLAUDE.md — directness, language, style)

Draft `reader-context.md` from these. Show the draft. Ask:
- "Does this capture how you want extracts framed?"
- "Any domains to add or remove?"
- "What language for Executive Summaries?"

Incorporate feedback, write the file.
Go to **Step 3**.

### Step 2b — Discovery conversation

Ask these questions one at a time. Wait for each answer before asking the next.

**Q1: Who are you?**
"What do you do professionally? 2-3 sentences — role, domain, what makes your perspective distinct."

**Q2: What do you read about?**
"What topics, fields, or areas do you actively follow? Not what you *should* read — what you actually spend attention on."

Map answers to domain tags. Propose a list. Ask if anything is missing.

**Q3: What are you focused on right now?**
"Current projects, goals, or questions you're actively working through. These frame how Executive Summaries talk to you."

**Q4: How do you want extracts to talk to you?**
"Direct or thorough? Challenge assumptions or stay neutral? Connect to your work or keep it general?"

If the user isn't sure, offer defaults:
- Challenge my assumptions
- Be direct, no fluff
- Connect to my priorities where genuine
- Actionable over theoretical

**Q5: Language?**
"Executive Summaries in English, Russian, or match the source language?"

Draft `reader-context.md`. Show it. Ask for edits. Write the final version.

### Step 3 — Confirm

Show the path to the written file. Say:

"Your reader context is set. Every source you add through `/source-add` will now be extracted through your lens. You can edit `reader-context.md` anytime — it's just a markdown file."

Done. Do not continue with other tasks.
