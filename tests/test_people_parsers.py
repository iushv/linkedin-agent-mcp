"""Tests for people-search parsing helpers."""

from linkedin_mcp_server.tools.people import (
    _card_matches_filters,
    _extract_connection_degree,
    _extract_shared_connections,
    _normalize_person_profile_url,
    _parse_person_card_text,
)


class TestNormalizePersonProfileUrl:
    def test_relative_url_normalized(self):
        assert _normalize_person_profile_url("/in/priya-sharma/") == (
            "https://www.linkedin.com/in/priya-sharma/"
        )

    def test_non_person_url_rejected(self):
        assert _normalize_person_profile_url("/company/mastercard/") is None


class TestParseHelpers:
    def test_extract_connection_degree(self):
        assert _extract_connection_degree("2nd degree connection") == "2nd"

    def test_extract_shared_connections(self):
        assert _extract_shared_connections("3 shared connections") == 3

    def test_parse_person_card_text(self):
        card = _parse_person_card_text(
            "\n".join(
                [
                    "Priya Sharma",
                    "Senior ML Engineer at Mastercard",
                    "Singapore",
                    "2nd degree connection",
                    "3 shared connections",
                ]
            ),
            profile_url="https://www.linkedin.com/in/priya-sharma/",
            default_current_company="Mastercard",
            default_past_company="EXL",
        )

        assert card is not None
        assert card.name == "Priya Sharma"
        assert card.profile_url == "https://www.linkedin.com/in/priya-sharma/"
        assert card.headline == "Senior ML Engineer at Mastercard"
        assert card.location == "Singapore"
        assert card.connection_degree == "2nd"
        assert card.shared_connections == 3
        assert card.current_company == "Mastercard"

    def test_parse_person_card_requires_name_and_profile(self):
        assert _parse_person_card_text("", profile_url=None) is None

    def test_parse_person_card_strips_third_plus_suffix(self):
        card = _parse_person_card_text(
            "\n".join(
                [
                    "Chu-Jen (Nick) Shao • 3rd+",
                    "Machine Learning Engineer at TikTok",
                    "Singapore",
                ]
            ),
            profile_url="https://www.linkedin.com/in/chu-jen-nick-shao/",
        )

        assert card is not None
        assert card.name == "Chu-Jen (Nick) Shao"

    def test_parse_person_card_extracts_current_and_past_company_lines(self):
        card = _parse_person_card_text(
            "\n".join(
                [
                    "Divya Monga • 2nd",
                    "Director, Global Fraud Decision Science at American Express",
                    "Singapore, Singapore",
                    "Past: Associate managing consultant at Mastercard",
                    "Current: Director, Global Fraud Decision Science at American Express",
                ]
            ),
            profile_url="https://www.linkedin.com/in/divyamonga/",
        )

        assert card is not None
        assert card.current_company == "Director, Global Fraud Decision Science at American Express"
        assert card.past_companies == ["Associate managing consultant at Mastercard"]

    def test_card_matches_filters_uses_raw_text_for_past_company(self):
        card = _parse_person_card_text(
            "\n".join(
                [
                    "Divya Monga • 2nd",
                    "Director, Global Fraud Decision Science at American Express",
                    "Singapore, Singapore",
                ]
            ),
            profile_url="https://www.linkedin.com/in/divyamonga/",
        )

        assert card is not None
        assert _card_matches_filters(
            card,
            raw_text="Past: Associate managing consultant at Mastercard",
            past_company="Mastercard",
            location="Singapore",
        )
