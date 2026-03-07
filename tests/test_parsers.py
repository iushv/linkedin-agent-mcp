"""Unit tests for internal parsing helpers — no browser or mocking needed."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# ── feed.py parsers ──
from linkedin_mcp_server.tools.feed import (
    _build_activity_post_analytics_item,
    _build_post_analytics_item,
    _extract_metric,
    _extract_post_from_text,
    _extract_post_url,
    _extract_time_ago,
    _looks_like_analytics_card_text,
    _normalize_post_url,
    _parse_posts_from_activity_text,
)

# ── job.py parsers ──
from linkedin_mcp_server.tools.job import (
    _dedupe_repeated_text,
    _extract_job_id,
    _extract_posting_date,
    _first_locator_text,
    _is_verification_title_line,
    _normalize_job_url,
    _parse_job_card_text,
    _parse_job_search_results_text,
)

# ── messaging.py parsers ──
from linkedin_mcp_server.tools.messaging import (
    _parse_conversation_item,
    _parse_message_item,
)

# ── network.py parsers ──
from linkedin_mcp_server.tools.network import (
    _extract_mutual_connections,
    _extract_name_headline,
)


# ────────────────────────────────────────
# feed._extract_metric
# ────────────────────────────────────────


class TestExtractMetric:
    def test_integer_before_phrase(self):
        assert _extract_metric("123 reactions", "reactions") == 123

    def test_k_suffix(self):
        result = _extract_metric("1.2K comments on this", "comments")
        assert result is not None and result >= 1000

    def test_phrase_colon_format(self):
        assert _extract_metric("reactions: 5", "reactions") == 5

    def test_no_match_returns_none(self):
        assert _extract_metric("no metrics here", "reactions") is None


# ────────────────────────────────────────
# feed._extract_time_ago
# ────────────────────────────────────────


class TestExtractTimeAgo:
    def test_hours_ago(self):
        result = _extract_time_ago("Posted 2h ago by someone")
        assert result is not None and "2" in result

    def test_days(self):
        result = _extract_time_ago("3d ago")
        assert result is not None and "3" in result

    def test_month_ago(self):
        result = _extract_time_ago("1mo ago")
        assert result is not None

    def test_no_match(self):
        assert _extract_time_ago("no time info") is None


# ────────────────────────────────────────
# feed._extract_post_from_text
# ────────────────────────────────────────


class TestExtractPostFromText:
    def test_full_post(self):
        text = (
            "Jane Doe\n"
            "This is a great article about testing!\n"
            "5 reactions\n"
            "2 comments\n"
            "2h ago\n"
        )
        result = _extract_post_from_text(text)
        assert result["author"] == "Jane Doe"
        assert "great article" in result["text"]
        assert result["reactions_count"] == 5
        assert result["comments_count"] == 2
        assert result["time_ago"] is not None


class TestBuildPostAnalyticsItem:
    def test_rich_post_item(self):
        text = (
            "Jane Doe\n"
            "Testing richer analytics output\n"
            "5 reactions\n"
            "2 comments\n"
            "1 repost\n"
            "100 impressions\n"
            "2h ago\n"
        )
        result = _build_post_analytics_item(
            text,
            url="https://www.linkedin.com/feed/update/urn:li:activity:123",
        )
        assert result["author"] == "Jane Doe"
        assert (
            result["url"] == "https://www.linkedin.com/feed/update/urn:li:activity:123"
        )
        assert result["reactions"] == 5
        assert result["comments"] == 2
        assert result["impressions"] == 100
        assert result["time_ago"] is not None


class TestBuildActivityPostAnalyticsItem:
    def test_activity_card_parser_uses_time_marker_for_content(self):
        text = (
            "Feed post number 1\n"
            "Jane Doe\n"
            "Data Consultant | Building AI systems\n"
            "2h ago\n"
            "This is the actual post content\n"
            "120 impressions\n"
            "3 comments\n"
            "Repost\n"
        )

        result = _build_activity_post_analytics_item(
            text,
            url="https://www.linkedin.com/feed/update/urn:li:activity:123",
        )

        assert result["author"] == "Jane Doe"
        assert result["text_preview"] == "This is the actual post content"
        assert result["impressions"] == 120
        assert result["comments"] == 3
        assert result["url"] == "https://www.linkedin.com/feed/update/urn:li:activity:123"

    def test_activity_card_parser_strips_visibility_metadata(self):
        text = (
            "Feed post number 2\n"
            "Jane Doe\n"
            "1 day ago • Visible to anyone on or off LinkedIn\n"
            "Actual post body line\n"
            "43 impressions\n"
        )

        result = _build_activity_post_analytics_item(text)

        assert result["author"] == "Jane Doe"
        assert result["text_preview"] == "Actual post body line"
        assert result["impressions"] == 43

    def test_activity_card_parser_strips_follow_noise(self):
        text = (
            "Feed post number 3\n"
            "Jane Doe\n"
            "1w ago\n"
            "Follow\n"
            "AI Engineering is just 20% AI...\n"
            "24 comments\n"
        )

        result = _build_activity_post_analytics_item(text)

        assert result["author"] == "Jane Doe"
        assert result["text_preview"] == "AI Engineering is just 20% AI..."
        assert result["comments"] == 24


class TestNormalizePostUrl:
    def test_relative_feed_url(self):
        result = _normalize_post_url("/feed/update/urn:li:activity:123/")
        assert result == "https://www.linkedin.com/feed/update/urn:li:activity:123/"

    def test_non_post_url_returns_none(self):
        assert _normalize_post_url("/messaging/thread/123/") is None


class TestExtractPostUrl:
    @pytest.mark.asyncio
    async def test_skips_missing_selectors_without_get_attribute_wait(self):
        card = MagicMock()

        missing = MagicMock()
        missing.count = AsyncMock(return_value=0)
        missing.first = MagicMock()
        missing.first.get_attribute = AsyncMock()

        present = MagicMock()
        present.count = AsyncMock(return_value=1)
        present.first = MagicMock()
        present.first.get_attribute = AsyncMock(
            return_value="/feed/update/urn:li:activity:123/"
        )

        def _locator(selector: str):
            return present if selector == "a[href*='/feed/update/']" else missing

        card.locator = MagicMock(side_effect=_locator)

        result = await _extract_post_url(card)

        assert result == "https://www.linkedin.com/feed/update/urn:li:activity:123/"
        missing.first.get_attribute.assert_not_called()


class TestLooksLikeAnalyticsCardText:
    def test_metrics_present_returns_true(self):
        assert _looks_like_analytics_card_text("Post body\n12 impressions\n3 comments")

    def test_short_or_metricless_text_returns_false(self):
        assert not _looks_like_analytics_card_text("Like\nComment")


class TestParsePostsFromActivityText:
    def test_trailing_action_labels_do_not_block_metric_scan(self):
        text = (
            "Ayush Kumar\n"
            "posted this •\n"
            "2d\n"
            "2d\n"
            "Shipping the MCP update today\n"
            "7\n"
            "3 comments\n"
            "1 repost\n"
            "React\n"
            "Comment\n"
            "Repost\n"
            "Send\n"
            "Ayush Kumar\n"
        )

        posts = _parse_posts_from_activity_text(text, limit=5)

        assert len(posts) == 1
        assert posts[0]["author"] == "Ayush Kumar"
        assert posts[0]["reactions"] == 7
        assert posts[0]["comments"] == 3
        assert posts[0]["reposts"] == 1
        assert "_debug_tail" not in posts[0]

    def test_bare_reaction_count_without_comments_is_parsed(self):
        text = (
            "Ayush Kumar\n"
            "posted this •\n"
            "1d\n"
            "1d\n"
            "A short post without comment counts\n"
            "1\n"
            "React\n"
            "Comment\n"
            "Repost\n"
            "Send\n"
            "Ayush Kumar\n"
        )

        posts = _parse_posts_from_activity_text(text, limit=5)

        assert len(posts) == 1
        assert posts[0]["reactions"] == 1
        assert posts[0]["comments"] is None
        assert posts[0]["reposts"] is None

    def test_truncates_at_next_profile_section_for_last_post(self):
        text = (
            "Ayush Kumar\n"
            "posted this •\n"
            "3d\n"
            "3d\n"
            "Last visible activity post\n"
            "1\n"
            "React\n"
            "Comment\n"
            "Repost\n"
            "Send\n"
            "Experience\n"
            "Staff Engineer\n"
            "Acme Corp\n"
            "Interests\n"
            "Cassie Kozyrkov\n"
        )

        posts = _parse_posts_from_activity_text(text, limit=5)

        assert len(posts) == 1
        assert posts[0]["reactions"] == 1
        assert "Experience" not in posts[0]["text_preview"]

    def test_truncates_at_top_voices_section(self):
        text = (
            "Ayush Kumar\n"
            "posted this •\n"
            "5d\n"
            "5d\n"
            "Monitoring the situation\n"
            "1\n"
            "React\n"
            "Comment\n"
            "Repost\n"
            "Send\n"
            "Top Voices\n"
            "Cassie Kozyrkov\n"
        )

        posts = _parse_posts_from_activity_text(text, limit=5)

        assert len(posts) == 1
        assert posts[0]["reactions"] == 1
        assert "Top Voices" not in posts[0]["text_preview"]


# ────────────────────────────────────────
# job.py helpers
# ────────────────────────────────────────


class TestNormalizeJobUrl:
    def test_relative_job_url(self):
        result = _normalize_job_url("/jobs/view/4252026496/")
        assert result == "https://www.linkedin.com/jobs/view/4252026496/"

    def test_non_job_url_returns_none(self):
        assert _normalize_job_url("/in/test-user/") is None


class TestExtractJobId:
    def test_from_url(self):
        assert (
            _extract_job_id("https://www.linkedin.com/jobs/view/4252026496/")
            == "4252026496"
        )

    def test_from_attribute(self):
        assert _extract_job_id("4252026496") == "4252026496"


class TestParseJobCardText:
    def test_basic_job_card(self):
        text = "Senior Python Engineer\nAcme Inc\nRemote\n12 applicants\n"
        result = _parse_job_card_text(text)
        assert result["title"] == "Senior Python Engineer"
        assert result["company"] == "Acme Inc"
        assert result["location"] == "Remote"


class TestExtractPostingDate:
    def test_matches_relative_posting_age(self):
        assert _extract_posting_date("2 days ago") == "2 days ago"

    def test_non_posting_line_returns_none(self):
        assert _extract_posting_date("Singapore") is None


class TestParseJobSearchResultsText:
    def test_parses_basic_job_blocks(self):
        text = (
            "Data Engineer\n"
            "Acme Pte Ltd\n"
            "Singapore\n"
            "2 days ago\n"
            "Senior Data Engineer\n"
            "Globex\n"
            "Remote\n"
            "1 week ago\n"
        )

        result = _parse_job_search_results_text(text, limit=10)

        assert len(result) == 2
        assert result[0]["title"] == "Data Engineer"
        assert result[0]["company"] == "Acme Pte Ltd"
        assert result[0]["location"] == "Singapore"
        assert result[0]["posting_date"] == "2 days ago"
        assert result[0]["job_id"] is None
        assert result[0]["url"] is None

    def test_dedupes_duplicate_titles_from_raw_text(self):
        text = (
            "Django Developer Django Developer with verification\n"
            "Acme Pte Ltd\n"
            "Singapore\n"
            "3 days ago\n"
        )

        result = _parse_job_search_results_text(text, limit=10)

        assert len(result) == 1
        assert result[0]["title"] == "Django Developer with verification"

    def test_skips_secondary_verification_title_line(self):
        text = (
            "Data Engineer\n"
            "Data Engineer with verification\n"
            "Astek\n"
            "Singapore, Singapore (On-site)\n"
            "1 week ago\n"
            "Easy Apply\n"
        )

        result = _parse_job_search_results_text(text, limit=10)

        assert len(result) == 1
        assert result[0]["title"] == "Data Engineer"
        assert result[0]["company"] == "Astek"
        assert result[0]["location"] == "Singapore, Singapore (On-site)"
        assert result[0]["posting_date"] == "1 week ago"

    def test_filters_noise_rows_from_raw_search_text(self):
        text = (
            "Are these results helpful?\n"
            "Data Engineer\n"
            "Acme\n"
            "Singapore\n"
            "1 week ago\n"
        )

        result = _parse_job_search_results_text(text, limit=10)

        assert len(result) == 1
        assert result[0]["title"] == "Data Engineer"

    def test_drops_structurally_invalid_raw_rows(self):
        text = (
            "Data engineer in Singapore\n"
            "200+ results\n"
            "1 week ago\n"
        )

        result = _parse_job_search_results_text(text, limit=10)

        assert result == []


class TestIsVerificationTitleLine:
    def test_matches_secondary_verification_line(self):
        assert _is_verification_title_line(
            "Data Engineer with verification",
            "Data Engineer",
        )

    def test_rejects_non_matching_line(self):
        assert not _is_verification_title_line("Astek", "Data Engineer")


class TestDedupeRepeatedText:
    def test_collapses_adjacent_duplicate_title(self):
        value = (
            "Software Engineer (Python) - Remote "
            "Software Engineer (Python) - Remote"
        )

        assert _dedupe_repeated_text(value) == "Software Engineer (Python) - Remote"

    def test_leaves_distinct_text_unchanged(self):
        value = "Senior Python Engineer"

        assert _dedupe_repeated_text(value) == value

    def test_keeps_longer_suffix_bearing_half(self):
        value = "Django Developer Django Developer with verification"

        assert _dedupe_repeated_text(value) == "Django Developer with verification"


class TestFirstLocatorText:
    @pytest.mark.asyncio
    async def test_skips_missing_selectors_without_inner_text_wait(self):
        scope = MagicMock()
        missing = MagicMock()
        missing.count = AsyncMock(return_value=0)
        missing.first = MagicMock()
        missing.first.inner_text = AsyncMock()

        present = MagicMock()
        present.count = AsyncMock(return_value=1)
        present.first = MagicMock()
        present.first.inner_text = AsyncMock(return_value="Senior Python Engineer")

        def _locator(selector: str):
            return missing if selector == ".missing" else present

        scope.locator = MagicMock(side_effect=_locator)

        result = await _first_locator_text(scope, (".missing", ".present"))

        assert result == "Senior Python Engineer"
        missing.first.inner_text.assert_not_called()


# ────────────────────────────────────────
# messaging._parse_conversation_item
# ────────────────────────────────────────


class TestParseConversationItem:
    def test_multi_line(self):
        text = "Alice Smith\nHey, how are you?\nYesterday\n"
        result = _parse_conversation_item(text)
        assert result["name"] == "Alice Smith"
        assert result["preview"] == "Hey, how are you?"
        assert result["timestamp"] == "Yesterday"
        assert result["unread"] is False

    def test_unread_flag(self):
        text = "Bob\nUnread message\n5m ago\n"
        result = _parse_conversation_item(text)
        assert result["unread"] is True


# ────────────────────────────────────────
# messaging._parse_message_item
# ────────────────────────────────────────


class TestParseMessageItem:
    def test_full_message(self):
        text = "Alice\n2:30 PM\nHello, thanks for connecting!\n"
        result = _parse_message_item(text)
        assert result["sender"] == "Alice"
        assert result["timestamp"] == "2:30 PM"
        assert "thanks for connecting" in result["text"]

    def test_two_lines(self):
        text = "Bob\nHi there\n"
        result = _parse_message_item(text)
        assert result["sender"] == "Bob"

    def test_single_line(self):
        text = "Charlie\n"
        result = _parse_message_item(text)
        assert result["sender"] == "Charlie"


# ────────────────────────────────────────
# network._extract_mutual_connections
# ────────────────────────────────────────


class TestExtractMutualConnections:
    def test_found(self):
        assert _extract_mutual_connections("5 mutual connections") == 5

    def test_no_match(self):
        assert _extract_mutual_connections("no connections info") is None


# ────────────────────────────────────────
# network._extract_name_headline
# ────────────────────────────────────────


class TestExtractNameHeadline:
    def test_two_lines(self):
        name, headline = _extract_name_headline("Jane Doe\nSoftware Engineer at Acme\n")
        assert name == "Jane Doe"
        assert headline == "Software Engineer at Acme"

    def test_one_line(self):
        name, headline = _extract_name_headline("Bob\n")
        assert name == "Bob"
        assert headline == ""

    def test_empty(self):
        name, headline = _extract_name_headline("")
        assert name == ""
        assert headline == ""
