# PRD: LinkedIn Job Search Manager MCP — v3 (Implementation-Ready)

**Version:** 3.0  
**Status:** Ready for implementation  
**Date:** March 2026

---

## 1. Problem Statement

The current LinkedIn MCP covers content, feed, and basic messaging. For a complete
job search workflow — finding roles, identifying warm referral paths, reaching out,
and tracking progress — several critical capabilities are missing.

> 77 EXL alumni at Mastercard, 35 at Visa. Can't reach them because there's no
> tool to find who they are.

Every outreach workflow breaks at "find the person."

---

## 2. Target User

A professional exploring new roles quietly — running a targeted, human-supervised
search focused on warm connections, inbound visibility, and fit-first applications.

---

## 3. Current State

| Tool | Status | Notes |
|------|--------|-------|
| `search_jobs` | ✅ Working | Structured `jobs[]` via text fallback; noise rows remain |
| `get_job_details` | ✅ Working | Full JD by job_id |
| `get_person_profile` | ✅ Working | Full profile by username/URL |
| `get_company_profile` | ✅ Working | Company overview |
| `send_connection_request` | ✅ Working | With optional note (300 char) |
| `send_message` | ✅ Working | To 1st-degree connections |
| `get_my_post_analytics` | ✅ Fixed | Posts with reactions |
| `get_profile_analytics` | ⚠️ Flaky | Needs resilience overhaul |
| `browse_feed` | ✅ Working | 6s resolver deadline |
| `get_conversations` | ✅ Working | Recent DM threads |

---

## 4. Phase 0 — Foundation

Build shared infrastructure before any new tools. Without this, people-search
tools will be brittle and slow from day one.

### 4.1 Entity Resolver — `core/resolver.py`

LinkedIn filters use hidden URNs, not human-readable names. The resolver translates
names to IDs and caches aggressively to stay within the 60s ceiling.

**Outputs per entity type:**

```python
@dataclass
class ResolvedCompany:
    company_id: str          # LinkedIn URN, e.g. "2034"
    company_slug: str        # URL slug, e.g. "mastercard"
    company_url: str         # Full URL: linkedin.com/company/mastercard
    display_name: str        # "Mastercard"

@dataclass
class ResolvedGeo:
    geo_id: str              # LinkedIn geo URN, e.g. "102454443"
    geo_label: str           # "Singapore"
```

**Resolution strategy:**

```
1. Check in-session cache (dict keyed by normalized lowercase input)
   → hit: return immediately (0ms)

2. Check persistent disk cache (~/.linkedin_mcp/entity_cache.json, 30-day TTL)
   → hit: load into session cache, return (< 5ms)

3. Live resolution:
   → Navigate to linkedin.com/search/results/companies/?keywords={name}
   → Extract first matching company card: URN, slug, display name
   → Write to both session + disk cache
   → Budget: max 8s per resolution

4. Geo resolution uses the same approach via:
   → linkedin.com/jobs/search/?keywords=&location={name}
   → Extract geoUrn from the URL redirect or filter sidebar
```

**Batched resolution:**

When `search_people` receives both `current_company` and `past_company`, resolve
both concurrently (asyncio.gather) before building the search URL. Total resolution
budget: 10s. If either exceeds 8s individually, use the partial result.

**Strict fallback policy:**

Unresolved filters do NOT fail the tool. They are dropped with a warning:

```json
{
  "results": [...],
  "warnings": ["Could not resolve past_company='EXL'; search ran without that filter"],
  "filters_applied": {
    "current_company": "2034",
    "past_company": null,
    "location": "102454443"
  }
}
```

This means every people search always returns results — potentially broader than
requested, but never an empty failure due to resolver issues.

### 4.2 Shared Schemas — `core/schemas.py`

**People card (all people-returning tools must use this):**

```python
@dataclass
class PersonCard:
    name: str                     # Required — invalid without this
    profile_url: str              # Required — invalid without this
    headline: str | None = None
    location: str | None = None
    connection_degree: str | None = None
    shared_connections: int | None = None
    current_company: str | None = None
    past_companies: list[str] | None = None
```

**Validity rule:** A person card is valid if and only if `name` and `profile_url`
are both non-empty. Cards missing either are dropped silently from results.

**Job card (all job-returning tools must use this):**

```python
@dataclass
class JobCard:
    title: str                    # Required
    company: str                  # Required
    location: str | None = None
    posting_date: str | None = None
    job_id: str | None = None
    job_url: str | None = None
```

**Validity rule:** A job card is valid if `title` is present AND `company` is
present AND at least one of (`location`, `posting_date`, `job_url`, `job_id`)
is non-null. Invalid cards are dropped.

This replaces the regex-only noise filtering. Regex patterns (like
`/are these results helpful/i`) remain as secondary heuristics applied after
the shape check.

### 4.3 Pagination — `core/pagination.py`

```python
@dataclass
class PaginatedResponse:
    results: list               # PersonCard[] or JobCard[]
    total: int | None           # null if LinkedIn doesn't expose count
    page: int                   # 1-indexed
    has_next: bool
    next_cursor: str | None     # opaque, pass back to get next page
    partial: bool = False       # true if page timed out mid-scrape
    warnings: list[str] | None = None
```

**Rules:**
- Default `limit`: 10 per call
- Max `limit`: 25
- Each page must complete within 45s (leaving 15s for MCP overhead)
- If a page times out mid-scrape, return whatever was collected with `partial: true`
- Caller passes `page` (1-indexed) or `next_cursor` from previous response

### 4.4 Phase 0 Deliverables Checklist

```
[ ] core/resolver.py — resolve_company(), resolve_geo()
[ ] core/resolver.py — in-session cache (dict)
[ ] core/resolver.py — persistent disk cache with 30-day TTL
[ ] core/resolver.py — batched concurrent resolution
[ ] core/resolver.py — graceful fallback (drop unresolved, return warning)
[ ] core/schemas.py — PersonCard, JobCard dataclasses
[ ] core/schemas.py — validity check functions
[ ] core/pagination.py — PaginatedResponse envelope
[ ] core/pagination.py — partial-result handler
[ ] tests/test_resolver.py — cache hit, cache miss, resolution failure, batch
[ ] tests/test_schemas.py — validity accept/reject
```

---

## 5. Phase 1 — People Search

### 5.1 `search_people`

Find LinkedIn members by keyword, company, title, location, or shared background.

**Input:**
```json
{
  "keywords": "machine learning engineer",
  "current_company": "Mastercard",
  "past_company": "EXL",
  "location": "Singapore",
  "limit": 10,
  "page": 1
}
```

**Output:** `PaginatedResponse` containing `PersonCard[]` + `warnings[]` +
`filters_applied{}`.

**Implementation:**
1. Resolve `current_company`, `past_company`, `location` via `core/resolver.py`
   (concurrent, 10s budget)
2. Build search URL with resolved URNs:
   `linkedin.com/search/results/people/?keywords=...&currentCompany=[URN]&pastCompany=[URN]&geoUrn=[URN]`
3. Drop any filter that failed resolution (include warning)
4. Navigate, wait for `<main>`, scroll once, extract people cards
5. Parse each card into `PersonCard`, drop invalid cards
6. Return `PaginatedResponse`

**Rate limit:** 3s minimum between paginated calls, randomised 2–5s.

### 5.2 `get_company_people`

Get people at a specific company, filtered by past employer or title keyword.

**Input:**
```json
{
  "company_name": "visa",
  "past_company": "EXL",
  "title_keyword": "engineer",
  "limit": 15,
  "page": 1
}
```

**Output:** Same `PaginatedResponse` with `PersonCard[]`.

**Implementation:**
1. Resolve `company_name` to get both `company_id` AND `company_slug` via resolver
   (slug is needed for the /people/ URL path)
2. Resolve `past_company` to `company_id` (for the filter param)
3. Navigate to `linkedin.com/company/{company_slug}/people/`
4. Apply filters via URL params or sidebar filter UI
5. Scrape people cards, validate, return

**Note:** `company_slug` resolution is explicitly part of Phase 0's resolver —
`ResolvedCompany` includes both `company_id` and `company_slug`.

---

## 6. Phase 2 — Job Queue + Search Maintenance

Two distinct workstreams tracked separately, delivered in the same phase window.

### 6A. Job Queue (product feature)

#### 6A.1 `save_job`

Save a job posting to the user's Saved Jobs list.

**Input:**
```json
{ "job_url": "https://linkedin.com/jobs/view/4252026496" }
```

**Output:**
```json
{
  "success": true,
  "job_id": "4252026496",
  "job_url": "https://linkedin.com/jobs/view/4252026496",
  "company": "Mastercard",
  "title": "Senior AI Engineer",
  "status": "saved"
}
```

**Note:** Accepts `job_url` (always available) instead of `job_id` (absent on
degraded paths). Extracts `job_id` from URL when possible.

#### 6A.2 `get_saved_jobs`

Return the user's Saved Jobs list.

**Output:**
```json
{
  "jobs": [
    {
      "job_id": "4252026496",
      "job_url": "https://linkedin.com/jobs/view/4252026496",
      "title": "Senior AI Engineer",
      "company": "Mastercard",
      "location": "Singapore",
      "saved_at": "2026-03-06",
      "status": "saved"
    }
  ]
}
```

### 6B. Search Maintenance (debt cleanup)

#### 6B.1 `search_jobs` noise cleanup

**Primary filter (structural):** Apply `JobCard` validity rule — drop any result
that fails the shape check (missing title, missing company, or no location/date/url/id).

**Secondary filter (heuristic):** After shape check, drop rows matching known
noise patterns as a safety net:
- `title` matches `/are these results helpful/i`
- `title` matches `/jobs you may be interested in/i`
- `company` matches `/^\d+ results$/`

Structural filter catches the majority. Regex catches edge cases.

---

## 7. Phase 3 — Profile Editing

### 7.1 `update_profile_headline`

**Input:**
```json
{
  "headline": "AI/ML Engineer | Agentic Systems · RAG · LLMs | Open Source Builder | BFSI Domain",
  "dry_run": false,
  "confirm": true
}
```

**Output:**
```json
{
  "success": true,
  "previous_headline": "Data Consultant @ EXL | ...",
  "new_headline": "AI/ML Engineer | ..."
}
```

### 7.2 `set_open_to_work`

**Input:**
```json
{
  "enabled": true,
  "visibility": "recruiters_only",
  "job_titles": ["Machine Learning Engineer", "AI Engineer", "Generative AI Engineer", "Applied Scientist", "Senior Data Scientist"],
  "job_types": ["full_time"],
  "locations": ["India", "Singapore", "Remote"],
  "dry_run": false,
  "confirm": true
}
```

### 7.3 `add_profile_skills`

Add new skills to the profile. Standard modal flow.

**Input:**
```json
{
  "skills": ["Generative AI", "RAG Pipelines", "Multi-Agent Systems"],
  "dry_run": false,
  "confirm": true
}
```

### 7.4 `set_featured_skills` — EXPERIMENTAL

**Classification:** Experimental. Best-effort. Higher maintenance burden than all
other Phase 3 tools. May break across LinkedIn UI updates. Not a required Phase 3
deliverable — treat as a stretch goal.

**Why it's hard:** LinkedIn's skill reordering uses a nested modal with drag/drop
or arrow-button interactions. This is significantly more fragile than text-input
based edits (headline, skills add).

**Input:**
```json
{
  "featured_skills": ["Generative AI", "Large Language Models (LLMs)", "Python", "RAG Pipelines", "Multi-Agent Systems"],
  "dry_run": false,
  "confirm": true
}
```

**Fallback:** If reorder fails, return an error with the current skill order
and suggest the user reorder manually. Never silently corrupt.

### Profile-Write Safety Contract

All Phase 3 tools must implement:

1. **`confirm: true`** — required for any write. Default `false` (preview only).
2. **`dry_run: true`** — navigate, read current state, return diff, do not save.
3. **Audit log** — append to `~/.linkedin_mcp/audit.jsonl`:
   ```json
   {
     "timestamp": "2026-03-06T20:41:00Z",
     "tool": "update_profile_headline",
     "action": "write",
     "old_value": "Data Consultant @ EXL | ...",
     "new_value": "AI/ML Engineer | ...",
     "success": true
   }
   ```
4. **Per-session quotas** — max 2 calls to any single profile-write tool per
   session. Prevents runaway automation.
5. **Rollback data** — audit log preserves old values for manual recovery.

---

## 8. Phase 4 — Analytics & Recommendations

### 8.1 `get_profile_analytics` (overhaul)

Follow the same resilience pattern used elsewhere in this repo:

```
1. DOM-first: try locators for analytics widgets (3s timeout)
2. Heuristic fallback: wait_for_selector("main") + scroll + innerText
3. Structured parse: regex extraction of
   "N profile views", "N post impressions", "N search appearances"
4. Strict timeout: entire flow completes in 15s max
```

**Output:**
```json
{
  "profile_views": 193,
  "post_impressions": 494,
  "search_appearances": 48,
  "period": "last_7_days"
}
```

### 8.2 `get_job_recommendations`

LinkedIn's personalised "Jobs you may be interested in" feed. Passive weekly
discovery — surfaces roles you wouldn't search for explicitly.

Navigate to `linkedin.com/jobs/` (no keywords), scrape "Recommended for you,"
return `JobCard[]` via `PaginatedResponse`.

---

## 9. Implementation Order

```
Phase 0 (Week 0-1): Foundation
  - core/resolver.py
      - resolve_company() → ResolvedCompany
      - resolve_geo() → ResolvedGeo
      - in-session cache (dict, keyed by normalized lowercase)
      - persistent disk cache (~/.linkedin_mcp/entity_cache.json, 30-day TTL)
      - batched concurrent resolution (asyncio.gather, 10s budget)
      - graceful fallback (drop + warn, never fail)
  - core/schemas.py
      - PersonCard dataclass + validity check
      - JobCard dataclass + validity check
  - core/pagination.py
      - PaginatedResponse envelope
      - partial-result handler
  - Tests for all of the above

Phase 1 (Week 1-2): People Search
  - search_people
  - get_company_people

Phase 2 (Week 2-3): Job Queue + Search Maintenance
  2A. Product:
    - save_job
    - get_saved_jobs
  2B. Maintenance:
    - search_jobs structural + heuristic noise cleanup

Phase 3 (Week 3-4): Profile Editing
  Required:
    - update_profile_headline
    - set_open_to_work
    - add_profile_skills
  Experimental (stretch goal):
    - set_featured_skills

Phase 4 (Week 4-5): Analytics & Recommendations
  - get_profile_analytics overhaul
  - get_job_recommendations
```

---

## 10. Rate Limit Strategy

| Action type | Minimum delay | Session cap | Notes |
|-------------|--------------|-------------|-------|
| Entity resolution | 2s between calls | No cap (cached) | Concurrent pair OK |
| People search (paginated) | 3s between pages | No cap | Randomise 2–5s |
| Job search | 3s between searches | No cap | |
| Profile reads | 2s | No cap | Lower risk |
| Profile writes | 10s between writes | 2 per tool | |
| Connection requests | 60–120s between sends | 5 per session | |
| Messages | 30–60s between sends | 10 per session | |
| On CAPTCHA | Pause all calls 10 min | — | Surface clear error |

---

## 11. Success Criteria

A supervised job search session can:

1. Search jobs at a target company by keyword → structured, noise-free results
2. Find past-company alumni currently at that company → valid people cards
   with name + profile_url
3. Assist the user in sending connection requests to 3–5 of them across
   multiple spaced calls in a human-supervised session
4. Save shortlisted jobs to a queue for follow-up
5. Update profile headline and Open to Work with dry-run preview first
6. Pull weekly profile analytics to track recruiter visibility improvements

All within LinkedIn's rate limits, within Cowork's 60s per-call MCP ceiling,
with the agent assisting a human-paced session rather than running autonomous
bulk outreach.
