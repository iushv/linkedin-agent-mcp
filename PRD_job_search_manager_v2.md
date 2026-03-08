# PRD: LinkedIn Job Search Manager MCP — v2

**Version:** 2.0
**Status:** Draft
**Date:** March 2026

---

## 1. Problem Statement

The current LinkedIn MCP covers content, feed, and basic messaging. For a complete
job search workflow — finding roles, identifying warm referral paths, reaching out,
and tracking progress — several critical capabilities are missing.

The core gap discovered during a live job search session:

> 77 EXL alumni at Mastercard, 35 at Visa. Can't reach them because there's no
> tool to find who they are.

Every outreach workflow breaks at "find the person." The rest of the stack
(send_connection_request, send_message) already works.

---

## 2. Target User

A professional exploring new roles quietly — not mass-applying, but running a
targeted search focused on warm connections, inbound visibility, and fit-first
applications.

---

## 3. Current State

| Tool | Status | Notes |
|------|--------|-------|
| `search_jobs` | ✅ Working | Structured `jobs[]` via text fallback; minor noise rows remain |
| `get_job_details` | ✅ Working | Full JD for a specific job_id |
| `get_person_profile` | ✅ Working | Full profile by username or URL |
| `get_company_profile` | ✅ Working | Company overview |
| `send_connection_request` | ✅ Working | With optional note (300 char) |
| `send_message` | ✅ Working | To existing 1st-degree connections |
| `get_my_post_analytics` | ✅ Fixed | Returns posts with reactions |
| `get_profile_analytics` | ⚠️ Flaky | Timeout on locator; needs resilience overhaul |
| `browse_feed` | ✅ Working | 6s resolver deadline |
| `get_conversations` | ✅ Working | Recent DM threads |

---

## 4. Phase 0 — Foundation (before any new tools)

Build shared infrastructure that P0 tools depend on. Without this, people-search
tools will be brittle from day one.

### 4.1 Entity Resolution Layer — `core/search_entities.py`

LinkedIn's search filters don't accept human-readable names like "Mastercard" or
"EXL." They use hidden URNs/IDs (e.g., `currentCompany=["1234567"]`). A resolver
is needed.

**What it does:**
- Accepts a human-readable company name (e.g., "Mastercard")
- Navigates to LinkedIn's typeahead/search autocomplete
- Returns the LinkedIn company ID / URN
- Caches resolved IDs in-memory for the session (avoid re-resolving)

```python
async def resolve_company_id(name: str) -> str | None:
    """Resolve 'Mastercard' -> '2034' (LinkedIn company URN)."""
    # 1. Navigate to linkedin.com/search/results/companies/?keywords={name}
    # 2. Extract the first matching company's URN from the results
    # 3. Cache it: _company_cache["mastercard"] = "2034"
    # 4. Return the URN

async def resolve_geo_id(location: str) -> str | None:
    """Resolve 'Singapore' -> '102454443' (LinkedIn geo URN)."""
```

**Same pattern for geo URNs.** LinkedIn encodes locations as numeric IDs in
search URLs.

### 4.2 Shared People-Card Schema

All people-returning tools (search_people, get_company_people, future tools)
must return the same shape:

```json
{
  "name": "Priya Sharma",
  "headline": "Senior ML Engineer at Mastercard",
  "profile_url": "https://linkedin.com/in/priyasharma",
  "location": "Singapore",
  "connection_degree": "2nd",
  "shared_connections": 3,
  "current_company": "Mastercard",
  "past_companies": ["EXL", "TCS"]
}
```

Define this as a dataclass/TypedDict in `core/schemas.py` and reuse it everywhere.

### 4.3 Pagination Contract

People and job searches can return large result sets. Under the 60s MCP ceiling,
pagination must be explicit:

```json
{
  "results": [...],
  "total": 77,
  "page": 1,
  "has_next": true,
  "next_cursor": "page=2"
}
```

**Rules:**
- Default `limit`: 10 results per call
- Max `limit`: 25 (beyond this, risk of timeout + rate limit)
- Caller passes `page` (1-indexed) or `next_cursor` from previous response
- Each page must complete within 45s (leaving 15s buffer for MCP overhead)
- If a page times out mid-scrape, return whatever was collected + `partial: true`

---

## 5. Phase 1 — People Search (P0, unblocks outreach)

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

**Output:** Array of people-cards (Section 4.2 schema) + pagination metadata.

**Implementation:**
1. Resolve `current_company`, `past_company`, `location` to LinkedIn URNs via
   `search_entities.py`
2. Build search URL:
   `linkedin.com/search/results/people/?keywords=...&currentCompany=[URN]&pastCompany=[URN]&geoUrn=[URN]`
3. Navigate, wait for `<main>`, scroll once, extract people cards
4. Parse each card into the shared people-card schema
5. Return results + pagination

**Rate limit:** 3s minimum delay between paginated calls.

### 5.2 `get_company_people`

Get people at a specific company, filtered by past employer or title keyword.
More targeted than `search_people` for the "who at Visa came from EXL?" question.

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

**Output:** Same people-card array + pagination.

**Implementation:**
1. Resolve company slug via `get_company_profile` or entity resolver
2. Navigate to `linkedin.com/company/{slug}/people/`
3. Apply filters (past company, title keyword) via URL params or UI filter clicks
4. Scrape people cards into shared schema

---

## 6. Phase 2 — Job Queue + Search Cleanup

Lower risk than profile-write tools, immediately useful. Moved ahead of profile
editing per review feedback.

### 6.1 `save_job`

Save a job posting to the user's Saved Jobs list.

**Input:**
```json
{ "job_url": "https://linkedin.com/jobs/view/4252026496" }
```

**Note:** Accepts `job_url` (always available from search results text) rather
than `job_id` (can be absent on degraded paths).

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

### 6.2 `get_saved_jobs`

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

### 6.3 `search_jobs` — noise cleanup

Fix the three known noise issues in the structured `jobs[]` output:

| Noise pattern | Fix |
|---------------|-----|
| Page header row ("data engineer in Singapore / 200+ results") | Drop entries where `title` matches `/^\w+ in \w+$/` or `company` matches `/^\d+ results$/` |
| "Are these results helpful?" row | Drop entries where `title` matches `/are these results helpful/i` |
| Company/location swap on "with verification" titles | When title has "with verification" suffix, skip that line and use next line as company |

---

## 7. Phase 3 — Profile Editing

### 7.1 `update_profile_headline`

Update the logged-in user's headline.

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

**Safety:**
- `dry_run: true` → navigates, reads current headline, returns what would change,
  does not save
- `confirm: true` → required for actual write; if false, returns a preview only
- Audit log: append to `~/.linkedin_mcp/audit.jsonl` with timestamp, tool name,
  old value, new value

### 7.2 `set_open_to_work`

Enable/disable Open to Work with job titles, locations, and visibility.

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

**Safety:** Same `dry_run` / `confirm` / audit log pattern as headline.

### 7.3 `add_profile_skills`

Add new skills to the profile. Separated from reordering because the UI flows
are different.

**Input:**
```json
{
  "skills": ["Generative AI", "RAG Pipelines", "Multi-Agent Systems"],
  "dry_run": false,
  "confirm": true
}
```

**Implementation:** Navigate to Skills section → "Add a new skill" modal → type
skill name → select from autocomplete → save.

### 7.4 `set_featured_skills`

Set which skills appear in the top-5 "Featured" slot on the profile.

**Input:**
```json
{
  "featured_skills": ["Generative AI", "Large Language Models (LLMs)", "Python", "RAG Pipelines", "Multi-Agent Systems"],
  "dry_run": false,
  "confirm": true
}
```

**Implementation notes:**
- This involves a modal with drag/drop or reorder buttons — more fragile than
  other profile edits
- May need to use LinkedIn's "Edit featured" flow, which is a nested modal
- Higher risk of UI breakage across LinkedIn updates
- Should have a robust fallback: if reorder fails, log the failure and surface
  it to the user rather than silently corrupting

**Safety:** Same `dry_run` / `confirm` / audit log pattern. Per-session quota:
max 2 calls to any profile-write tool per session.

---

## 8. Phase 4 — Analytics & Recommendations

### 8.1 `get_profile_analytics` (overhaul existing)

Currently times out intermittently. Needs the same resilience pattern used
elsewhere in this repo:

```
1. DOM-first: try locators for analytics widgets (3s timeout)
2. Heuristic fallback: wait_for_selector("main") + scroll + innerText
3. Structured parse: regex extraction of "N profile views", "N post impressions",
   "N search appearances"
4. Strict timeout budget: entire flow must complete in 15s
```

**Target output:**
```json
{
  "profile_views": 193,
  "post_impressions": 494,
  "search_appearances": 48,
  "period": "last_7_days"
}
```

### 8.2 `get_job_recommendations`

Return LinkedIn's personalised "Jobs you may be interested in" feed.

**Why:** Passive weekly discovery. Surfaces roles matching your profile that you
wouldn't search for explicitly. Distinct from `search_jobs` which is keyword-driven.

**Implementation:** Navigate to `linkedin.com/jobs/` (no keywords), scrape the
"Recommended for you" section, return structured job cards.

---

## 9. Implementation Order

```
Phase 0 (Week 0-1):
  - core/search_entities.py (company/geo resolver)
  - core/schemas.py (people-card dataclass, pagination envelope)
  - core/pagination.py (cursor helper, partial-result handler)

Phase 1 (Week 1-2):
  - search_people
  - get_company_people

Phase 2 (Week 2-3):
  - save_job + get_saved_jobs
  - search_jobs noise cleanup (3 known patterns)

Phase 3 (Week 3-4):
  - update_profile_headline
  - set_open_to_work
  - add_profile_skills
  - set_featured_skills

Phase 4 (Week 4-5):
  - get_profile_analytics overhaul
  - get_job_recommendations
```

---

## 10. Rate Limit Strategy

LinkedIn's anti-bot detection triggered during live testing at ~5 tool calls
in under 3 minutes.

| Action type | Minimum delay | Notes |
|-------------|--------------|-------|
| People search (paginated) | 3s between pages | Randomise 2-5s |
| Job search | 3s between searches | |
| Profile reads | 2s | Lower risk |
| Profile writes | 10s between writes | Max 2 per session |
| Connection requests | 60-120s between sends | Max 5 per session |
| Messages | 30-60s between sends | Max 10 per session |
| On CAPTCHA detection | Pause all calls 10 min | Surface clear error |

---

## 11. Safety Requirements for Profile-Write Tools

All tools in Phase 3 must implement:

1. **`confirm` flag** — required `true` for any write. Default `false`.
2. **`dry_run` flag** — when `true`, navigate and read current state, return
   a diff of what would change, do not save.
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
   session. Prevents runaway automation from making 10 headline changes.
5. **Rollback data** — audit log preserves old values, enabling manual rollback
   if a change was wrong.

---

## 12. Success Criteria

A complete job search session can:

1. Search jobs at a target company by keyword → structured results with no noise
2. Find EXL alumni (or any past-company alumni) at that company by name/title
3. Send tailored connection requests to 3-5 of them, spaced 60-120s apart
4. Save shortlisted jobs to a queue
5. Update profile headline and Open to Work settings with dry-run preview first
6. Pull weekly profile analytics to track recruiter visibility improvements

All within LinkedIn's rate limits, within Cowork's 60s per-call MCP ceiling.
