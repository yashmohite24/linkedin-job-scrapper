"""Parse LinkedIn guest listing cards and job detail fragments."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from bs4 import BeautifulSoup, Tag

_URN_ID = re.compile(r"urn:li:jobPosting:(\d+)")
_URL_ID = re.compile(r"/jobs/view/(?:[^/?#]*-)?(\d+)(?:[/?#]|$)")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class ListingCard:
    job_id: str
    designation: str | None
    company_name: str | None
    location: str | None
    job_url: str
    linkedin_listed_at: datetime | None


@dataclass(frozen=True)
class JobDetail:
    description: str
    hiring_team: str | None
    designation: str | None


def canonical_job_url(job_id: str) -> str:
    return f"https://www.linkedin.com/jobs/view/{job_id}"


def parse_listing(html: str) -> list[ListingCard]:
    soup = BeautifulSoup(html, "html.parser")
    cards: list[ListingCard] = []
    for node in soup.select("div.base-search-card"):
        card = _parse_card(node)
        if card is not None:
            cards.append(card)
    return cards


def parse_detail(html: str) -> JobDetail:
    soup = BeautifulSoup(html, "html.parser")
    markup = soup.select_one(".show-more-less-html__markup")
    description = _description_text(markup) if markup is not None else ""
    heading = soup.select_one("h2")
    designation = _clean(heading.get_text(" ", strip=True)) if heading is not None else None
    if designation == "":
        designation = None
    return JobDetail(
        description=description,
        hiring_team=parse_hiring_team(soup),
        designation=designation,
    )


def parse_hiring_team(soup: BeautifulSoup) -> str | None:
    scoped = soup.select_one("[class*='people-who-can-help'], [class*='hiring-team']")
    if scoped is not None:
        names = _person_names(scoped)
        if names:
            return "; ".join(names)

    for tag in soup.find_all(["h2", "h3"]):
        label = tag.get_text(" ", strip=True).lower()
        if "hiring team" not in label:
            continue
        sibling_html = []
        for sibling in tag.next_siblings:
            if getattr(sibling, "name", None) in {"h2", "h3"}:
                break
            sibling_html.append(str(sibling))
        names = _person_names(BeautifulSoup("".join(sibling_html), "html.parser"))
        if names:
            return "; ".join(names)
    return None


def _parse_card(node: Tag) -> ListingCard | None:
    job_id = _job_id(node)
    if job_id is None:
        return None
    title = node.select_one("h3.base-search-card__title")
    subtitle = node.select_one("h4.base-search-card__subtitle")
    location = node.select_one("span.job-search-card__location")
    listed = node.select_one("time.job-search-card__listdate, time.job-search-card__listdate--new")
    company = None
    if subtitle is not None:
        company_link = subtitle.find("a")
        company_source = company_link if company_link is not None else subtitle
        company = _clean(company_source.get_text(" ", strip=True)) or None
    designation = _clean(title.get_text(" ", strip=True)) if title is not None else None
    place = _clean(location.get_text(" ", strip=True)) if location is not None else None
    return ListingCard(
        job_id=job_id,
        designation=designation or None,
        company_name=company,
        location=place or None,
        job_url=canonical_job_url(job_id),
        linkedin_listed_at=_parse_datetime(listed.get("datetime") if listed is not None else None),
    )


def _job_id(node: Tag) -> str | None:
    urn = node.get("data-entity-urn")
    if isinstance(urn, str):
        match = _URN_ID.search(urn)
        if match:
            return match.group(1)
    link = node.select_one("a.base-card__full-link")
    href = link.get("href") if link is not None else None
    if isinstance(href, str):
        match = _URL_ID.search(href)
        if match:
            return match.group(1)
    return None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _description_text(node: Tag) -> str:
    parts: list[str] = []
    for element in node.find_all(["p", "li"]):
        text = _clean(element.get_text(" ", strip=True))
        if text:
            parts.append(text)
    if parts:
        return "\n".join(parts)
    return _clean(node.get_text(" ", strip=True))


def _person_names(node: Tag) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for anchor in node.find_all("a"):
        name = _clean(anchor.get_text(" ", strip=True))
        if not name or len(name) > 80 or name in seen:
            continue
        lowered = name.lower()
        if lowered in {"see more", "show more", "linkedin"}:
            continue
        if "sign in" in lowered or lowered.startswith("join"):
            continue
        seen.add(name)
        names.append(name)
    return names


def _clean(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()
