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
        assert (
            card.current_company
            == "Director, Global Fraud Decision Science at American Express"
        )
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


class TestPersonCard2025Layout:
    """LinkedIn's 2025 people card puts the degree directly under the name.

    That previously left headline null and stored the headline text as the
    location, which made every result's employer unverifiable.
    """

    def test_degree_line_after_name_still_yields_headline(self):
        card = _parse_person_card_text(
            "\n".join(
                [
                    "Abhilash Adavi",
                    "• 2nd",
                    "AI Engineer @ Google",
                    "Bengaluru, Karnataka, India",
                ]
            ),
            profile_url="https://www.linkedin.com/in/abhilash-adavi/",
        )

        assert card is not None
        assert card.headline == "AI Engineer @ Google"
        assert card.location == "Bengaluru, Karnataka, India"
        assert card.connection_degree == "2nd"

    def test_headline_with_comma_is_not_mistaken_for_location(self):
        card = _parse_person_card_text(
            "\n".join(
                [
                    "Rishu Roy",
                    "• 3rd+",
                    "AI @ Google, ex-Amazon",
                    "Hyderabad, India",
                ]
            ),
            profile_url="https://www.linkedin.com/in/rishu-roy/",
        )

        assert card is not None
        assert card.headline == "AI @ Google, ex-Amazon"
        assert card.location == "Hyderabad, India"

    def test_location_only_card_does_not_become_a_headline(self):
        card = _parse_person_card_text(
            "\n".join(["Someone Anon", "• 2nd", "Bengaluru, India"]),
            profile_url="https://www.linkedin.com/in/someone-anon/",
        )

        assert card is not None
        assert card.headline is None
        assert card.location == "Bengaluru, India"

    def test_legacy_layout_still_parses(self):
        """The pre-2025 order must keep working."""
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
        )

        assert card is not None
        assert card.headline == "Senior ML Engineer at Mastercard"
        assert card.location == "Singapore"
        assert card.connection_degree == "2nd"
        assert card.shared_connections == 3
