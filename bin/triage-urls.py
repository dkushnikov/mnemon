#!/usr/bin/env python3
"""triage-urls.py — content-aware URL triage (thin spike, v0.1)

Per spec ~/Mnemon/docs/specs/2026-05-31-content-aware-url-triage.md (v2).

SPIKE SCOPE:
  - Tier 1 (URL pattern + Knowledge dedup + hard-skip rules)
  - Tier 2 (defuddle peek + substance score + domain classification)
  - Cluster detection (orthogonal flags, NOT verdict override per v2)
  - Output doc with foldable callouts + evidence snippets
  - Reader Context staleness warning

NOT IN SPIKE (deferred to full MVP):
  - Per-URL JSON cache (re-run from scratch each time)
  - Action executor `apply --checked` (output is read-only)
  - Tier 3 LLM judgment (Tier 2 confident verdicts for all)
  - Calibration loop (no override log yet)
  - Bookmark via gateway (verdict is just "bookmark" string in output)

Usage:
    triage-urls.py <input> [--vault PATH] [--output PATH] [--limit N]

INPUT: markdown file with ``url`` checklist items (extracts URLs) OR
       plain text file with one URL per line.
"""

import argparse
import json
import re
import subprocess
import sys
import urllib.parse
from collections import defaultdict, Counter
from datetime import datetime, timezone, date
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────
# URL normalization (reuse from existing pipeline)
# ──────────────────────────────────────────────────────────────────────

TRACKING = {'fbclid', 'gclid', 'msclkid', 'dclid', 'yclid', '_ga', '_gl',
            '_hsenc', '_hsmi', '_openstat', 'mc_cid', 'mc_eid', 'igshid', 'igsh',
            'srsltid', 'segment_id', 'campaignid', 'adgroupid'}
TRACKING_PREFIXES = ('utm_', 'ga_', 'hsa_', 'vero_', 'oly_', 'pk_', 'piwik_',
                     'cm_', 'mtm_', 'gad_')


def normalize_url(u: str) -> str:
    try:
        p = urllib.parse.urlparse(u.strip())
    except Exception:
        return u
    if not p.scheme:
        return u

    # v0.5: Strip auth-redirect wrapper.
    # If URL is /login (or similar) and has ?next=X (or redirect=X) where X
    # is a URL in same domain, use X as canonical instead of the auth gate.
    path_lower = p.path.lower()
    if AUTH_PATH_RE_FOR_NORM.search(path_lower):
        q_dict = dict(urllib.parse.parse_qsl(p.query))
        for redirect_key in ('next', 'redirect', 'return', 'returnurl', 'return_to', 'r'):
            if redirect_key in q_dict:
                dest = urllib.parse.unquote(q_dict[redirect_key])
                if dest.startswith('http'):
                    try:
                        dest_host = urllib.parse.urlparse(dest).hostname or ''
                        if dest_host.lower() == p.netloc.lower():
                            return normalize_url(dest)
                    except Exception:
                        pass
                break

    # v0.6: Aggregator redirect — community.bvp.com/links?url=X, similar share
    # patterns. Extract destination URL even if cross-domain (the destination
    # IS what we care about, not the share/wrapper service).
    q_dict_all = dict(urllib.parse.parse_qsl(p.query))
    for redirect_key in ('url', 'u', 'target', 'link'):
        if redirect_key in q_dict_all:
            dest = urllib.parse.unquote(q_dict_all[redirect_key])
            if dest.startswith('http'):
                # Only follow if dest is plausibly an article URL (has path)
                try:
                    dest_p = urllib.parse.urlparse(dest)
                    if dest_p.hostname and dest_p.path and dest_p.path != '/':
                        return normalize_url(dest)
                except Exception:
                    pass

    # v0.6: GitHub /tree/main and /tree/master are default branches = repo root.
    # Strip them so GH_REPO_ROOT_RE catches the URL as repo root.
    if 'github.com' in p.netloc.lower():
        path = re.sub(r'/tree/(main|master)/?$', '/', p.path)
    else:
        path = p.path

    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query)
         if k.lower() not in TRACKING
         and not any(k.lower().startswith(pr) for pr in TRACKING_PREFIXES)]
    new = urllib.parse.urlunparse((p.scheme.lower(), p.netloc.lower(),
                                    path, p.params, urllib.parse.urlencode(q),
                                    p.fragment))
    return new.rstrip('?')


AUTH_PATH_RE_FOR_NORM = re.compile(r'/(login|signin|signup|auth|oauth)')


def host_of(u: str) -> str:
    try:
        return (urllib.parse.urlparse(u).hostname or '').lower().lstrip('www.')
    except Exception:
        return ''


# ──────────────────────────────────────────────────────────────────────
# Tier 1: URL pattern + dedup + hard-skip rules
# ──────────────────────────────────────────────────────────────────────

JUNK_PATTERNS = [re.compile(p) for p in [
    r'^https?://(www\.)?google\.com/search',
    r'^https?://(www\.)?bing\.com/search',
    r'^https?://duckduckgo\.com/',
    r'^https?://chatgpt\.com/',
    r'^https?://claude\.ai/(chat|new|artifacts|recents)',
    r'^https?://t\.me/.+bot',
]]

LIFESTYLE_HOSTS = ['hotels.com', 'sirhotels', 'kitkat', 'cabaret', 'sothebysrealty',
                   'airbnb.', 'booking.com', 'amazon.', 'naenka.com',
                   'bestproductsreviews', 'getcourse.ru', 'fibonacciku.com',
                   'unriddle.ai', 'visithealth', 'weroad', 'luma.com',
                   'bungu.store', '5prismcoaching']

AUTH_PATH_RE = re.compile(r'/(login|signin|signup|auth|account|dashboard|profile|checkout|cart|dealPay|sales/shop)')
GH_DEEPLINK_RE = re.compile(r'github\.com/.+/(blob|tree|commit|issues|pull|wiki|releases)/')


def tier1(url: str, normalized: str, known_index: dict) -> dict | None:
    """Return verdict dict if Tier 1 decides; None means → Tier 2.

    v0.6: all pattern checks now run against NORMALIZED URL (not original) so
    that auth-wrappers / aggregator-redirects stripped in normalize_url take
    effect before hard-skip patterns are evaluated.
    """
    if normalized in known_index:
        return {'verdict': 'skip', 'reason': 'duplicate', 'note': f'already in Knowledge as {known_index[normalized]}', 'tier': 1}
    if any(p.search(normalized) for p in JUNK_PATTERNS):
        return {'verdict': 'skip', 'reason': 'junk', 'note': 'search/chat/bot URL', 'tier': 1}
    if AUTH_PATH_RE.search(normalized.lower()):
        return {'verdict': 'skip', 'reason': 'auth', 'note': 'auth/checkout page', 'tier': 1}
    h = host_of(normalized)
    if any(lh in h for lh in LIFESTYLE_HOSTS):
        return {'verdict': 'skip', 'reason': 'lifestyle', 'note': 'shopping/transactional', 'tier': 1}
    if GH_DEEPLINK_RE.search(normalized):
        return {'verdict': 'skip', 'reason': 'deeplink', 'note': 'GitHub specific file/issue', 'tier': 1}
    return None


# ──────────────────────────────────────────────────────────────────────
# Tier 2: defuddle fetch + substance score + domain classification
# ──────────────────────────────────────────────────────────────────────

def fetch_content(url: str, timeout: int = 45) -> tuple[str | None, str | None]:
    """Return (content, error). content is markdown from defuddle, or None on failure."""
    try:
        r = subprocess.run(['defuddle', 'parse', url, '--md'],
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            err = (r.stderr or '').strip().split('\n')[0][:80]
            return None, f'defuddle-failed: {err or "rc=" + str(r.returncode)}'
        content = (r.stdout or '').strip()
        if not content or len(content) < 50:
            return None, 'empty-content'
        return content, None
    except subprocess.TimeoutExpired:
        return None, 'timeout'
    except FileNotFoundError:
        return None, 'defuddle-not-installed'
    except Exception as e:
        return None, f'error: {type(e).__name__}'


# Domain classification: host hints + content keywords
DOMAIN_HOST_HINTS = {
    'github.com': ['mc/ai', 'mc/engineering', 'learning'],
    'gitlab.com': ['mc/engineering', 'learning'],
    'reminder.media': ['health', 'inner-work', 'learning'],
    'pimenov.ai': ['mc/product', 'learning', 'mc/ai'],
    'tobi.lutke.com': ['career', 'mc/strategy'],
    'platformer.news': ['mc/ai', 'mc/strategy'],
    'thinkingaboutthinking.org': ['inner-work', 'learning'],
    'acharyaprashant.org': ['inner-work'],
    'tim.blog': ['learning', 'health', 'inner-work'],
    'theatlantic.com': ['learning', 'culture'],
    'newyorker.com': ['learning', 'culture'],
    'paulgraham.com': ['career', 'learning'],
    'stratechery.com': ['mc/strategy', 'mc/ai'],
    'refactoring.fm': ['mc/engineering', 'career', 'learning'],
    'zamesin.ru': ['mc/product', 'mc/strategy'],
    'gopractice.ru': ['mc/product', 'learning'],
    'medium.com': ['learning'],
    'fastfounder.ru': ['mc/strategy', 'career'],
    'meduza.io': ['culture'],
    'seekingalpha.com': ['finance'],
    'techradar.com': ['mc/engineering'],
    'popsci.com': ['learning', 'health'],
    'cursor.com': ['mc/ai', 'mc/engineering'],
    'anthropic.com': ['mc/ai'],
    'openai.com': ['mc/ai'],
    'arxiv.org': ['learning', 'mc/ai'],
    'mesa.dev': ['mc/ai', 'mc/engineering'],
    'drpozdniakov.github.io': ['mc/ai', 'mc/engineering'],
    'thariqs.github.io': ['mc/engineering', 'learning'],
}

DOMAIN_KEYWORDS = {
    'mc/ai': ['agent', 'agents', 'llm', 'claude', 'gpt', 'openai', 'anthropic',
              'ai-driven', 'embedding', 'rag', 'mcp', 'fine-tune', 'inference',
              'prompt', 'tokens', 'autoregressive', 'transformer'],
    'mc/engineering': ['kubernetes', 'docker', 'aws', 'gcp', 'azure', 'database',
                       'api', 'graphql', 'devops', 'ci/cd', 'observability',
                       'deployment', 'microservice', 'lambda', 'serverless'],
    'mc/product': ['jtbd', 'product-market', 'roadmap', 'feature', 'ux', 'a/b test',
                   'metric', 'retention', 'activation', 'product manager'],
    'mc/growth': ['conversion', 'funnel', 'campaign', 'paid ads', 'seo',
                  'attribution', 'pricing', 'monetization', 'creator economy',
                  'aeo', 'geo'],
    'mc/people': ['hiring', 'performance', 'org design', 'compensation', 'culture',
                  'leadership', 'manager', 'people ops', 'hr'],
    'mc/operations': ['process', 'workflow', 'sop', 'compliance', 'automation',
                      'project management'],
    'mc/strategy': ['strategy', 'positioning', 'moat', 'competitor', 'tam',
                    'market', 'thesis', 'platform', 'unit economics'],
    'mc/legal': ['gdpr', 'license', 'contract', 'compliance', 'privacy', 'regulation'],
    'inner-work': ['mood', 'emotion', 'mindfulness', 'meditation', 'therapy',
                   'self-aware', 'ego', 'shadow', 'coaching', 'values',
                   'identity', 'psychotherapy'],
    'health': ['nutrition', 'exercise', 'sleep', 'fitness', 'longevity',
               'biomarker', 'vo2', 'protein', 'diet', 'workout', 'hrv'],
    'learning': ['mental model', 'framework', 'principle', 'thinking', 'study',
                 'cognition', 'expertise'],
    'career': ['exit', 'founder', 'ceo', 'leadership', 'executive',
               'professional', 'team building'],
    'finance': ['investment', 'portfolio', 'stocks', 'equity', 'valuation',
                'wealth', 'tax'],
    'relationships': ['family', 'partner', 'friendship', 'social capital', 'network'],
    'culture': ['art', 'music', 'film', 'travel', 'cuisine', 'fashion'],
    'home': ['interior', 'apartment', 'design home', 'kitchen', 'gardening'],
    'influence': ['speaking', 'audience', 'personal brand', 'newsletter',
                  'content marketing'],
}


def classify_domain(url: str, content: str | None) -> tuple[str, float]:
    """Return (primary_domain, confidence 0-1)."""
    h = host_of(url)
    text = (content or '').lower()
    scores = Counter()

    # Host hint: strong signal
    for hint_host, domains in DOMAIN_HOST_HINTS.items():
        if hint_host in h:
            for d in domains:
                scores[d] += 5

    # Content keyword: weaker signal
    for dom, kws in DOMAIN_KEYWORDS.items():
        for kw in kws:
            if kw in text:
                scores[dom] += 1

    if not scores:
        return ('learning', 0.1)  # weak fallback

    primary, top_score = scores.most_common(1)[0]
    total = sum(scores.values())
    confidence = round(top_score / total, 2) if total else 0.0
    return (primary, confidence)


def substance_score(content: str | None) -> int:
    """Heuristic 1-5: fetch quality + length + structure."""
    if not content:
        return 1
    words = len(content.split())
    if words < 200:
        return 1  # blurb / nav-page
    headers = content.count('\n## ') + content.count('\n### ')
    codes = content.count('```')
    if words >= 2000 and (headers >= 3 or codes >= 2):
        return 5  # deep / framework / tutorial
    if words >= 1000:
        return 4 if headers >= 2 or codes >= 1 else 3
    if words >= 500:
        return 3
    return 2


# HIGH-signal known hosts — override substance to 4 minimum
# (Authors / publications consistently producing capture-worthy content)
HIGH_SIGNAL_HOSTS = {
    'tobi.lutke.com': 5,
    'platformer.news': 5,
    'pimenov.ai': 5,
    'stratechery.com': 5,
    'paulgraham.com': 4,
    'acharyaprashant.org': 4,
    'thinkingaboutthinking.org': 4,
    'tim.blog': 4,
    'theatlantic.com': 4,
    'newyorker.com': 4,
    'reminder.media': 4,
    'refactoring.fm': 4,
    'zamesin.ru': 4,
    'newsletter.pragmaticengineer.com': 4,
    # v0.5 additions from Dima's spike feedback (2026-05-31)
    'codespeak.dev': 4,
    'aicouncil.com': 4,
    'fastcompany.com': 4,
}


def apply_host_substance_override(url: str, base_substance: int) -> int:
    """Boost substance to host's known-signal level if host is in override list."""
    h = host_of(url)
    for hint_host, min_substance in HIGH_SIGNAL_HOSTS.items():
        if hint_host in h:
            return max(base_substance, min_substance)
    return base_substance


# v0.5: GitHub repo root URL (no deeplink) — repo existence is signal even if
# README is thin. Minimum substance = 3 so it gets bookmark verdict on med-bar.
GH_REPO_ROOT_RE = re.compile(r'^https?://github\.com/[^/]+/[^/]+/?$')


def apply_github_repo_boost(url: str, base_substance: int) -> int:
    """GitHub repo root URLs get min substance 3 (defuddle often returns thin README,
    but the project itself has signal — preserve as bookmark via med-bar bookmark rule).
    """
    if GH_REPO_ROOT_RE.match(url):
        return max(base_substance, 3)
    return base_substance


# ──────────────────────────────────────────────────────────────────────
# Verdict logic per per-domain bar
# ──────────────────────────────────────────────────────────────────────

HIGH_BAR_DOMAINS = {'mc/strategy', 'mc/legal', 'mc/people',
                    'inner-work', 'finance', 'relationships', 'culture', 'home'}


def verdict_from(substance: int, domain: str) -> str:
    """v0.5: false-skip asymmetry fix — substance=2 + med-bar now bookmark, not skip.
    Preserves pointer when unsure; high-bar still skips low-substance (culture/news/etc
    don't benefit from low-quality pointers)."""
    is_high = domain in HIGH_BAR_DOMAINS
    if substance >= 5:
        return 'capture'
    if substance == 4:
        return 'bookmark' if is_high else 'capture'
    if substance == 3:
        return 'skip' if is_high else 'bookmark'
    if substance == 2:
        return 'skip' if is_high else 'bookmark'  # CHANGED v0.5
    return 'skip'  # substance 1: skip everywhere (fetch-failed/empty)


# ──────────────────────────────────────────────────────────────────────
# Cluster detection
# ──────────────────────────────────────────────────────────────────────

STOPWORDS = set("""
the a an and or but for to in on at of with from by is are was were be been being
have has had do does did will would should could may might must can shall ought
this that these those there here it its his her our their your my me you we us them
about after again against all also any because before below between both each few
how into more most no nor not now off once only other out over same some such than
too under until up very what when where which who whom why some however thus then
как что это для или но если так чтобы тоже также его её их мой моя моё их также если
чтобы быть была были был есть нет очень всё всех всем все эту эта это этот эти этого
""".split())


def clean_content_for_keywords(content: str) -> str:
    """Strip markdown noise that pollutes keyword extraction:
       image markdown ![...](...), URLs, code blocks, link refs [text](url) → text."""
    # Code blocks (multiline)
    content = re.sub(r'```[\s\S]*?```', ' ', content)
    content = re.sub(r'`[^`\n]+`', ' ', content)  # inline code
    # Image markdown: ![alt](url) → strip entirely (alt text often noise like filenames)
    content = re.sub(r'!\[[^\]]*\]\([^)]+\)', ' ', content)
    # Link markdown: [text](url) → keep text only
    content = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', content)
    # Raw URLs
    content = re.sub(r'https?://\S+', ' ', content)
    # HTML tags
    content = re.sub(r'<[^>]+>', ' ', content)
    return content


# Spike v0.4 limitation: heuristic title-Jaccard clustering catches OBVIOUS
# clusters (e.g., 3 URLs from same domain about same topic) but misses
# semantically-similar-but-syntactically-different titles (e.g., "claude-code
# agent" vs "agent skills manager" vs "agentic context engineering" — same
# concept, different surface tokens). Full MVP should add Tier 3 LLM-driven
# cluster detection for this case.

# Domain-specific noise tokens to filter from clustering signal
TITLE_NOISE_TOKENS = {'github', 'gitlab', 'youtube', 'medium', 'twitter', 'linkedin',
                       'reddit', 'website', 'home', 'page'}


def _tokenize(text: str) -> set:
    """Tokenize on word boundaries — split on ANY non-word incl. hyphen/underscore.
    Ensures 'free-claude-code' and 'claude_code_agent_farm' both yield
    {claude, code, agent, ...} for proper matching."""
    text = text.lower()
    raw = re.split(r'[^a-zа-яё]+', text)
    return {w for w in raw
            if len(w) >= 4
            and w not in STOPWORDS
            and w not in TITLE_NOISE_TOKENS}


def extract_keywords_titled(title: str) -> set:
    """Title-only keywords (clean signal, used for clustering)."""
    return _tokenize(title or '')


def extract_keywords(title: str, content: str | None, max_words: int = 500) -> set:
    """Title + content keywords (used for agenda matching, NOT clustering)."""
    cleaned_content = clean_content_for_keywords(content or '')[:max_words * 6]
    return _tokenize((title or '') + ' ' + cleaned_content)


def detect_clusters(items: list[dict], threshold: float = 0.25, min_size: int = 3) -> list[dict]:
    """Group items by domain, then cluster within domain via TITLE-only keyword Jaccard.

    v0.3 fixes:
      - Title-only Jaccard (drop content — was being polluted by image filenames, URLs, code)
      - Threshold 0.25 (title is sparse, generous matching needed)
      - Include all items with title + domain (orthogonal to verdict)
      - Stable seed-keyword signature (no tightening)
    """
    by_domain = defaultdict(list)
    for item in items:
        if item.get('domain') and item.get('title'):
            by_domain[item['domain']].append(item)

    clusters = []
    for domain, group in by_domain.items():
        if len(group) < min_size:
            continue
        # Title-only keywords for clustering — cleaner signal
        kw_sets = [(it, extract_keywords_titled(it.get('title', ''))) for it in group]
        used = set()
        for i, (it_i, kws_seed) in enumerate(kw_sets):
            if i in used or len(kws_seed) < 2:
                continue
            cluster = [it_i]
            used.add(i)
            for j, (it_j, kws_j) in enumerate(kw_sets):
                if j in used or j == i or len(kws_j) < 2:
                    continue
                jaccard = len(kws_seed & kws_j) / max(1, len(kws_seed | kws_j))
                if jaccard >= threshold:
                    cluster.append(it_j)
                    used.add(j)
            if len(cluster) >= min_size:
                # Common across cluster (intersection of title keywords)
                all_kws = [extract_keywords_titled(c.get('title', '')) for c in cluster]
                common = set.intersection(*all_kws) if all_kws else set()
                if not common:
                    common = kws_seed
                common_sorted = sorted(common)[:3] or [domain.replace('/', '-')]
                name = '-'.join(common_sorted)
                clusters.append({
                    'name': name,
                    'domain': domain,
                    'items': cluster,
                    'common_keywords': sorted(common)[:5],
                })

    # Tag items orthogonally (don't override verdict)
    for c in clusters:
        for it in c['items']:
            it['cluster_id'] = c['name']
            it['synthesis_candidate'] = True

    return clusters


# ──────────────────────────────────────────────────────────────────────
# Input parsing & Knowledge dedup index
# ──────────────────────────────────────────────────────────────────────

def load_knowledge_index(vault_path: Path) -> dict:
    """Return {normalized_url: source_folder_name}."""
    index = {}
    for f in (vault_path / 'Sources').glob('*/source.md'):
        folder = f.parent.name
        try:
            with open(f, encoding='utf-8', errors='ignore') as fp:
                for line in fp:
                    m = re.match(r'^url:\s*"?([^"\n]+?)"?\s*$', line.strip())
                    if m:
                        nu = normalize_url(m.group(1))
                        if nu:
                            index.setdefault(nu, folder)
                        break
        except Exception:
            continue
    return index


def parse_input(path: Path) -> list[dict]:
    """Extract URL + optional title from input file.
    Supports our triage-pending.md format (`url` in backticks) or plain URL list.
    """
    text = path.read_text()
    items = []
    seen = set()

    # Try markdown triage format first: look for - [ ] **Title** + url + reason
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = re.match(r'^- \[\s*[xX ]?\s*\] \*\*(.+?)\*\*', lines[i])
        if m:
            title = m.group(1).strip().replace('\\|', '|')
            title = re.sub(r'\s*🦴\s*$', '', title)
            url = None
            for j in range(i + 1, min(i + 4, len(lines))):
                mu = re.match(r'^\s*`(http\S+?)`\s*$', lines[j])
                if mu:
                    url = mu.group(1).strip()
                    break
            if url and url not in seen:
                seen.add(url)
                items.append({'url': url, 'title': title})
            i += 4 if url else 1
        else:
            # Also accept plain URL on its own line
            stripped = lines[i].strip()
            if stripped.startswith('http') and stripped not in seen and ' ' not in stripped:
                seen.add(stripped)
                items.append({'url': stripped, 'title': ''})
            i += 1
    return items


# ──────────────────────────────────────────────────────────────────────
# Output document
# ──────────────────────────────────────────────────────────────────────

def staleness_days(personal_context_path: Path) -> int | None:
    if not personal_context_path.exists():
        return None
    mtime = datetime.fromtimestamp(personal_context_path.stat().st_mtime)
    return (datetime.now() - mtime).days


def evidence_snippet(content: str | None, max_chars: int = 160) -> str:
    if not content:
        return '(no content fetched)'
    # First non-empty paragraph
    for para in content.split('\n\n'):
        p = para.strip()
        if p and not p.startswith('#') and not p.startswith('```'):
            snippet = p[:max_chars]
            if len(p) > max_chars:
                snippet += '…'
            return snippet.replace('\n', ' ')
    return content[:max_chars].replace('\n', ' ')


def write_output(items: list[dict], clusters: list[dict],
                 output_path: Path, staleness: int | None,
                 elapsed_sec: float) -> None:

    by_verdict = defaultdict(list)
    for it in items:
        by_verdict[it['verdict']].append(it)

    tier_count = Counter(it.get('tier', '?') for it in items)
    reason_count = Counter(it.get('reason', '') for it in items if it['verdict'] == 'skip')
    domain_count = Counter(it.get('domain', '—') for it in items if it.get('domain'))

    today = datetime.now().strftime('%Y-%m-%d %H:%M')
    out = []

    # Header
    out.append('---')
    out.append('type: tabs-triage-verdicts')
    out.append('status: review')
    out.append(f'created: {today}')
    out.append(f'total-items: {len(items)}')
    out.append('generated-by: triage-urls.py v0.1 (spike)')
    out.append('---')
    out.append('')
    out.append('# Triage Verdicts — content-aware spike')
    out.append('')

    # Staleness warning
    if staleness is not None and staleness > 14:
        out.append(f'> [!warning] Reader Context **{staleness} days stale**')
        out.append(f'> `Personal Context.md` last updated {staleness} days ago. '
                   f'Per `_meta/Protocol.md:79`, context > 90 days stale should warn before extracting. '
                   f'Verdicts may be miscalibrated against current agenda. Recalibrate or proceed with eyes open.')
        out.append('')

    # Summary stats
    out.append('## Summary')
    out.append('')
    out.append(f'- **🎯 Capture:** {len(by_verdict["capture"])}')
    out.append(f'- **🔖 Bookmark:** {len(by_verdict["bookmark"])}')
    out.append(f'- **⊘ Skip:** {len(by_verdict["skip"])}')
    out.append(f'- **🌱 Synthesis-seed clusters:** {len(clusters)} '
               f'({sum(len(c["items"]) for c in clusters)} items tagged)')
    out.append('')
    out.append(f'Runtime: {elapsed_sec:.1f}s. Tier distribution: {dict(tier_count)}.')
    out.append('')

    # Clusters first (cross-cutting)
    if clusters:
        out.append('## 🌱 Synthesis-seed clusters')
        out.append('')
        out.append('Items in these clusters keep their individual verdict AND get tagged for the synthesis-candidates registry. Member items appear in their verdict section below with `cluster:<name>` tag.')
        out.append('')
        for c in sorted(clusters, key=lambda x: -len(x['items'])):
            out.append(f'### `{c["name"]}` ({c["domain"]}, {len(c["items"])} items)')
            out.append('')
            out.append(f'Common keywords: {", ".join("`"+k+"`" for k in c["common_keywords"])}')
            out.append('')
            for it in c['items']:
                out.append(f'- {it["verdict"]}: **{it["title"][:70]}** — `{it["url"]}`')
            out.append('')

    # Verdict sections
    for verdict, emoji in [('capture', '🎯'), ('bookmark', '🔖')]:
        section_items = by_verdict[verdict]
        if not section_items:
            continue
        out.append(f'## {emoji} {verdict.title()} ({len(section_items)})')
        out.append('')
        # Group by domain within section
        by_domain = defaultdict(list)
        for it in section_items:
            by_domain[it.get('domain', '—')].append(it)
        for domain in sorted(by_domain.keys()):
            domain_items = by_domain[domain]
            bar = 'HIGH' if domain in HIGH_BAR_DOMAINS else 'med'
            out.append(f'### {domain} ({bar}-bar · {len(domain_items)})')
            out.append('')
            for it in sorted(domain_items, key=lambda x: -x.get('substance', 0)):
                title = (it.get('title', '') or '(no title)').replace('|', '\\|')[:80]
                out.append(f'- [x] **{title}**')
                out.append(f'  `{it["url"]}`')
                tags = [f"substance:{it.get('substance', '?')}"]
                if it.get('domain_confidence'):
                    tags.append(f"domain-conf:{it['domain_confidence']:.2f}")
                if it.get('cluster_id'):
                    tags.append(f"cluster:`{it['cluster_id']}`")
                if it.get('tier'):
                    tags.append(f"T{it['tier']}")
                out.append(f'  *{evidence_snippet(it.get("content"), 140)}*')
                out.append(f'  {" · ".join(tags)}')
                out.append('')

    # Skip section (collapsed in Obsidian via foldable callout)
    skip_items = by_verdict['skip']
    if skip_items:
        out.append('## ⊘ Skip — auto + low-substance')
        out.append('')
        # Auto-skip (Tier 1) — fully collapsed
        auto_skip = [it for it in skip_items if it.get('tier') == 1]
        if auto_skip:
            out.append(f'> [!info]- Tier-1 auto-skip ({len(auto_skip)}) — junk/dup/lifestyle/auth/deeplink')
            for reason, cnt in reason_count.most_common():
                if cnt > 0:
                    out.append(f'> - {reason}: {cnt}')
            out.append('>')
            out.append('> <details><summary>Full list</summary>')
            out.append('>')
            for it in auto_skip:
                out.append(f'> - `{it["url"]}` — *{it.get("note", it.get("reason"))}*')
            out.append('> </details>')
            out.append('')
        # Substance-skip (Tier 2 said low-substance) — collapsed but visible
        substance_skip = [it for it in skip_items if it.get('tier') == 2]
        if substance_skip:
            out.append(f'> [!info]- Tier-2 substance-skip ({len(substance_skip)}) — fetched but thin')
            for it in substance_skip:
                title = (it.get('title') or '(no title)')[:60]
                out.append(f'> - **{title}** — `{it["url"]}` — substance:{it.get("substance", "?")} · domain:{it.get("domain", "?")}')
            out.append('')

    # Footer: how to use
    out.append('---')
    out.append('')
    out.append('## How to use (spike: no executor yet)')
    out.append('')
    out.append('1. Review verdicts in 🎯 Capture and 🔖 Bookmark sections (cluster tags shown inline).')
    out.append('2. Uncheck items you disagree with. (No executor in spike — apply manually for now.)')
    out.append('3. ⊘ Skip section is auto-classified; expand callouts to audit.')
    out.append('4. Synthesis-seed clusters are FLAGGED, не override verdict — clustered items still get their capture/bookmark/skip individually.')
    out.append('')
    out.append('This is a **content-aware spike** built on top of the v2 spec. Comparison against the URL-pattern triage output is the validation.')
    out.append('')

    output_path.write_text('\n'.join(out))


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Content-aware URL triage (spike)')
    parser.add_argument('input', type=Path, help='Input file: markdown triage doc OR URL list')
    parser.add_argument('--vault', type=Path,
                        default=Path('/Users/dkushnikov/Obsidian/Knowledge'),
                        help='Knowledge vault path for dedup')
    parser.add_argument('--output', type=Path, default=None,
                        help='Output doc path (default: vault/_claude/tabs-triage-verdicts.md)')
    parser.add_argument('--limit', type=int, default=None,
                        help='Limit to first N URLs (for testing)')
    parser.add_argument('--personal-context',
                        default=Path.home() / 'Obsidian' / 'Shared' / 'Context' / 'Personal Context.md',
                        type=Path)
    args = parser.parse_args()

    if not args.input.exists():
        print(f'ERROR: input not found: {args.input}', file=sys.stderr)
        return 2

    output = args.output or (args.vault / '_claude' / 'tabs-triage-verdicts.md')

    print(f'• Loading Knowledge URL index from {args.vault}/Sources/ …', file=sys.stderr)
    known = load_knowledge_index(args.vault)
    print(f'  {len(known)} URLs indexed', file=sys.stderr)

    print(f'• Parsing input: {args.input}', file=sys.stderr)
    items = parse_input(args.input)
    if args.limit:
        items = items[:args.limit]
    print(f'  {len(items)} URLs to evaluate', file=sys.stderr)

    start = datetime.now()
    for i, it in enumerate(items, 1):
        url = it['url']
        nu = normalize_url(url)
        it['normalized'] = nu

        # Tier 1
        v1 = tier1(url, nu, known)
        if v1:
            it.update(v1)
            print(f'  [{i:3}/{len(items)}] T1 {v1["verdict"]:10} {v1["reason"]:12} {url[:60]}',
                  file=sys.stderr)
            continue

        # Tier 2 — use normalized URL for fetch + classification
        print(f'  [{i:3}/{len(items)}] T2 fetching … {nu[:60]}', file=sys.stderr)
        content, err = fetch_content(nu)
        if err:
            # v0.6: even on fetch-failed, apply HIGH_SIGNAL host boost.
            # Known-good hosts (Pimenov, FastCompany, etc.) → preserve as bookmark
            # even when defuddle returns nothing (paywalls, JS-only sites).
            base_substance = 1
            boosted = apply_host_substance_override(nu, base_substance)
            boosted = apply_github_repo_boost(nu, boosted)
            if boosted > base_substance:
                # Host-known good but fetch failed → bookmark via med-bar at substance=4
                # Use the boosted substance with default domain learning if no content to classify
                it['content'] = ''
                it['substance'] = boosted
                it['host_boost'] = True
                it['domain'] = 'learning'  # fallback when no content for classification
                it['domain_confidence'] = 0.0
                it['verdict'] = verdict_from(boosted, 'learning')
                it['reason'] = f'substance={boosted}* (host-boost, fetch-failed: {err})'
                it['tier'] = 2
                print(f'         → {it["verdict"]:10} substance={boosted}* (host-boost, fetch-failed)',
                      file=sys.stderr)
                continue
            it.update({'verdict': 'skip', 'reason': 'fetch-failed',
                       'note': err, 'tier': 2, 'substance': 1})
            print(f'         → fetch-failed: {err}', file=sys.stderr)
            continue

        it['content'] = content
        base_substance = substance_score(content)
        boosted = apply_host_substance_override(nu, base_substance)
        boosted = apply_github_repo_boost(nu, boosted)
        it['substance'] = boosted
        if it['substance'] > base_substance:
            it['host_boost'] = True
        domain, conf = classify_domain(nu, content)
        it['domain'] = domain
        it['domain_confidence'] = conf
        it['verdict'] = verdict_from(it['substance'], domain)
        it['reason'] = f'substance={it["substance"]}{"*" if it.get("host_boost") else ""} domain={domain}({conf})'
        it['tier'] = 2
        print(f'         → {it["verdict"]:10} substance={it["substance"]} '
              f'domain={domain}({conf:.2f})', file=sys.stderr)

    print('• Detecting clusters …', file=sys.stderr)
    clusters = detect_clusters(items)
    print(f'  {len(clusters)} cluster(s) found', file=sys.stderr)

    elapsed = (datetime.now() - start).total_seconds()
    staleness = staleness_days(args.personal_context)

    print(f'• Writing output: {output}', file=sys.stderr)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_output(items, clusters, output, staleness, elapsed)

    # Final summary to stdout
    verdict_counts = Counter(it['verdict'] for it in items)
    print(f'\n✅ Triage complete in {elapsed:.1f}s')
    print(f'   Verdicts: {dict(verdict_counts)}')
    print(f'   Clusters: {len(clusters)} (covering {sum(len(c["items"]) for c in clusters)} items)')
    print(f'   Output:   {output}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
