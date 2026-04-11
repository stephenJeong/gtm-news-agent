# PRD: GTM & Marketing Ops AI News Agent

## Overview

A weekly intelligence agent that monitors GTM and marketing ops content sources, synthesizes what's happening in the space, and delivers a structured digest to a Slack channel every Monday morning. The agent maintains memory of what projects the user has already built and uses that context to generate relevant, depth-first build recommendations.

---

## Goals

- Surface what practitioners are actually doing with AI in GTM and marketing ops, not just what vendors are selling
- Deliver a synthesized digest (not a raw link dump) with inline source citations
- Generate 2 to 3 deep build recommendations per week plus 2 to 3 one-line honorable mentions
- Distinguish clearly between trending signals and inferred opportunities
- Allow the user to reply in Slack and ask follow-up questions against that week's digest
- Persist memory of completed portfolio projects so recommendations don't repeat what's already built

---

## Non-Goals

- Real-time monitoring (weekly batch is sufficient)
- Full LinkedIn coverage (patchy is acceptable given ToS constraints)
- CRM or Marketo integration (out of scope for v1)

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  GitHub Actions                  │
│         Cron: every Monday 7:00 AM PT            │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│              collector.py                        │
│  - Substack RSS feeds                            │
│  - Website scrapers (requests + BeautifulSoup)   │
│  - Apify actor for LinkedIn (free tier)          │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│              synthesizer.py                      │
│  - Deduplication                                 │
│  - Passes raw content + project memory to        │
│    Claude API for synthesis and recommendations  │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│              slack_bot.py                        │
│  - Posts digest to #mops-intel channel           │
│  - Listens for replies via Socket Mode           │
│  - Passes conversation context back to Claude    │
└─────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────┐
│           memory/projects.json                   │
│  - Flat list of completed portfolio projects     │
│  - Read on every synthesis run                   │
│  - Updated manually or via /project-done command │
└─────────────────────────────────────────────────┘
```

---

## Directory Structure

```
gtm-news-agent/
├── .github/
│   └── workflows/
│       └── weekly_digest.yml       # GitHub Actions cron job
├── agent/
│   ├── collector.py                # All scraping logic
│   ├── synthesizer.py              # Claude API calls
│   ├── slack_bot.py                # Slack delivery + conversation handler
│   └── prompts.py                  # All system and user prompts
├── memory/
│   └── projects.json               # Portfolio project memory
├── config/
│   └── sources.json                # Source URLs and types
├── tests/
│   ├── test_collector.py
│   └── test_synthesizer.py
├── requirements.txt
├── .env.example
└── README.md
```

---

## Data Sources

Defined in `config/sources.json`. Claude Code should make this file data-driven so adding a new source requires only a new entry here, not a code change.

```json
[
  {
    "id": "cookingupgtm",
    "name": "Cooking Up GTM",
    "type": "substack_rss",
    "url": "https://cookingupgtm.substack.com/feed"
  },
  {
    "id": "appliedaiformops",
    "name": "Applied AI for MOPS",
    "type": "substack_rss",
    "url": "https://www.appliedaiformops.com/feed"
  },
  {
    "id": "the_moperator",
    "name": "The MOperator",
    "type": "website",
    "url": "https://the-moperator.com/",
    "article_selector": "article"
  },
  {
    "id": "martech_org",
    "name": "MarTech.org",
    "type": "website",
    "url": "https://martech.org/",
    "article_selector": "article",
    "filter_keywords": ["AI", "automation", "marketing ops", "GTM", "lead", "attribution"]
  },
  {
    "id": "mops_strategist_linkedin",
    "name": "Marketing Operations Strategist (LinkedIn)",
    "type": "linkedin_apify",
    "apify_actor": "apify/linkedin-company-posts-scraper",
    "linkedin_url": "https://www.linkedin.com/company/the-marketing-operations-strategist/"
  }
]
```

---

## Module Specifications

### collector.py

**Substack RSS**
- Use `feedparser` to pull RSS feed
- Extract: title, published date, summary, link
- Filter to items published in the last 7 days
- If no `summary` field, fetch full article URL with `requests` and extract first 500 chars of body text

**Website scraping**
- Use `requests` + `BeautifulSoup`
- Target the `article_selector` defined in sources.json
- Extract: headline, publication date (look for `<time>` tag or meta date), article URL, first 300 chars of body
- Apply `filter_keywords` if present: only include articles where headline OR body snippet contains at least one keyword (case-insensitive)
- Respect `robots.txt` — check before scraping

**LinkedIn via Apify**
- Use the Apify Python client
- Call actor `apify/linkedin-company-posts-scraper` with the company URL
- Retrieve posts from the last 7 days
- Extract: post text (truncated to 500 chars), post date, post URL if available
- Handle Apify free tier limits gracefully: if monthly run quota is exceeded, log a warning and skip LinkedIn rather than failing the whole job

**Output format for all collectors**

Each collected item should be a dict:
```python
{
  "source_id": "cookingupgtm",
  "source_name": "Cooking Up GTM",
  "title": "...",
  "url": "...",
  "published_date": "2025-01-06",
  "snippet": "...",
  "type": "article"  # or "post"
}
```

Deduplication: before passing to synthesizer, deduplicate by URL. If same story appears across multiple sources, keep the one with the longer snippet and note both source names.

---

### synthesizer.py

Reads `memory/projects.json`, formats all collected items, and calls the Claude API.

**Model:** `claude-sonnet-4-20250514`
**Max tokens:** 2000

**System prompt** (stored in `prompts.py`):

```
You are an intelligence analyst for a marketing operations professional named Stephen.
Stephen has 13+ years in marketing ops and is building a portfolio of AI-powered MOPS tools.
Your job is to synthesize weekly content from GTM and marketing ops sources into a structured digest.

Stephen's completed projects are provided in each request. Do not recommend building something he has already built.

Tone: direct, practitioner-level. No fluff. Assume Stephen knows what Marketo, SFDC, LeanData, and Clay are.
```

**User prompt template** (stored in `prompts.py`):

```
Today is {date}. Here are this week's articles and posts from GTM and marketing ops sources:

{formatted_items}

Stephen's completed portfolio projects:
{projects_list}

Produce a digest in this exact structure:

## This Week in GTM & MOPS AI
*Week of {date}*

### What's Happening
[3 to 5 paragraph synthesis of the week's themes. Cite sources inline like this: ([Source Name](url)). Do not just summarize each article separately — find the threads that connect them.]

### Build Recommendations

**1. [Recommendation title]**
Trend signal: [What you saw in the content that supports this]
What to build: [Specific description, 3 to 5 sentences]
Why now: [Why this is worth building this week vs later]
Complexity: [Low / Medium / High]

**2. [Recommendation title]**
[same structure]

**3. [Recommendation title — mark as INFERRED if not directly from a trending signal]**
[same structure, add "Note: This is an inferred opportunity, not directly trending in this week's content."]

### Also Worth a Look
- [One sentence + source link]
- [One sentence + source link]
- [One sentence + source link]
```

---

### memory/projects.json

Simple flat structure. Claude Code should create a helper function to read and format this for the prompt.

```json
{
  "last_updated": "2025-01-06",
  "projects": [
    {
      "id": "lead_scoring_optimizer",
      "name": "Lead Scoring Optimizer",
      "description": "AI-powered tool that analyzes and optimizes Marketo lead scoring models",
      "url": "https://lead-scoring-optimizer.vercel.app",
      "completed": true,
      "tags": ["lead scoring", "marketo", "AI", "scoring model"]
    },
    {
      "id": "campaign_ops_slackbot",
      "name": "Campaign Operations Assistant Slackbot",
      "description": "12-phase Slackbot for campaign operations workflows",
      "completed": true,
      "tags": ["slack", "campaign ops", "automation", "bot"]
    }
  ]
}
```

Add more projects as they are completed. Stephen can also trigger an update via Slack command (see below).

---

### slack_bot.py

**Delivery**
- Post the weekly digest to a channel (e.g. `#mops-intel`) using `chat.postMessage`
- Format using Slack Block Kit for readability: use `section` blocks for body text, `divider` blocks between sections
- Pin the message after posting so it's easy to find

**Conversation mode**
- Use Slack Socket Mode to listen for `app_mention` events and direct messages
- Maintain a conversation thread: when Stephen replies in the digest thread, the bot responds in that same thread
- Pass the original digest content + conversation history to Claude API on each reply
- Conversation context resets weekly when the new digest is posted

**Slash commands to implement**
- `/project-done [project name and description]` — appends a new entry to `projects.json` and confirms in Slack
- `/digest-now` — triggers an immediate digest run outside the Monday schedule (useful for testing)
- `/sources` — lists all configured sources and their last-scraped status

**Conversation system prompt for follow-up questions:**
```
You are a GTM and marketing ops expert assistant. The user just received their weekly digest (included below).
They may ask follow-up questions about specific topics, request more detail on a recommendation,
or ask you to find more information on a trend.

Answer in the same direct, practitioner-level tone. If they ask about something not covered in the digest,
say so clearly and answer from your own knowledge, noting it's not from this week's sources.

Weekly digest context:
{digest_text}
```

---

## GitHub Actions Workflow

File: `.github/workflows/weekly_digest.yml`

```yaml
name: Weekly MOPS Intelligence Digest

on:
  schedule:
    - cron: '0 15 * * 1'  # Every Monday 7:00 AM PT (15:00 UTC)
  workflow_dispatch:       # Allows manual trigger

jobs:
  run_digest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: python agent/synthesizer.py
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          SLACK_BOT_TOKEN: ${{ secrets.SLACK_BOT_TOKEN }}
          SLACK_CHANNEL_ID: ${{ secrets.SLACK_CHANNEL_ID }}
          APIFY_API_TOKEN: ${{ secrets.APIFY_API_TOKEN }}
```

Note: The Slack bot's Socket Mode listener (for conversation) needs to run persistently. Host this as a separate lightweight process. Options in order of preference:
1. Render free tier (spin-up delay is acceptable for a personal tool)
2. Fly.io free tier
3. Railway free tier

The GitHub Actions job handles the weekly digest. The persistent process only handles conversation replies.

---

## Environment Variables

```
ANTHROPIC_API_KEY=
SLACK_BOT_TOKEN=          # Bot token (xoxb-)
SLACK_APP_TOKEN=          # App-level token for Socket Mode (xapp-)
SLACK_CHANNEL_ID=         # Target channel for digest delivery
APIFY_API_TOKEN=          # Apify token for LinkedIn scraping
```

---

## Slack App Setup (Step by Step)

1. Go to api.slack.com/apps and create a new app from scratch
2. Under **OAuth & Permissions**, add these Bot Token Scopes:
   - `chat:write`
   - `channels:read`
   - `app_mentions:read`
   - `im:read`
   - `im:write`
   - `commands`
3. Install the app to your workspace and copy the Bot Token (`xoxb-`)
4. Under **Socket Mode**, enable it and generate an App-Level Token (`xapp-`) with scope `connections:write`
5. Under **Event Subscriptions**, enable and subscribe to `app_mention` and `message.im`
6. Under **Slash Commands**, create `/project-done`, `/digest-now`, and `/sources`
7. Invite the bot to your target channel: `/invite @[bot-name]`

---

## requirements.txt

```
anthropic>=0.20.0
slack-sdk>=3.27.0
feedparser>=6.0.10
requests>=2.31.0
beautifulsoup4>=4.12.0
apify-client>=1.6.0
python-dotenv>=1.0.0
```

---

## Build Order for Claude Code

Build in this sequence. Each phase should be independently testable before moving to the next.

**Phase 1: Collector**
- Implement RSS collector for Substack sources
- Implement website scraper for martech.org and the-moperator.com
- Write test that runs collectors and dumps output to `tests/fixtures/sample_collected.json`
- Do not wire up LinkedIn yet

**Phase 2: Synthesizer**
- Implement `synthesizer.py` using sample fixture data from Phase 1
- Load `memory/projects.json`
- Call Claude API and print digest to stdout
- Validate the digest structure matches the template

**Phase 3: Slack Delivery**
- Implement `slack_bot.py` delivery function
- Post a test digest to the channel
- Validate Block Kit formatting looks correct

**Phase 4: Conversation**
- Implement Socket Mode listener
- Test reply handling in a thread
- Validate conversation context is maintained across 3+ back-and-forth exchanges

**Phase 5: LinkedIn via Apify**
- Wire up Apify actor call
- Add fallback handling for quota limits
- Test with a manual trigger

**Phase 6: GitHub Actions**
- Set up workflow file
- Add all secrets to repo
- Trigger manually with `workflow_dispatch` and confirm end-to-end

**Phase 7: Persistent Bot Hosting**
- Deploy Socket Mode listener to Render free tier
- Confirm it receives and responds to messages after cold start

---

## Testing Notes

- All collectors should have a `--dry-run` flag that fetches and prints raw output without calling the Claude API or posting to Slack
- `synthesizer.py` should accept a `--fixture` flag pointing to a JSON file so it can be tested without live scraping
- Use `pytest` for unit tests; mock external API calls with `unittest.mock`

---

## Known Constraints and Tradeoffs

| Constraint | Decision |
|---|---|
| LinkedIn ToS | Use Apify; accept patchy coverage; fail gracefully on quota |
| Render free tier cold starts | Acceptable for a personal tool; conversation replies may have 30s delay |
| GitHub Actions free tier | 2000 min/month; this job runs ~5 min/week, well within limits |
| Apify free tier | ~$5 of free compute/month; LinkedIn actor is lightweight, should be sufficient |
| Claude API cost | Estimated $0.05 to $0.15 per weekly run depending on content volume |

---

## Future Enhancements (Out of Scope for v1)

- Auto-updating `projects.json` by parsing sjeong.com via GitHub Actions
- Podcast transcript ingestion (e.g. MOPS-adjacent pods)
- Semantic deduplication across weeks (avoid resurface the same story two weeks in a row)
- Scoring items by relevance before synthesis to manage token usage at scale
