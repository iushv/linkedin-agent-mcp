"""DOM and text extraction helpers shared by feed/company/engagement tools.

Split out of tools/feed.py so the volatile, LinkedIn-DOM-coupled logic lives
in focused modules with fixture-driven tests:

- activity_text: pure innerText parsers (no Playwright)
- post_identity: post URL/URN identifier resolution from cards
- engagement_rows: reaction/comment modal row extraction
- feed_cards: feed and recent-activity card resolution
- profile_analytics: profile analytics widget extraction
"""
