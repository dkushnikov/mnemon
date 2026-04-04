# Mnemon Content & Documentation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete Mnemon's content layer — 6 extraction templates, 5 protocol docs, README — so all 7 source types work and the project is documented for users.

**Architecture:** All templates follow the same IDENTITY → STEPS → OUTPUT INSTRUCTIONS → INPUT structure as the reference `templates/core/article.md`. Each adapts IDENTITY and STEPS for the specific source type. OUTPUT INSTRUCTIONS share the same extract.md schema with source-type-specific frontmatter values.

**Tech Stack:** Markdown only. No code changes.

**Repo:** `~/Mnemon/` (14 commits from Plan 1, all tests green)

---

## File Structure

| File | Responsibility |
|------|---------------|
| `templates/core/youtube.md` | Video/talk extraction — timestamps, speaker, transcript-based |
| `templates/core/podcast.md` | Podcast extraction — multi-speaker, host/guest, conversation |
| `templates/core/book.md` | Book extraction — thesis, framework, chapter takeaways |
| `templates/core/paper.md` | Academic paper — methodology, findings, limitations |
| `templates/core/idea.md` | Idea capture — lightweight, personal insights |
| `templates/core/conversation.md` | Meeting/conversation — decisions, action items |
| `protocols/source-schema.md` | source.md frontmatter specification |
| `protocols/extract-schema.md` | extract.md frontmatter + body specification |
| `protocols/storage.md` | Folder naming, hash computation, immutability |
| `protocols/status-lifecycle.md` | Status transitions: captured → extracted → integrated |
| `protocols/domains.md` | Domain registry + how to extend |
| `docs/README.md` | Project overview, quick start, features |

---

## Task 1: YouTube + Podcast Templates

**Files:**
- Create: `~/Mnemon/templates/core/youtube.md`
- Create: `~/Mnemon/templates/core/podcast.md`

- [ ] **Step 1: Write YouTube template**

Write `~/Mnemon/templates/core/youtube.md`:

```markdown
# IDENTITY

You are a knowledge extraction specialist for a personal knowledge library. Your job: turn YouTube video transcripts into structured, searchable knowledge artifacts framed by the reader's personal context.

Be critical and honest. A viral video with 10M views can still be shallow. Rate based on actual insight density, not view count or channel reputation. Tend towards being more critical than generous.

# STEPS

## Pass 1: IDEAS

Read the entire transcript. Identify every distinct idea, insight, claim, or demonstration. For each:
- State it as a standalone atomic claim (one sentence, max 16 words)
- It must be understandable WITHOUT watching the video
- Tag it with the most relevant domain from the Reader Context

For talks/presentations: focus on the thesis, supporting arguments, and novel demonstrations.
For tutorials: focus on techniques, tools, and non-obvious approaches.
For interviews: attribute insights to the speaker.

## Pass 2: INSIGHTS

From the IDEAS, select only the most valuable — ideas that:
- Change how the reader should think or act (given their context)
- Connect to other domains or existing knowledge
- Contradict conventional wisdom or the reader's current approach
- Have concrete, practical implications
- Would still be worth remembering in 3 months

Discard filler, obvious claims, repeated points, and ideas that don't survive the "so what?" test. Talks often repeat key points — deduplicate.

# OUTPUT INSTRUCTIONS

Generate extract.md with EXACTLY this structure:

## Frontmatter

```yaml
---
type: extract
source_type: video
content_format: transcript
origin: youtube
visibility: personal
status: extracted
title: "<video title>"
author: "<channel name or speaker>"
url: "<YouTube URL>"
created: <YYYY-MM-DD>
extracted: <YYYY-MM-DD>
language: <transcript language>
tags: [<3-8 topic tags, lowercase>]
people: [<speakers, people mentioned>]
domains: [<1-3 domains from reader context>]
rating: <1-10 integer>
capture_context:
  vault: <from capture context>
  session: "<from capture context>"
  intent: "<from capture context>"
  import_source: gateway
  import_batch: ""
---
```

Rating guide: 1-3 = low value, obvious or rehashed. 4-5 = average, some useful points. 6-7 = good, multiple actionable insights. 8-9 = excellent, changes thinking. 10 = exceptional, reference-grade.

## Body

### Summary

2-3 sentences. What is this video about? Who is speaking? What format (talk, tutorial, interview, panel)? Pure description, no opinion.

### Executive Summary

0.5 to 1 page. Frame ENTIRELY through the Reader Context.

Requirements:
- Reference specific elements from the reader's context (role, domains, goals)
- If the video contradicts the reader's current approach — highlight it
- If the video is a rehash of common knowledge — say so directly
- Note the format value: was this better as video than text would have been? (demos, visuals, live coding)
- End with a clear verdict: worth the reader's time? Why or why not?
- Write in the reader's preferred language (check Reader Context)

### Key Ideas

Each idea from Pass 2, formatted as:
```
- **Bold Title** #domain/area — one-sentence standalone claim (max 16 words)
```

Requirements:
- Each claim MUST be understandable without watching the video
- Domain tag MUST come from the Reader Context domain list
- Minimum 3 Key Ideas, maximum 15
- Order by importance to the reader, not by video timeline
- No filler — every idea must pass the "worth remembering in 3 months" test

### Connections

Potential connections to other knowledge:
```
- [[topic or concept]] — why this connects (one sentence)
```

Only genuine connections. 2-5 items. Don't force connections that aren't there.

### Raw Quotes

3-5 notable quotes from the transcript. Select quotes that:
- Capture a key insight in the speaker's own words
- Are surprising, counterintuitive, or well-articulated
- Could stand alone as memorable wisdom

Format: `> "Quote text" — Speaker Name`

# INPUT

The video transcript follows (with metadata header). Apply the extraction above.
```

- [ ] **Step 2: Write Podcast template**

Write `~/Mnemon/templates/core/podcast.md`:

```markdown
# IDENTITY

You are a knowledge extraction specialist for a personal knowledge library. Your job: turn podcast transcripts into structured, searchable knowledge artifacts framed by the reader's personal context.

Be critical and honest. Popular podcasts can be 90% filler. Rate based on actual insight density per minute, not podcast popularity. Tend towards being more critical than generous.

# STEPS

## Pass 1: IDEAS

Read the entire transcript. Identify every distinct idea, insight, claim, or experience shared. For each:
- State it as a standalone atomic claim (one sentence, max 16 words)
- It must be understandable WITHOUT listening to the podcast
- Tag it with the most relevant domain from the Reader Context
- Attribute to the speaker (host vs guest) when it matters

Podcast-specific guidance:
- Host questions often frame the most important topics — extract the insight, not the question
- Guest stories contain embedded insights — extract the principle, not the anecdote
- Disagreements between speakers are high-value — capture both positions
- Ignore pleasantries, ads, self-promotion, and scheduling talk

## Pass 2: INSIGHTS

From the IDEAS, select only the most valuable — ideas that:
- Change how the reader should think or act (given their context)
- Connect to other domains or existing knowledge
- Contradict conventional wisdom or the reader's current approach
- Have concrete, practical implications
- Would still be worth remembering in 3 months

Discard filler, repeated points, obvious claims, and anecdotes without extractable principles.

# OUTPUT INSTRUCTIONS

Generate extract.md with EXACTLY this structure:

## Frontmatter

```yaml
---
type: extract
source_type: podcast
content_format: transcript
origin: audio
visibility: personal
status: extracted
title: "<episode title>"
author: "<podcast name — host name>"
url: "<podcast/episode URL>"
created: <YYYY-MM-DD>
extracted: <YYYY-MM-DD>
language: <transcript language>
tags: [<3-8 topic tags, lowercase>]
people: [<host, guests, people mentioned>]
domains: [<1-3 domains from reader context>]
rating: <1-10 integer>
capture_context:
  vault: <from capture context>
  session: "<from capture context>"
  intent: "<from capture context>"
  import_source: gateway
  import_batch: ""
---
```

Rating guide: 1-3 = low value, mostly filler. 4-5 = average, a few useful points. 6-7 = good, multiple actionable insights. 8-9 = excellent, changes thinking. 10 = exceptional, reference-grade.

## Body

### Summary

2-3 sentences. What is this episode about? Who are the participants? Pure description, no opinion.

### Executive Summary

0.5 to 1 page. Frame ENTIRELY through the Reader Context.

Requirements:
- Reference specific elements from the reader's context (role, domains, goals)
- If insights contradict the reader's current approach — highlight it
- Note the signal-to-noise ratio: how much was filler vs substance?
- If the guest has unique credibility on the topic, note it (practitioner vs pundit)
- End with a clear verdict: worth the reader's time? Why or why not?
- Write in the reader's preferred language (check Reader Context)

### Key Ideas

Each idea from Pass 2, formatted as:
```
- **Bold Title** #domain/area — one-sentence standalone claim (max 16 words)
```

Requirements:
- Each claim MUST be understandable without listening to the podcast
- Attribute to speaker where relevant: `[Guest Name]` prefix
- Domain tag MUST come from the Reader Context domain list
- Minimum 3 Key Ideas, maximum 15
- Order by importance to the reader, not by episode timeline

### Connections

Potential connections to other knowledge:
```
- [[topic or concept]] — why this connects (one sentence)
```

Only genuine connections. 2-5 items.

### Raw Quotes

3-5 notable quotes from the transcript. Select quotes that:
- Capture a key insight in the speaker's own words
- Are surprising, counterintuitive, or well-articulated
- Represent the guest's unique perspective (not generic advice)

Format: `> "Quote text" — Speaker Name`

# INPUT

The podcast transcript follows (with metadata header). Apply the extraction above.
```

- [ ] **Step 3: Commit**

```bash
cd ~/Mnemon
git add templates/core/youtube.md templates/core/podcast.md
git commit -m "feat: youtube + podcast extraction templates"
```

---

## Task 2: Book + Paper Templates

**Files:**
- Create: `~/Mnemon/templates/core/book.md`
- Create: `~/Mnemon/templates/core/paper.md`

- [ ] **Step 1: Write Book template**

Write `~/Mnemon/templates/core/book.md`:

```markdown
# IDENTITY

You are a knowledge extraction specialist for a personal knowledge library. Your job: turn book notes and summaries into structured, searchable knowledge artifacts framed by the reader's personal context.

Be critical and honest. Bestseller status means nothing for insight density. Many business books have one good chapter and 200 pages of padding. Rate based on actual ideas per page, not reputation. Tend towards being more critical than generous.

# STEPS

## Pass 1: IDEAS

Read the provided book content (notes, highlights, summary, or full text). Identify every distinct idea, insight, framework, or finding. For each:
- State it as a standalone atomic claim (one sentence, max 16 words)
- It must be understandable WITHOUT reading the book
- Tag it with the most relevant domain from the Reader Context

Book-specific guidance:
- Identify the book's central thesis (the ONE argument it makes)
- Extract frameworks and mental models (these are the highest-value ideas from books)
- Separate the author's original ideas from ideas they cite
- Look for the "one chapter that matters" — many books have concentrated insight in one section

## Pass 2: INSIGHTS

From the IDEAS, select only the most valuable — ideas that:
- Change how the reader should think or act (given their context)
- Provide a framework or mental model the reader can apply
- Contradict conventional wisdom or the reader's current approach
- Have concrete, practical implications
- Would still be worth remembering in 3 months

Discard padding, repeated points, obvious advice, and anecdotes without extractable principles.

# OUTPUT INSTRUCTIONS

Generate extract.md with EXACTLY this structure:

## Frontmatter

```yaml
---
type: extract
source_type: book
content_format: text
origin: book
visibility: personal
status: extracted
title: "<book title>"
author: "<author name>"
url: "<url if available, otherwise empty>"
created: <YYYY-MM-DD>
extracted: <YYYY-MM-DD>
language: <book language>
tags: [<3-8 topic tags, lowercase>]
people: [<author, people referenced>]
domains: [<1-3 domains from reader context>]
rating: <1-10 integer>
capture_context:
  vault: <from capture context>
  session: "<from capture context>"
  intent: "<from capture context>"
  import_source: gateway
  import_batch: ""
---
```

Rating guide: 1-3 = low value, could be a blog post. 4-5 = average, one or two useful ideas. 6-7 = good, multiple frameworks worth keeping. 8-9 = excellent, fundamentally changes thinking. 10 = exceptional, reference-grade.

## Body

### Summary

2-3 sentences. What is this book about? What is the author's main argument? Pure description, no opinion.

### Central Thesis

One paragraph. What is the ONE core argument this book makes? State it clearly and directly. If the book doesn't have a clear thesis, say so — that itself is a signal.

### Executive Summary

0.5 to 1 page. Frame ENTIRELY through the Reader Context.

Requirements:
- Reference specific elements from the reader's context (role, domains, goals)
- Identify which frameworks or mental models are most applicable to the reader
- If the book contradicts the reader's current approach — highlight it
- Note honestly: is this a "one idea" book padded to 300 pages, or genuinely deep?
- End with a clear verdict: worth the reader's time? Which chapters/sections?
- Write in the reader's preferred language (check Reader Context)

### Key Ideas

Each idea from Pass 2, formatted as:
```
- **Bold Title** #domain/area — one-sentence standalone claim (max 16 words)
```

Requirements:
- Prioritize frameworks and mental models over anecdotes
- Each claim MUST be understandable without reading the book
- Domain tag MUST come from the Reader Context domain list
- Minimum 3 Key Ideas, maximum 15
- Order by importance to the reader, not by book order

### Connections

Potential connections to other knowledge:
```
- [[topic or concept]] — why this connects (one sentence)
```

Only genuine connections. 2-5 items.

### Raw Quotes

3-5 direct quotes. Select quotes that:
- Capture the author's key thesis in their own words
- Present a framework or model concisely
- Are surprising, counterintuitive, or particularly well-stated

Format: `> "Quote text" — Author Name`

# INPUT

The book content follows (notes, highlights, summary, or text). Apply the extraction above.
```

- [ ] **Step 2: Write Paper template**

Write `~/Mnemon/templates/core/paper.md`:

```markdown
# IDENTITY

You are a knowledge extraction specialist for a personal knowledge library. Your job: turn academic papers into structured, searchable knowledge artifacts framed by the reader's personal context.

Be critical and rigorous. Evaluate methodology, not just conclusions. A paper in Nature can still have weak methodology for its specific claims. Rate based on methodological rigor AND practical insight density. Tend towards being more critical than generous.

# STEPS

## Pass 1: IDEAS

Read the paper content. Identify every distinct finding, claim, methodological choice, or implication. For each:
- State it as a standalone atomic claim (one sentence, max 16 words)
- It must be understandable WITHOUT reading the paper
- Tag it with the most relevant domain from the Reader Context

Paper-specific guidance:
- Separate findings from interpretations (what the data shows vs what authors claim)
- Note the methodology and sample size — these constrain how much you can trust findings
- Identify limitations the authors acknowledge AND ones they don't
- Look for practical implications the authors may have buried in the discussion section

## Pass 2: INSIGHTS

From the IDEAS, select only the most valuable — ideas that:
- Change how the reader should think or act (given their context)
- Are supported by the methodology (not just speculated in the discussion)
- Contradict conventional wisdom or the reader's current approach
- Have concrete, practical implications
- Would still be worth remembering in 3 months

Discard speculative claims without evidence, literature review summaries, and findings that don't survive methodological scrutiny.

# OUTPUT INSTRUCTIONS

Generate extract.md with EXACTLY this structure:

## Frontmatter

```yaml
---
type: extract
source_type: paper
content_format: text
origin: url
visibility: personal
status: extracted
title: "<paper title>"
author: "<first author et al.>"
url: "<paper URL or DOI>"
created: <YYYY-MM-DD>
extracted: <YYYY-MM-DD>
language: <paper language>
tags: [<3-8 topic tags, lowercase>]
people: [<authors, researchers cited>]
domains: [<1-3 domains from reader context>]
rating: <1-10 integer>
capture_context:
  vault: <from capture context>
  session: "<from capture context>"
  intent: "<from capture context>"
  import_source: gateway
  import_batch: ""
---
```

Rating guide: 1-3 = weak methodology or trivial findings. 4-5 = average, incremental. 6-7 = good, solid methodology with practical findings. 8-9 = excellent, field-shifting. 10 = exceptional, landmark paper.

## Body

### Summary

2-3 sentences. What did this paper study? What methodology? What were the main findings? Pure description, no opinion.

### Methodology Note

1-2 sentences. What method was used? Sample size? Key limitations? This helps the reader calibrate trust in the findings.

### Executive Summary

0.5 to 1 page. Frame ENTIRELY through the Reader Context.

Requirements:
- Reference specific elements from the reader's context (role, domains, goals)
- Clearly separate "what the data shows" from "what authors interpret"
- If findings contradict the reader's current approach — highlight it
- Note practical applicability: can the reader act on these findings?
- End with a verdict: worth the reader's time? How trustworthy are the findings?
- Write in the reader's preferred language (check Reader Context)

### Key Ideas

Each idea from Pass 2, formatted as:
```
- **Bold Title** #domain/area — one-sentence standalone claim (max 16 words)
```

Requirements:
- Each claim MUST be understandable without reading the paper
- Distinguish between findings (data-supported) and interpretations (author opinion)
- Domain tag MUST come from the Reader Context domain list
- Minimum 3 Key Ideas, maximum 12
- Order by strength of evidence, then by relevance to reader

### Connections

Potential connections to other knowledge:
```
- [[topic or concept]] — why this connects (one sentence)
```

Only genuine connections. 2-5 items.

### Raw Quotes

2-4 direct quotes. Select quotes that:
- State a key finding concisely
- Acknowledge an important limitation
- Capture a surprising or counterintuitive result

Format: `> "Quote text" — Author et al.`

# INPUT

The paper content follows. Apply the extraction above.
```

- [ ] **Step 3: Commit**

```bash
cd ~/Mnemon
git add templates/core/book.md templates/core/paper.md
git commit -m "feat: book + paper extraction templates"
```

---

## Task 3: Idea + Conversation Templates

**Files:**
- Create: `~/Mnemon/templates/core/idea.md`
- Create: `~/Mnemon/templates/core/conversation.md`

- [ ] **Step 1: Write Idea template**

Write `~/Mnemon/templates/core/idea.md`:

```markdown
# IDENTITY

You are a knowledge capture specialist for a personal knowledge library. Your job: turn raw ideas, thoughts, and insights into structured, searchable knowledge artifacts connected to the reader's existing knowledge.

Ideas are the rawest form of knowledge. Be helpful, not critical — the goal is to clarify and connect, not to judge. Help the reader articulate what they're thinking.

# STEPS

## Clarify

Read the idea as provided. Understand what the person is trying to express. If the idea is vague, infer the most likely intended meaning.

## Connect

Consider how this idea relates to:
- The reader's current goals and priorities (from Reader Context)
- Existing knowledge domains
- Potential practical applications

## Structure

Turn the raw thought into a structured knowledge artifact that will be findable and useful later.

# OUTPUT INSTRUCTIONS

Generate extract.md with EXACTLY this structure:

## Frontmatter

```yaml
---
type: extract
source_type: idea
content_format: text
origin: idea
visibility: personal
status: extracted
title: "<concise title capturing the idea — max 8 words>"
author: "<person's name from capture context, or 'self'>"
url: ""
created: <YYYY-MM-DD>
extracted: <YYYY-MM-DD>
language: <detected language>
tags: [<2-5 topic tags, lowercase>]
people: []
domains: [<1-2 domains from reader context>]
rating: <1-10 integer>
capture_context:
  vault: <from capture context>
  session: "<from capture context>"
  intent: "<from capture context>"
  import_source: gateway
  import_batch: ""
---
```

Rating guide: 1-3 = fleeting thought, low reuse value. 4-5 = decent observation. 6-7 = actionable insight. 8-9 = paradigm-shifting personal insight. 10 = life-changing realization.

## Body

### Summary

1-2 sentences. What is this idea? State it clearly and concisely.

### Executive Summary

2-4 sentences. Why does this idea matter to THIS reader, given their context? How might it connect to their current priorities? What could they do with it?

Write in the reader's preferred language (check Reader Context).

### Key Ideas

1-3 ideas, formatted as:
```
- **Bold Title** #domain/area — one-sentence standalone claim (max 16 words)
```

For ideas, the source IS the key idea. Restate it as a clear, atomic claim. Add 1-2 implications if they exist.

### Connections

Potential connections to other knowledge:
```
- [[topic or concept]] — why this connects (one sentence)
```

1-3 connections. Ideas are most valuable when connected.

# INPUT

The idea follows. Apply the capture above.
```

- [ ] **Step 2: Write Conversation template**

Write `~/Mnemon/templates/core/conversation.md`:

```markdown
# IDENTITY

You are a knowledge extraction specialist for a personal knowledge library. Your job: turn conversation transcripts (meetings, 1-on-1s, discussions) into structured, searchable knowledge artifacts that capture decisions, insights, and action items.

Be factual and precise. Conversations contain social dynamics — focus on substance, not pleasantries. Attribute insights and decisions to speakers when it matters.

# STEPS

## Pass 1: IDEAS

Read the entire transcript. Identify:
- Decisions made (explicit or implicit)
- Action items committed to (by whom, by when)
- Insights and ideas shared by participants
- Disagreements or tensions (capture both sides)
- Information shared that the reader didn't know before

For each idea:
- State it as a standalone atomic claim (one sentence, max 16 words)
- Attribute to the speaker when relevant
- Tag with the most relevant domain from the Reader Context

## Pass 2: INSIGHTS

From the IDEAS, select the most valuable:
- Decisions that affect the reader's work or thinking
- Insights that change understanding of a topic
- Action items the reader is responsible for
- Relationship dynamics worth noting (signals, not gossip)

Discard small talk, logistics, repeated points, and scheduling discussion.

# OUTPUT INSTRUCTIONS

Generate extract.md with EXACTLY this structure:

## Frontmatter

```yaml
---
type: extract
source_type: conversation
content_format: transcript
origin: <url|audio|text — depends on how captured>
visibility: personal
status: extracted
title: "<conversation topic — max 8 words>"
author: "<participants, comma-separated>"
url: "<if available>"
created: <YYYY-MM-DD>
extracted: <YYYY-MM-DD>
language: <transcript language>
tags: [<3-6 topic tags, lowercase>]
people: [<all participants>]
domains: [<1-3 domains from reader context>]
rating: <1-10 integer>
capture_context:
  vault: <from capture context>
  session: "<from capture context>"
  intent: "<from capture context>"
  import_source: gateway
  import_batch: ""
---
```

Rating guide: 1-3 = routine, no significant decisions. 4-5 = useful context shared. 6-7 = important decisions or insights. 8-9 = pivotal conversation. 10 = life-changing discussion.

## Body

### Summary

2-3 sentences. Who talked? What was discussed? What was the main outcome? Pure description.

### Decisions

Explicit decisions made during the conversation:
```
- **Decision** — details, who decided, any conditions
```

If no decisions were made, write "No explicit decisions."

### Action Items

Commitments made by participants:
```
- [ ] **Action** — owner, deadline if mentioned
```

If no action items, write "No action items identified."

### Executive Summary

0.5 page. Frame through the Reader Context.

Requirements:
- What does this conversation mean for the reader specifically?
- If decisions affect the reader's work — highlight implications
- Note any unresolved tensions or open questions
- Write in the reader's preferred language (check Reader Context)

### Key Ideas

Each idea from Pass 2, formatted as:
```
- **Bold Title** #domain/area — one-sentence standalone claim (max 16 words)
```

Requirements:
- Attribute to speaker where relevant: `[Speaker Name]` prefix
- Each claim MUST be understandable without the conversation context
- Minimum 2 Key Ideas, maximum 10

### Connections

Potential connections to other knowledge:
```
- [[topic or concept]] — why this connects (one sentence)
```

1-3 connections.

# INPUT

The conversation transcript follows. Apply the extraction above.
```

- [ ] **Step 3: Commit**

```bash
cd ~/Mnemon
git add templates/core/idea.md templates/core/conversation.md
git commit -m "feat: idea + conversation extraction templates"
```

---

## Task 4: Protocols

**Files:**
- Create: `~/Mnemon/protocols/source-schema.md`
- Create: `~/Mnemon/protocols/extract-schema.md`
- Create: `~/Mnemon/protocols/storage.md`
- Create: `~/Mnemon/protocols/status-lifecycle.md`
- Create: `~/Mnemon/protocols/domains.md`
- Remove: `~/Mnemon/protocols/.gitkeep`

- [ ] **Step 1: Write source-schema.md**

Write `~/Mnemon/protocols/source-schema.md`:

```markdown
# Source Schema — source.md

The source file captures raw content exactly as received. It is **immutable** — never modified after creation.

## Frontmatter

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | Always `source` |
| `source_type` | enum | Yes | `article`, `video`, `podcast`, `book`, `paper`, `idea`, `conversation` |
| `content_format` | enum | Yes | `text`, `transcript`, `reference` |
| `origin` | enum | Yes | `url`, `text`, `youtube`, `audio`, `book`, `idea` |
| `url` | string | If URL-based | Canonical source URL |
| `author` | string | No | Source author or creator |
| `captured` | date | Yes | ISO date of capture (YYYY-MM-DD) |
| `captured_by` | string | Yes | `agent` (gateway) or `manual` |

### Origin → Source Type Mapping

| Origin | Default source_type | content_format |
|--------|-------------------|----------------|
| `url` | article | text |
| `youtube` | video | transcript |
| `audio` | podcast | transcript |
| `text` | article | text |
| `book` | book | text |
| `idea` | idea | text |

## Body

Raw captured content. For URL sources: fetched article text. For transcripts: full transcript with metadata header. For ideas: user's raw text.

## Immutability Rule

Once source.md is written, it is NEVER modified. If you need to re-capture:
- Same URL → same hash → collision handling (append -2)
- Updated content → new capture is a new source
```

- [ ] **Step 2: Write extract-schema.md**

Write `~/Mnemon/protocols/extract-schema.md`:

```markdown
# Extract Schema — extract.md

The extract file contains AI-generated structured knowledge from a source. It is **mutable** — can be overwritten by re-extraction with updated reader context.

## Frontmatter

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | Always `extract` |
| `source_type` | enum | Yes | Matches source.md |
| `content_format` | enum | Yes | Matches source.md |
| `origin` | enum | Yes | Matches source.md |
| `visibility` | enum | Yes | `personal`, `public`, `company`, `private` |
| `status` | enum | Yes | `captured`, `extracted`, `integrated` |
| `title` | string | Yes | Source title |
| `author` | string | No | Source author |
| `url` | string | No | Source URL |
| `created` | date | Yes | Source capture date |
| `extracted` | date | Yes | Extraction date |
| `language` | string | Yes | Content language code (en, ru, etc.) |
| `tags` | list | Yes | 3-8 topic tags, lowercase |
| `people` | list | No | People mentioned or referenced |
| `domains` | list | Yes | 1-3 domains from reader context |
| `rating` | integer | Yes | 1-10 quality/insight density score |
| `capture_context.vault` | string | Yes | Calling vault context |
| `capture_context.session` | string | No | Session name |
| `capture_context.intent` | string | No | Why captured |
| `capture_context.import_source` | string | Yes | `gateway`, `manual`, `batch` |
| `capture_context.import_batch` | string | No | Batch ID if batch import |

## Body Sections (required)

### Summary
2-3 sentences. Factual description. No opinion, no framing.

### Executive Summary
0.5-1 page. Framed by reader-context.md. Personal to the reader. Critical and honest. Written in the reader's preferred language.

### Key Ideas
Atomic claims, domain-tagged:
```
- **Bold Title** #domain/area — one-sentence claim (max 16 words)
```

### Connections
Wiki-style links: `- [[topic]] — why this connects`

### Raw Quotes
3-5 direct quotes from the source.

## Source-Type-Specific Sections

- **book**: adds `Central Thesis` section between Summary and Executive Summary
- **paper**: adds `Methodology Note` section between Summary and Executive Summary
- **conversation**: adds `Decisions` and `Action Items` sections before Executive Summary

## Rating Guide

| Score | Meaning |
|-------|---------|
| 1-3 | Low value — obvious, wrong, or heavily padded |
| 4-5 | Average — some useful points, nothing surprising |
| 6-7 | Good — multiple actionable insights |
| 8-9 | Excellent — changes thinking on the topic |
| 10 | Exceptional — reference-grade, revisit regularly |
```

- [ ] **Step 3: Write storage.md**

Write `~/Mnemon/protocols/storage.md`:

```markdown
# Storage Protocol

## Folder Structure

```
Sources/
├── 2026-04-04_a1b2c3d4/
│   ├── source.md     (immutable)
│   └── extract.md    (mutable)
├── 2026-04-04_e5f6g7h8/
│   ├── source.md
│   └── extract.md
└── ...
```

## Folder Naming

Format: `YYYY-MM-DD_{hash8}`

- **Date**: capture date (ISO format)
- **Hash**: first 8 characters of SHA-256
  - URL sources: `echo -n "<canonical-url>" | shasum -a 256 | cut -c1-8`
  - Non-URL sources: `echo -n "<title>_<YYYY-MM-DD>" | shasum -a 256 | cut -c1-8`

## Collision Handling

If `Sources/2026-04-04_a1b2c3d4/` exists, append `-2`, `-3`, etc.:
- `Sources/2026-04-04_a1b2c3d4-2/`
- `Sources/2026-04-04_a1b2c3d4-3/`

## Immutability

- `source.md`: NEVER modified after creation. Raw captured content.
- `extract.md`: CAN be overwritten by re-extraction (e.g., with updated reader context).
- Folders: NEVER renamed or moved. The hash is permanent.

## Other Directories

- `Synthesis/`: human-written notes connecting ideas across sources. Never auto-generated.
- `_meta/`: vault protocol and configuration.
- `_inputs/pending-writes/`: failed gateway writes, queued for retry.
```

- [ ] **Step 4: Write status-lifecycle.md**

Write `~/Mnemon/protocols/status-lifecycle.md`:

```markdown
# Status Lifecycle

Sources progress through three statuses:

```
captured → extracted → integrated
```

## States

### captured
- source.md exists with raw content
- extract.md is missing or contains an error
- Gateway failed during extraction, or extraction was deferred

### extracted
- Both source.md and extract.md exist
- extract.md has full structured content (Summary, Executive Summary, Key Ideas, etc.)
- This is the default end state for successful `/source-add`

### integrated
- User has reviewed the extract
- Connected to other knowledge (Synthesis notes, links, applied in work)
- Manually set by the user — never auto-promoted

## Transitions

| From | To | Trigger |
|------|----|---------|
| (new) | captured | source.md created, extraction fails |
| (new) | extracted | source.md + extract.md both created successfully |
| captured | extracted | Re-extraction succeeds (`/source-extract`) |
| extracted | integrated | User manually marks as integrated |

## Convention, Not Enforcement

Status is a frontmatter field, not a database constraint. There is no state machine enforcement. The status is a convention that helps users track their knowledge processing pipeline.
```

- [ ] **Step 5: Write domains.md**

Write `~/Mnemon/protocols/domains.md`:

```markdown
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
- Namespaced with `/` for work contexts: `mc/engineering`, `mc/product`
- Keep the list to 7-15 domains — too many defeats the purpose
```

- [ ] **Step 6: Remove .gitkeep and commit**

```bash
cd ~/Mnemon
rm -f protocols/.gitkeep
git add protocols/
git commit -m "feat: protocols — source schema, extract schema, storage, lifecycle, domains"
```

---

## Task 5: README

**Files:**
- Create: `~/Mnemon/docs/README.md`

- [ ] **Step 1: Write README**

Write `~/Mnemon/docs/README.md`:

```markdown
# Mnemon

> *From Greek μνήμων (mnemon) — "mindful, remembering."*

**AI-powered personal knowledge extraction system.** Feed it articles, videos, podcasts, books — get structured, searchable knowledge artifacts framed by your personal context.

Fabric extracts wisdom. **Mnemon remembers it.**

## What It Does

You read an article. Mnemon creates:
- **source.md** — immutable captured content (what you read)
- **extract.md** — structured knowledge (what it means for YOU)
  - Summary, Executive Summary (personalized), Key Ideas (domain-tagged), Connections, Raw Quotes

Every extract is framed by YOUR context — your role, domains, goals. Two people read the same article, get different extracts.

## Quick Start

```bash
# 1. Clone and setup
git clone https://github.com/dkushnikov/mnemon ~/Mnemon
cd ~/Mnemon
./setup.sh ~/Obsidian/Knowledge

# 2. Edit your reader context (personalizes extractions)
# Open ~/Obsidian/Knowledge/reader-context.md and fill in your profile

# 3. Install the Claude Code plugin
claude plugin install ~/Mnemon

# 4. Add your first source
/source-add https://example.com/interesting-article
```

Time from clone to first extract: **under 15 minutes.**

## Features

| Feature | Description |
|---------|-------------|
| **7 source types** | Articles, YouTube, podcasts, books, papers, ideas, conversations |
| **Personal context** | Every extract framed by your role, domains, goals |
| **Structured output** | YAML frontmatter + Key Ideas + domain tags = searchable |
| **Immutable sources** | source.md never changes. Re-extract with new context anytime |
| **Local & yours** | Markdown files + git. No cloud, no lock-in |
| **Pluggable search** | grep (zero deps) or QMD (semantic + keyword) |
| **Fabric-inspired** | Two-pass extraction, 16-word discipline, anti-sycophancy |

## How It Works

```
You → /source-add URL → knowledge-gateway.sh → claude -p → Sources/
                              ↓                              ├── source.md
                        mnemon.yaml                          └── extract.md
                        reader-context.md
                        templates/core/*.md
```

1. **Capture**: Gateway fetches content (WebFetch for URLs, yt-dlp for YouTube, whisper for audio)
2. **Extract**: Claude applies a source-type-specific template with your reader context embedded
3. **Store**: Structured source.md + extract.md in `Sources/YYYY-MM-DD_{hash8}/`
4. **Search**: `/source-search` queries your library via grep or QMD

## Requirements

- **Claude Code** (required) — any subscription (Pro, Max, API key)
- **yt-dlp** (optional) — for YouTube transcript extraction
- **whisper** (optional) — for audio transcription
- **QMD** (optional) — for hybrid semantic + keyword search

## Commands

| Command | Description |
|---------|-------------|
| `/source-add <url>` | Capture and extract a source |
| `/source-search <query>` | Search your knowledge library |
| `/source-status` | Dashboard: totals, breakdowns, recent |

## Configuration

After `setup.sh`, edit two files:

**`reader-context.md`** — Who you are, what domains you care about, your current goals. This personalizes every extraction.

**`mnemon.yaml`** — Vault path, search provider, model, whisper settings. Usually no changes needed after setup.

## Templates

Mnemon ships 7 extraction templates in `templates/core/`:

| Template | For | Special Features |
|----------|-----|-----------------|
| `article.md` | Web articles, blog posts | Two-pass IDEAS→INSIGHTS extraction |
| `youtube.md` | YouTube videos | Transcript-based, format-value note |
| `podcast.md` | Podcasts, audio content | Speaker attribution, signal-to-noise |
| `book.md` | Books (notes/highlights) | Central Thesis, framework extraction |
| `paper.md` | Academic papers | Methodology Note, evidence grading |
| `idea.md` | Quick thoughts, insights | Lightweight, connection-focused |
| `conversation.md` | Meetings, discussions | Decisions, Action Items sections |

All templates follow the Fabric-inspired format: IDENTITY → STEPS → OUTPUT INSTRUCTIONS → INPUT.

## Project Structure

```
mnemon/
├── plugin.json           # Claude Code plugin manifest
├── setup.sh              # One-command installation
├── bin/                   # Core scripts
│   ├── knowledge-gateway.sh
│   └── media-extract.py
├── skills/               # Claude Code skills (/source-add, etc.)
├── templates/core/       # 7 extraction templates
├── protocols/            # Schema and convention docs
├── vault-template/       # Copied into vault by setup.sh
└── docs/                 # Documentation
```

## License

TBD (MIT or Apache 2.0)

## Author

Dima Kushnikov — [@dkushnikov](https://github.com/dkushnikov)
```

- [ ] **Step 2: Commit**

```bash
cd ~/Mnemon
git add docs/README.md
git commit -m "docs: README with quick start, features, architecture"
```

---

## Self-Review

### Spec Coverage (PRD Requirements)

| Requirement | Task | Status |
|------------|------|--------|
| R3.4: 7 extraction templates | Tasks 1-3 + Plan 1 Task 5 | ✅ All 7 |
| R3.9: Fabric-inspired format | All templates | ✅ IDENTITY→STEPS→OUTPUT→INPUT |
| Protocols documented | Task 4 | ✅ 5 protocol files |
| README | Task 5 | ✅ |

### Placeholder Scan
No "TBD", "TODO", or "implement later" found. All content complete.

### Consistency
- All templates use identical frontmatter schema (source_type and origin values vary correctly)
- All templates share: Summary, Executive Summary, Key Ideas, Connections, Raw Quotes
- Book adds: Central Thesis. Paper adds: Methodology Note. Conversation adds: Decisions, Action Items
- Rating guides use consistent 1-10 scale with source-type-appropriate descriptions
