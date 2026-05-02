"""Fetch Gmail inbox emails with the official Gmail API."""

from __future__ import annotations

import base64
from html.parser import HTMLParser
from typing import Any

from googleapiclient.discovery import Resource

GMAIL_USER_ID = "me"
INBOX_LABEL = "INBOX"
MESSAGE_FORMAT_FULL = "full"
BODY_SNIPPET_CHARS = 300
BLOCK_TAGS = {"br", "p", "div", "li", "tr", "section", "article"}


class _HTMLTextExtractor(HTMLParser):
    """Convert HTML email fragments into readable text."""

    def __init__(self) -> None:
        """Initialize an empty text buffer."""
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Add spacing around common block tags."""
        if tag.lower() in BLOCK_TAGS:
            self._chunks.append(" ")

    def handle_data(self, data: str) -> None:
        """Collect text content."""
        if data:
            self._chunks.append(data)

    def text(self) -> str:
        """Return normalized text."""
        return " ".join(" ".join(self._chunks).split())


def fetch_emails(service: Resource, max_results: int = 50) -> list[dict[str, Any]]:
    """Fetch recent Gmail INBOX emails.

    Args:
        service: Authorized Gmail API service.
        max_results: Maximum number of inbox messages to fetch.

    Returns:
        List of dictionaries containing id, subject, sender, date, and
        body_snippet.
    """
    if max_results <= 0:
        return []

    message_ids = _list_inbox_message_ids(service, max_results=max_results)
    return [
        _message_to_email(message)
        for message in _fetch_full_messages(service, message_ids)
    ]


def _list_inbox_message_ids(service: Resource, max_results: int) -> list[str]:
    """List Gmail message IDs from INBOX only."""
    message_ids: list[str] = []
    page_token: str | None = None

    while len(message_ids) < max_results:
        remaining = max_results - len(message_ids)
        response = (
            service.users()
            .messages()
            .list(
                userId=GMAIL_USER_ID,
                labelIds=[INBOX_LABEL],
                maxResults=min(remaining, 500),
                pageToken=page_token,
            )
            .execute()
        )

        for message in response.get("messages", []) or []:
            message_id = message.get("id")
            if message_id:
                message_ids.append(message_id)

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return message_ids[:max_results]


def _fetch_full_messages(
    service: Resource,
    message_ids: list[str],
) -> list[dict[str, Any]]:
    """Fetch full Gmail messages with a batch request."""
    if not message_ids:
        return []

    messages_by_id: dict[str, dict[str, Any]] = {}
    batch = service.new_batch_http_request()

    def callback(
        request_id: str,
        response: dict[str, Any] | None,
        exception: Exception | None,
    ) -> None:
        """Collect one batch response."""
        if exception is None and response is not None:
            messages_by_id[request_id] = response

    for message_id in message_ids:
        batch.add(
            service.users().messages().get(
                userId=GMAIL_USER_ID,
                id=message_id,
                format=MESSAGE_FORMAT_FULL,
            ),
            request_id=message_id,
            callback=callback,
        )

    batch.execute()
    return [
        messages_by_id[message_id]
        for message_id in message_ids
        if message_id in messages_by_id
    ]


def _message_to_email(message: dict[str, Any]) -> dict[str, Any]:
    """Convert a Gmail API message into app email fields."""
    payload = message.get("payload", {})
    headers = payload.get("headers", []) or []
    body = _extract_body(payload)
    body_snippet = " ".join(body.split())[:BODY_SNIPPET_CHARS]

    return {
        "id": message.get("id", ""),
        "subject": _get_header(headers, "Subject"),
        "sender": _get_header(headers, "From"),
        "date": _get_header(headers, "Date"),
        "body_snippet": body_snippet or message.get("snippet", "")[:BODY_SNIPPET_CHARS],
    }


def _get_header(headers: list[dict[str, str]], name: str) -> str:
    """Return a header value by case-insensitive header name."""
    expected = name.lower()
    for header in headers:
        if header.get("name", "").lower() == expected:
            return header.get("value", "")
    return ""


def _extract_body(payload: dict[str, Any]) -> str:
    """Extract plain text from Gmail payload, falling back to stripped HTML."""
    plain_parts: list[str] = []
    html_parts: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        """Traverse one MIME part."""
        mime_type = part.get("mimeType", "")
        data = part.get("body", {}).get("data")

        if data and mime_type == "text/plain":
            plain_parts.append(_decode_body(data))
        elif data and mime_type == "text/html":
            html_parts.append(_html_to_text(_decode_body(data)))

        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    if plain_parts:
        return "\n".join(part.strip() for part in plain_parts if part.strip())
    return "\n".join(part.strip() for part in html_parts if part.strip())


def _decode_body(data: str) -> str:
    """Decode Gmail base64url body data."""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode(
        "utf-8",
        errors="replace",
    )


def _html_to_text(value: str) -> str:
    """Strip HTML tags and normalize text."""
    parser = _HTMLTextExtractor()
    parser.feed(value)
    parser.close()
    return parser.text()
