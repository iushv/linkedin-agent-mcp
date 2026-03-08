# PRD: LinkedIn Job Search Manager MCP

**Version:** 1.0
**Status:** Draft
**Author:** Ayush Kumar
**Date:** March 2026

---

## 1. Problem Statement

The current LinkedIn MCP covers content, feed, and basic messaging. For a complete
job search workflow — finding the right roles, identifying warm referral paths, reaching
out to the right people, and tracking progress — several critical capabilities are missing.

The core gap discovered during a live job search session:

> You have 77 EXL alumni at Mastercard and 35 at Visa. You can't reach them because
> there's no tool to find who they are.

Every outreach workflow breaks at the "find the person" step. The rest of the stack
(send_connection_request, send_message) is already built.

---

## 2. Target User

A professional exploring new roles quietly — not actively applying everywhere, but
running a high-quality, targeted search focused on warm connections, inbound profile
visibility, and fit-first applications.

---

## 3. Current State (What Already Works)

| Tool | Status | Notes |
|------|--------|-------|
| `search_jobs` | ✅ Working | Returns job listings; structured `jobs[]` array via text fallback |
| `get_job_details` | ✅ Working | Full JD for a specific job_id |
| `get_person_profile` | ✅ Working | Full profile by username or URL |
| `get_company_profile` | ✅ Working | Company overview |
| `send_connection_request` | ✅ Working | With optional note (300 char) |
| `send_message` | ✅ Working | To existing 1st-degree connections |
| `get_my_post_analytics` | ✅ Fixed this session | Returns posts with reactions |
| `get_profile_analytics` | ⚠️ Flaky | Timeout on locator; intermittent |
| `browse_feed` | ✅ Working | With 6s resolver deadline |
| `get_conversations` | ✅ Working | Recent DM threads |

---

## 4. Missing Tools — Prioritised

### P0 — Blocks the core outreach workflow

#### 4.1 `search_people`

Find LinkedIn members by keyword, company, title, location, or shared connection.

**Why it's P0:** Without this, the entire warm referral path is blocked. We know 77 EXL
alumni work at Mastercard, but we can't find out who they are or reach them.

**Input:**
```json
{
  "keywords": "machine learning engineer",
  "current_company": "Mastercard",
  "past_company": "EXL",
  "location": "Singapore",
  "limit": 10
}
```

**Output:**
```json
{
  "people": [
    {
      "name": "Priya Sharma",
      "headline": "Senior ML Engineer at Mastercard",
      "profile_url": "https://linkedin.com/in/priyasharma",
      "location": "Singapore",
      "connection_degree": "2nd",
      "shared_connections": 3
    }
  ]
}
```

**Implementation notes:**
- Navigate to `linkedin.com/search/results/people/` with query params
- `keywords`, `currentCompany[]`, `pastCompany[]`, `geoUrn[]` are URL params LinkedIn uses
- Parse the people cards from the search results page
- Respect rate limits — add 2–3s delay between paginated calls

---

#### 4.2 `get_company_people`

Get people currently at a company, optionally filtered by past employer or title keyword.

**Why it's P0:** More targeted than `search_people` for "who at Visa came from EXL?"

**Input:**
```json
{
  "company_name": "visa",
  "past_company": "EXL",
  "title_keyword": "engineer",
  "limit": 15
}
```

**Output:** Same shape as `search_people`.

**Implementation notes:**
- Navigate to `linkedin.com/company/{slug}/people/`
- Apply filters via URL params or UI interaction
- Scrape the people cards — name, headline, profile URL, connection degree

---

### P1 — Needed for full workflow automation

#### 4.3 `update_profile_headline`

Update the logged-in user's LinkedIn headline.

**Why P1:** Currently the user has to make profile changes manually. For a job search
manager that can suggest and apply optimisations, this is the most impactful single field.

**Input:**
```json
{
  "headline": "AI/ML Engineer | Agentic Systems · RAG · LLMs | Open Source Builder | BFSI Domain"
}
```

**Output:**
```json
{ "success": true, "previous_headline": "...", "new_headline": "..." }
```

**Implementation notes:**
- Navigate to profile, click the edit pencil on the intro section
- Locate the headline input, clear it, type new value
- Save and confirm the update rendered

---

#### 4.4 `update_profile_skills`

Reorder or add skills so the top 5 shown are the target ones.

**Why P1:** LinkedIn's recruiter search weights the first 5 skills heavily. Reordering
from "Data Migration, Predictive Analytics" to "Generative AI, LLMs, Python" directly
improves inbound recruiter visibility.

**Input:**
```json
{
  "top_skills": ["Generative AI", "Large Language Models (LLMs)", "Python", "RAG Pipelines", "Multi-Agent Systems"]
}
```

**Output:**
```json
{ "success": true, "skills_reordered": 5 }
```

---

#### 4.5 `set_open_to_work`

Enable or disable Open to Work signal, with job titles, location preferences, and
visibility (recruiter-only vs. public).

**Input:**
```json
{
  "enabled": true,
  "visibility": "recruiters_only",
  "job_titles": ["Machine Learning Engineer", "AI Engineer", "Generative AI Engineer", "Applied Scientist", "Senior Data Scientist"],
  "job_types": ["full_time"],
  "locations": ["India", "Singapore", "Remote"]
}
```

**Output:**
```json
{ "success": true, "visibility": "recruiters_only", "titles_set": 5 }
```

---

#### 4.6 `save_job`

Save a job posting to the user's Saved Jobs list.

**Input:**
```json
{ "job_id": "4252026496" }
```

**Why P1:** Enables a "find and queue" workflow — Claude surfaces relevant jobs, saves
them, user reviews the list and decides which to act on.

---

#### 4.7 `get_saved_jobs`

Return the user's current Saved Jobs list with status (saved, applied, etc.).

**Output:**
```json
{
  "jobs": [
    {
      "title": "Senior AI Engineer",
      "company": "Mastercard",
      "location": "Singapore",
      "saved_at": "2026-03-06",
      "status": "saved",
      "job_url": "https://linkedin.com/jobs/view/..."
    }
  ]
}
```

---

### P2 — Quality of life, high value over time

#### 4.8 `get_profile_analytics` (fix existing)

Currently times out intermittently. Needs a more resilient selector strategy.

**Target output:**
```json
{
  "profile_views": 193,
  "post_impressions": 494,
  "search_appearances": 48,
  "period": "last_7_days"
}
```

**Fix:** Use `page.wait_for_selector` with a longer timeout + fallback to text scraping
if the analytics widget doesn't load within 5s.

---

#### 4.9 `get_job_recommendations`

Return LinkedIn's "Jobs you may be interested in" personalised feed as structured data.

**Why P2:** Useful for passive discovery — run once a week to see what LinkedIn's
algorithm thinks is a match. Currently `search_jobs` is keyword-driven; this surfaces
roles you might not have searched for.

---

#### 4.10 `search_jobs` — structured `jobs[]` improvements

Current issues (observed in live testing):
- Page header row ("data engineer in Singapore / 200+ results") is being parsed as a job entry
- "Are these results helpful?" noise row appears in `jobs[]`
- Some entries have company and location fields swapped when the title has a "with verification" suffix immediately after

**Fix:** Add title-level filtering — drop any entry where `title` matches known noise
patterns (`/^\d+ results$/`, `/are these results helpful/i`, `/jobs you may be interested in/i`)

---

## 5. Recommended Implementation Order

```
Week 1:  search_people + get_company_people  (unblocks all outreach workflows)
Week 2:  update_profile_headline + update_profile_skills + set_open_to_work
Week 3:  save_job + get_saved_jobs + search_jobs noise fix
Week 4:  get_profile_analytics fix + get_job_recommendations
```

---

## 6. Rate Limit Strategy

LinkedIn's anti-bot detection triggered during this session at ~5 tool calls in under
3 minutes. Recommended mitigations:

- Add a minimum 3s inter-call delay for all people/job search tools
- Randomise delays between 2–5s (avoid fixed intervals which pattern-match easier)
- For bulk outreach (sending 5+ connection requests), space them 60–120s apart
- On CAPTCHA detection, surface a clear error and pause all further calls for 10 minutes

---

## 7. Success Criteria

A complete job search session should be able to:

1. Search jobs at a target company by keyword
2. Find EXL alumni (or any past-company alumni) currently at that company
3. Send tailored connection requests to 3–5 of them in one session
4. Update profile headline and top skills in under 30 seconds
5. Save shortlisted jobs to a queue for follow-up
6. Pull weekly profile analytics to track whether changes improved recruiter visibility

All of the above within LinkedIn's rate limits, within Cowork's 60s MCP call ceiling.
