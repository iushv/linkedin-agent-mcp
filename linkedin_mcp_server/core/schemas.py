"""Shared typed schemas for people and job search results."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PersonCard:
    """Normalized representation of a LinkedIn person search result."""

    name: str
    profile_url: str
    headline: str | None = None
    location: str | None = None
    connection_degree: str | None = None
    shared_connections: int | None = None
    current_company: str | None = None
    past_companies: list[str] | None = None


@dataclass
class JobCard:
    """Normalized representation of a LinkedIn job result."""

    title: str
    company: str
    location: str | None = None
    posting_date: str | None = None
    job_id: str | None = None
    job_url: str | None = None


def is_valid_person_card(card: PersonCard) -> bool:
    """A person result is valid only if name and profile URL are present."""
    return bool(card.name.strip() and card.profile_url.strip())


def is_valid_job_card(card: JobCard) -> bool:
    """A job result needs title, company, and one additional identifying field."""
    if not card.title.strip() or not card.company.strip():
        return False
    return any(
        value
        for value in (
            card.location,
            card.posting_date,
            card.job_url,
            card.job_id,
        )
    )
