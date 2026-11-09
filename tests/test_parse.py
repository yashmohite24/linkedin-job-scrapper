from datetime import datetime, timezone
from pathlib import Path

from linkedin_jobs.parse import parse_detail, parse_listing

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_listing_cards():
    cards = parse_listing((FIXTURES / "listing_page.html").read_text(encoding="utf-8"))

    assert [card.job_id for card in cards] == ["4012345678", "5098765432"]
    first, second = cards
    assert first.designation == "Product Manager"
    assert first.company_name == "Acme Corp"
    assert first.location == "Bengaluru, Karnataka, India"
    assert first.job_url == "https://www.linkedin.com/jobs/view/4012345678"
    assert first.linkedin_listed_at == datetime(2026, 11, 9, tzinfo=timezone.utc)

    assert second.designation == "Director of Product"
    assert second.company_name == "P&G"
    assert second.location == "Mumbai, Maharashtra, India"
    assert second.job_url == "https://www.linkedin.com/jobs/view/5098765432"
    assert second.linkedin_listed_at == datetime(2026, 11, 9, 4, 15, tzinfo=timezone.utc)


def test_parse_detail_with_hiring_team():
    detail = parse_detail((FIXTURES / "detail_with_hiring.html").read_text(encoding="utf-8"))

    assert detail.designation == "Product Manager"
    assert detail.description == "Build the payments roadmap.\nPartner with design.\nShip weekly."
    assert detail.hiring_team == "Jane Doe; Amit Shah"


def test_parse_detail_without_hiring_team():
    detail = parse_detail((FIXTURES / "detail_without_hiring.html").read_text(encoding="utf-8"))

    assert detail.description == "Own discovery for the India payments product."
    assert detail.hiring_team is None


def test_parse_hiring_team_stops_at_the_next_heading():
    detail = parse_detail((FIXTURES / "detail_hiring_siblings.html").read_text(encoding="utf-8"))

    assert detail.hiring_team == "Priya Nair"
