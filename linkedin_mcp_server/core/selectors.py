"""Selector registry and resilient locator fallback chains for LinkedIn UI actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, cast

from patchright.async_api import Locator, Page

from .exceptions import SelectorError


class LocatorStrategy(Protocol):
    """Protocol for a single locator resolution strategy."""

    def describe(self) -> str:
        """Return a human-readable strategy description."""

    def locator(self, page: Page) -> Locator:
        """Return a Patchright locator for this strategy."""


@dataclass(frozen=True)
class AriaLabel:
    """Resolve by accessible label."""

    label: str
    exact: bool = False

    def describe(self) -> str:
        return f"aria-label:{self.label}"

    def locator(self, page: Page) -> Locator:
        return page.get_by_label(self.label, exact=self.exact)


@dataclass(frozen=True)
class Role:
    """Resolve by ARIA role and optional accessible name."""

    role: str
    name: str | None = None
    exact: bool = False

    def describe(self) -> str:
        if self.name:
            return f"role:{self.role}:{self.name}"
        return f"role:{self.role}"

    def locator(self, page: Page) -> Locator:
        return page.get_by_role(
            cast(Any, self.role),
            name=self.name,
            exact=self.exact,
        )


@dataclass(frozen=True)
class Text:
    """Resolve by visible text."""

    text: str
    exact: bool = False

    def describe(self) -> str:
        return f"text:{self.text}"

    def locator(self, page: Page) -> Locator:
        return page.get_by_text(self.text, exact=self.exact)


@dataclass(frozen=True)
class CSS:
    """Resolve by CSS selector."""

    selector: str

    def describe(self) -> str:
        return f"css:{self.selector}"

    def locator(self, page: Page) -> Locator:
        return page.locator(self.selector)


@dataclass(frozen=True)
class LocatorChain:
    """Try multiple locator strategies in order until one resolves."""

    name: str
    strategies: list[LocatorStrategy]

    async def resolve(self, page: Page, timeout: int = 5000) -> Locator:
        """Return the first strategy locator that has at least one match."""
        attempted: list[str] = []

        for strategy in self.strategies:
            strategy_desc = strategy.describe()
            attempted.append(strategy_desc)

            try:
                locator = strategy.locator(page)
                if await locator.count() > 0:
                    return locator
            except Exception as exc:  # pragma: no cover - defensive fallback
                attempted.append(f"{strategy_desc}:error:{type(exc).__name__}")

        raise SelectorError(
            message=f"Could not resolve selector chain '{self.name}'",
            chain_name=self.name,
            tried_strategies=attempted,
            url=page.url,
            context={"page_debug": await collect_page_debug(page, timeout)},
        )

    async def find(self, page: Page, timeout: int = 5000) -> Locator:
        """Return the first matched element from the chain."""
        locator = await self.resolve(page, timeout=timeout)
        return locator.first


async def collect_page_debug(page: Page, timeout: int = 1000) -> dict[str, object]:
    """Collect lightweight telemetry for selector debugging."""

    debug: dict[str, object] = {
        "url": page.url,
    }

    try:
        debug["title"] = await page.title()
    except Exception:  # pragma: no cover - best-effort telemetry
        pass

    try:
        body_text = await page.locator("body").inner_text(timeout=timeout)
        if body_text:
            debug["body_preview"] = body_text[:400]
    except Exception:  # pragma: no cover - best-effort telemetry
        pass

    return debug


def chain(name: str, *strategies: LocatorStrategy) -> LocatorChain:
    """Helper to build a locator chain with concise syntax."""

    return LocatorChain(name=name, strategies=list(strategies))


SELECTORS: dict[str, dict[str, LocatorChain]] = {
    "post_composer": {
        "trigger": chain(
            "post_trigger",
            Role("button", "Start a post"),
            CSS(".share-box-feed-entry__trigger"),
            CSS("[data-placeholder*='Start a post']"),
            AriaLabel("Start a post"),
        ),
        "text_editor": chain(
            "post_text_editor",
            Role("textbox", "Text editor"),
            CSS(".share-creation-state__text-editor .ql-editor"),
            CSS(".ql-editor[contenteditable='true']"),
        ),
        "media_button": chain(
            "post_media_button",
            AriaLabel("Add media"),
            Role("button", "Add media"),
            CSS("button.share-actions__media"),
        ),
        "visibility": chain(
            "post_visibility_button",
            Role("button", "Anyone"),
            Role("button", "Connections"),
            CSS("button.share-creation-state__trigger"),
        ),
        "submit": chain(
            "post_submit",
            Role("button", "Post"),
            AriaLabel("Post"),
            CSS("button.share-actions__primary-action"),
        ),
        "poll_button": chain(
            "post_poll_button",
            AriaLabel("Create a poll"),
            Role("button", "Create a poll"),
            CSS("button[aria-label*='poll' i]"),
        ),
        "poll_question": chain(
            "poll_question_input",
            Role("textbox", "Ask a question"),
            CSS("input[name='question']"),
        ),
        "poll_option_1": chain(
            "poll_option_1",
            Role("textbox", "Option 1"),
            CSS("input[name='option1']"),
        ),
        "poll_option_2": chain(
            "poll_option_2",
            Role("textbox", "Option 2"),
            CSS("input[name='option2']"),
        ),
        "duration_dropdown": chain(
            "poll_duration",
            Role("combobox"),
            CSS("select[name='duration']"),
        ),
    },
    "post_actions": {
        "menu": chain(
            "post_overflow_menu",
            AriaLabel("Open control menu"),
            Role("button", "More actions"),
            CSS("button[aria-label*='more' i]"),
        ),
        "delete": chain(
            "post_delete_action",
            Role("menuitem", "Delete post"),
            Text("Delete post"),
            CSS("button[aria-label*='Delete' i]"),
        ),
        "confirm_delete": chain(
            "post_confirm_delete",
            Role("button", "Delete"),
            Text("Delete"),
            CSS("button.artdeco-button--primary"),
        ),
        "repost": chain(
            "post_repost",
            Role("button", "Repost"),
            AriaLabel("Repost"),
            CSS("button[aria-label*='Repost' i]"),
        ),
        "repost_now": chain(
            "post_repost_now",
            Text("Repost"),
            Role("button", "Repost"),
            CSS("button.share-box_actions__primary-action"),
        ),
        "repost_with_thoughts": chain(
            "post_repost_with_thoughts",
            Text("Repost with your thoughts"),
            CSS("button[aria-label*='thoughts' i]"),
        ),
    },
    "engagement": {
        "like": chain(
            "engagement_like_button",
            Role("button", "Like"),
            AriaLabel("Like"),
            CSS("button[aria-label*='Like' i]"),
        ),
        "comment_input": chain(
            "engagement_comment_input",
            Role("textbox", "Add a comment"),
            CSS("div.comments-comment-box__editor"),
            CSS("textarea[placeholder*='comment' i]"),
        ),
        "comment_post": chain(
            "engagement_comment_post",
            Role("button", "Post"),
            CSS("button.comments-comment-box__submit-button"),
        ),
        "reply": chain(
            "engagement_reply_button",
            Text("Reply"),
            CSS("button.comments-comment-social-bar__reply-action"),
        ),
        "comment_like": chain(
            "engagement_comment_like_button",
            Text("Like"),
            CSS("button.comments-comment-social-bar__reaction-action"),
        ),
    },
    "messaging": {
        "conversation_items": chain(
            "messaging_conversation_items",
            Role("listitem"),
            Text("Unread"),
            CSS("li.msg-conversations-container__convo-item"),
            CSS("li.msg-conversation-listitem"),
            CSS("li.msg-convo-wrapper"),
            CSS("li[class*='msg-conversation']"),
            CSS("div.msg-conversations-container__conversations-list li"),
            CSS("ul.msg-conversations-container__conversations-list > li"),
            CSS("section.messaging li[data-control-name]"),
        ),
        "message_input": chain(
            "messaging_input",
            Role("textbox", "Write a message"),
            CSS("div.msg-form__contenteditable"),
        ),
        "send_button": chain(
            "messaging_send_button",
            Role("button", "Send"),
            AriaLabel("Send"),
            CSS("button.msg-form__send-button"),
        ),
        "thread_messages": chain(
            "messaging_thread_messages",
            CSS("li.msg-s-message-list__event"),
            CSS("li.msg-conversation-card"),
            CSS("li.msg-s-event-listitem"),
            CSS("li[class*='msg-s-message-list']"),
            CSS("div.msg-s-message-list-container li"),
            CSS("ul.msg-s-message-list > li"),
            CSS("section.msg-s-message-list-container li[class*='msg-s']"),
        ),
    },
    "network": {
        "connect_button": chain(
            "network_connect_button",
            Role("button", "Connect"),
            AriaLabel("Connect"),
            CSS("button[aria-label*='Invite' i]"),
        ),
        "add_note": chain(
            "network_add_note",
            Role("button", "Add a note"),
            Text("Add a note"),
            CSS("button[aria-label*='Add a note' i]"),
        ),
        "note_input": chain(
            "network_note_input",
            Role("textbox"),
            CSS("textarea#custom-message"),
        ),
        "send_invite": chain(
            "network_send_invite",
            Role("button", "Send"),
            CSS("button[aria-label*='Send now' i]"),
        ),
        "follow_button": chain(
            "network_follow_button",
            Role("button", "Follow"),
            Text("Follow"),
            CSS("button[aria-label*='Follow' i]"),
        ),
        "more_actions": chain(
            "network_more_actions",
            Role("button", "More"),
            AriaLabel("More actions"),
            CSS("button[aria-label*='More actions' i]"),
        ),
        "invitation_rows": chain(
            "network_invitation_rows",
            Role("listitem"),
            Text("Accept"),
            CSS("li.invitation-card"),
            CSS("li.mn-invitation-manager__invitation-card"),
        ),
    },
    "feed": {
        "post_cards": chain(
            "feed_post_cards",
            Role("article"),
            Text("Like"),
            CSS("div.feed-shared-update-v2"),
            CSS("div.occludable-update"),
        ),
    },
    "people": {
        "search_result_cards": chain(
            "people_search_result_cards",
            Role("listitem"),
            Text("Connect"),
            CSS("li.reusable-search__result-container"),
            CSS("div.entity-result"),
        ),
    },
    "company_people": {
        "people_cards": chain(
            "company_people_cards",
            Role("listitem"),
            Text("Message"),
            CSS("li.org-people-profile-card"),
            CSS("div.org-people-profile-card"),
        ),
        "filter_button": chain(
            "company_people_filter_button",
            Role("button", "All filters"),
            Text("All filters"),
            CSS("button[aria-label*='All filters' i]"),
        ),
    },
    "jobs": {
        "save_button": chain(
            "jobs_save_button",
            Role("button", "Save"),
            AriaLabel("Save"),
            CSS("button[aria-label*='Save' i]"),
        ),
        "unsave_button": chain(
            "jobs_unsave_button",
            Role("button", "Saved"),
            AriaLabel("Saved"),
            CSS("button[aria-pressed='true']"),
        ),
        "saved_job_cards": chain(
            "jobs_saved_job_cards",
            Role("listitem"),
            Text("Saved"),
            CSS("li.jobs-saved-job-card-list__list-item"),
            CSS("li.jobs-search-results__list-item"),
        ),
        "saved_job_link": chain(
            "jobs_saved_job_link",
            Role("link"),
            CSS("a[href*='/jobs/view/']"),
        ),
        "saved_job_status": chain(
            "jobs_saved_job_status",
            Text("Saved"),
            Text("Applied"),
            CSS(".job-card-list__footer-wrapper"),
        ),
        "recommendation_cards": chain(
            "jobs_recommendation_cards",
            Role("listitem"),
            Text("Recommended for you"),
            CSS("li.jobs-search-results__list-item"),
            CSS("div.job-card-container"),
        ),
    },
    "profile": {
        "headline_text": chain(
            "profile_headline_text",
            CSS("div.text-body-medium.break-words"),
            CSS(".pv-text-details__left-panel .text-body-medium"),
        ),
        "intro_edit": chain(
            "profile_intro_edit",
            AriaLabel("Edit intro"),
            Role("button", "Edit intro"),
            CSS("button[aria-label*='Edit intro' i]"),
        ),
        "headline_input": chain(
            "profile_headline_input",
            Role("textbox", "Headline"),
            CSS("input[name='headline']"),
            CSS(
                "#single-line-text-form-component-formElement-urn-li-jalapeno-edit-top-card-headline"
            ),
        ),
        "modal_save": chain(
            "profile_modal_save",
            Role("button", "Save"),
            Text("Save"),
            CSS("button[aria-label='Save']"),
        ),
        "open_to_work_button": chain(
            "profile_open_to_work_button",
            Role("button", "Open to"),
            Text("Open to"),
            CSS("button[aria-label*='Open to work' i]"),
        ),
        "open_to_work_job_title": chain(
            "profile_open_to_work_job_title",
            Role("textbox", "Job title"),
            CSS("input[placeholder*='title' i]"),
        ),
        "open_to_work_location": chain(
            "profile_open_to_work_location",
            Role("textbox", "Location"),
            CSS("input[placeholder*='location' i]"),
        ),
        "open_to_work_recruiters_only": chain(
            "profile_open_to_work_recruiters_only",
            Role("radio", "Recruiters only"),
            Text("Recruiters only"),
            CSS("label[for*='recruiters']"),
        ),
        "open_to_work_public": chain(
            "profile_open_to_work_public",
            Role("radio", "All LinkedIn members"),
            Text("All LinkedIn members"),
            CSS("label[for*='all-linkedin-members']"),
        ),
        "open_to_work_remove": chain(
            "profile_open_to_work_remove",
            Role("button", "Delete"),
            Text("Delete"),
            CSS("button[aria-label*='Delete from profile' i]"),
        ),
        "skills_add_button": chain(
            "profile_skills_add_button",
            Role("button", "Add skill"),
            Text("Add skill"),
            CSS("button[aria-label*='Add skill' i]"),
        ),
        "skill_input": chain(
            "profile_skill_input",
            Role("combobox"),
            CSS("input[placeholder*='skill' i]"),
            CSS("input[aria-autocomplete='list']"),
        ),
        "featured_skills_button": chain(
            "profile_featured_skills_button",
            Role("button", "Edit featured skills"),
            Text("Edit featured"),
            CSS("button[aria-label*='featured skills' i]"),
        ),
    },
    "analytics": {
        "activity_link": chain(
            "analytics_activity_link",
            Role("link", "Show all activity"),
            Text("Show all activity"),
            CSS("a[href*='/recent-activity/']"),
        ),
    },
    "common": {
        "dismiss_modal": chain(
            "common_dismiss_modal",
            AriaLabel("Dismiss"),
            AriaLabel("Close"),
            CSS("button.artdeco-modal__dismiss"),
        ),
        "file_input": chain(
            "common_file_input",
            CSS("input[type='file']"),
        ),
        "message_button": chain(
            "common_message_button",
            Role("button", "Message"),
            AriaLabel("Message"),
            CSS("button[aria-label*='Message' i]"),
        ),
        "more_actions": chain(
            "common_more_actions",
            Role("button", "More"),
            CSS("button[aria-label*='More actions' i]"),
        ),
    },
}
