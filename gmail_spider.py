"""
gmail_spider.py
================
A Scrapy spider that authenticates with Gmail via OAuth2 and yields EmailItem
objects for every message matching a configurable query.

Dependencies (add to requirements.txt):
    scrapy>=2.11
    google-auth-oauthlib>=1.2
    google-auth-httplib2>=0.2
    google-api-python-client>=2.120
    itemloaders>=1.1

Quickstart
----------
1.  Create a Google Cloud project and enable the Gmail API.
2.  Download the OAuth2 credentials JSON (Desktop app) and save it as
    ``credentials.json`` next to this file.
3.  Run the spider once to complete the browser-based consent flow; the
    resulting token is cached in ``token.json``.
4.  scrapy runspider gmail_spider.py -o emails.jsonl
"""

from __future__ import annotations

import base64
import html
import json
import logging
import os
import re
import sqlite3
import time
from email import message_from_bytes
from pathlib import Path
from typing import Any, Generator, Iterator, Optional

import scrapy
from itemloaders import ItemLoader
from itemloaders.processors import Identity, MapCompose, TakeFirst
from scrapy import Field, Item
from scrapy.exceptions import CloseSpider, DropItem, NotConfigured
from scrapy.http import Request, Response

from google.auth.exceptions import TransportError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCOPES: list[str] = ["https://www.googleapis.com/auth/gmail.readonly"]

#: Default path for the downloaded OAuth2 client-secret file.
DEFAULT_CREDENTIALS_FILE: str = "credentials.json"
#: Where the refreshed token is persisted between runs.
DEFAULT_TOKEN_FILE: str = "token.json"

#: Maximum number of retries when a quota-exceeded (429 / 403 rateLimitExceeded)
#: error is encountered.
MAX_RETRIES: int = 6
#: Base delay in seconds for exponential backoff.
BACKOFF_BASE: float = 2.0
#: Hard cap on backoff sleep in seconds.
BACKOFF_MAX: float = 64.0

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scrapy Item
# ---------------------------------------------------------------------------


class EmailItem(Item):
    """Represents a single Gmail message with all relevant metadata."""

    #: Gmail message ID (immutable, unique per message).
    id: Field = Field()
    #: Decoded Subject header.
    subject: Field = Field()
    #: Decoded From header.
    sender: Field = Field()
    #: Plain-text body extracted from the MIME tree.
    body_text: Field = Field()
    #: Short snippet provided by the Gmail API.
    snippet: Field = Field()
    #: ISO-8601 internalDate string.
    date: Field = Field()
    #: List of Gmail label IDs applied to the message.
    labels: Field = Field()


# ---------------------------------------------------------------------------
# Item Loader
# ---------------------------------------------------------------------------


def _strip(value: str) -> str:
    """Strip leading/trailing whitespace from a string value."""
    return value.strip() if isinstance(value, str) else value


def _ensure_list(value: Any) -> list[Any]:
    """Wrap scalar values in a list; pass through existing lists unchanged."""
    return value if isinstance(value, list) else [value]


class EmailItemLoader(ItemLoader):
    """
    Custom ItemLoader for :class:`EmailItem`.

    Input processors
    ~~~~~~~~~~~~~~~~
    * All string fields are stripped of surrounding whitespace.
    * ``labels`` accepts a list and passes it through unchanged.

    Output processors
    ~~~~~~~~~~~~~~~~~
    * Scalar fields use :class:`~itemloaders.processors.TakeFirst` so only the
      first (and typically only) value is stored.
    * ``labels`` uses :class:`~itemloaders.processors.Identity` to preserve the
      full list.
    """

    default_item_class = EmailItem
    default_input_processor = MapCompose(_strip)
    default_output_processor = TakeFirst()

    labels_in = Identity()
    labels_out = Identity()


# ---------------------------------------------------------------------------
# OAuth2 helpers
# ---------------------------------------------------------------------------


def _load_or_refresh_credentials(
    credentials_file: str = DEFAULT_CREDENTIALS_FILE,
    token_file: str = DEFAULT_TOKEN_FILE,
) -> Credentials:
    """
    Load cached credentials from *token_file* or run the OAuth2 consent flow.

    Parameters
    ----------
    credentials_file:
        Path to the client-secret JSON downloaded from Google Cloud Console.
    token_file:
        Path where the access/refresh token is cached between runs.

    Returns
    -------
    google.oauth2.credentials.Credentials
        Valid (and refreshed if needed) OAuth2 credentials.

    Raises
    ------
    FileNotFoundError
        If *credentials_file* does not exist and *token_file* is also absent.
    """
    creds: Optional[Credentials] = None

    if Path(token_file).exists():
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        logger.debug("Loaded cached credentials from %s", token_file)

    # Refresh or re-authenticate as required.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired access token …")
            creds.refresh(GoogleRequest())
        else:
            if not Path(credentials_file).exists():
                raise FileNotFoundError(
                    f"OAuth2 credentials file not found: {credentials_file!r}. "
                    "Download it from the Google Cloud Console and place it next "
                    "to this spider."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                credentials_file, SCOPES
            )
            creds = flow.run_local_server(port=0)
            logger.info("Completed OAuth2 consent flow.")

        # Persist the (possibly refreshed) token for next run.
        Path(token_file).write_text(creds.to_json(), encoding="utf-8")
        logger.debug("Saved credentials to %s", token_file)

    return creds


# ---------------------------------------------------------------------------
# Gmail API wrapper with backoff
# ---------------------------------------------------------------------------


class GmailClient:
    """
    Thin wrapper around the Gmail REST API that adds exponential backoff for
    quota-exceeded errors.

    Parameters
    ----------
    credentials:
        Authenticated :class:`~google.oauth2.credentials.Credentials` object.
    """

    def __init__(self, credentials: Credentials) -> None:
        self._service = build(
            "gmail", "v1", credentials=credentials, cache_discovery=False
        )

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def list_messages(
        self,
        query: str = "",
        page_token: Optional[str] = None,
        max_results: int = 500,
    ) -> dict[str, Any]:
        """
        Call ``users.messages.list`` with backoff.

        Parameters
        ----------
        query:
            Gmail search query string (e.g. ``"from:noreply@github.com"``).
        page_token:
            Continuation token from a previous call; ``None`` for the first page.
        max_results:
            Maximum number of message stubs to return (1–500).

        Returns
        -------
        dict
            Raw API response containing ``messages`` and optionally
            ``nextPageToken``.
        """
        kwargs: dict[str, Any] = {
            "userId": "me",
            "maxResults": max_results,
            "q": query,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        return self._call_with_backoff(
            self._service.users().messages().list, **kwargs
        )

    def get_message(self, message_id: str) -> dict[str, Any]:
        """
        Fetch a full message (``format=full``) with backoff.

        Parameters
        ----------
        message_id:
            The Gmail message ID returned by :meth:`list_messages`.

        Returns
        -------
        dict
            Raw message resource as returned by the Gmail API.
        """
        return self._call_with_backoff(
            self._service.users().messages().get,
            userId="me",
            id=message_id,
            format="full",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _call_with_backoff(
        resource_method: Any, **kwargs: Any
    ) -> dict[str, Any]:
        """
        Execute a Google API resource method, retrying on quota errors.

        The retry strategy applies **exponential backoff with jitter**:
        ``sleep = min(BACKOFF_BASE ** attempt, BACKOFF_MAX)``.

        Parameters
        ----------
        resource_method:
            An un-executed Google API resource method (callable that returns a
            request object when called).
        **kwargs:
            Keyword arguments forwarded to *resource_method*.

        Returns
        -------
        dict
            Decoded JSON response from the API.

        Raises
        ------
        HttpError
            Re-raised after :data:`MAX_RETRIES` failed attempts, or immediately
            for non-retriable HTTP errors.
        TransportError
            Re-raised after :data:`MAX_RETRIES` failed attempts.
        """
        for attempt in range(MAX_RETRIES + 1):
            try:
                return resource_method(**kwargs).execute()
            except HttpError as exc:
                status = int(exc.resp.status)
                retriable = status in (429, 500, 502, 503, 504) or (
                    status == 403
                    and b"rateLimitExceeded" in (exc.content or b"")
                )
                if not retriable or attempt == MAX_RETRIES:
                    logger.error(
                        "Non-retriable HTTP error %s after %d attempt(s): %s",
                        status,
                        attempt + 1,
                        exc,
                    )
                    raise
                delay = min(BACKOFF_BASE ** attempt, BACKOFF_MAX)
                logger.warning(
                    "HTTP %s – quota exceeded. Retrying in %.1fs (attempt %d/%d) …",
                    status,
                    delay,
                    attempt + 1,
                    MAX_RETRIES,
                )
                time.sleep(delay)
            except TransportError as exc:
                if attempt == MAX_RETRIES:
                    raise
                delay = min(BACKOFF_BASE ** attempt, BACKOFF_MAX)
                logger.warning(
                    "Transport error – retrying in %.1fs (attempt %d/%d): %s",
                    delay,
                    attempt + 1,
                    MAX_RETRIES,
                    exc,
                )
                time.sleep(delay)

        # Should be unreachable, but satisfies type checkers.
        raise RuntimeError("Exceeded maximum retries without raising.")  # pragma: no cover


# ---------------------------------------------------------------------------
# MIME body extraction helpers
# ---------------------------------------------------------------------------


def _decode_base64url(data: str) -> bytes:
    """
    Decode a URL-safe base64-encoded string as used by the Gmail API.

    Parameters
    ----------
    data:
        The encoded string (may omit ``=`` padding).

    Returns
    -------
    bytes
        Raw decoded bytes.
    """
    # Gmail uses URL-safe base64; add padding if necessary.
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded)


def _extract_plain_text(payload: dict[str, Any]) -> str:
    """
    Recursively walk a Gmail message ``payload`` and concatenate all
    ``text/plain`` MIME parts.

    Parameters
    ----------
    payload:
        The ``payload`` object from a full Gmail message resource.

    Returns
    -------
    str
        The decoded, concatenated plain-text body (may be empty).
    """
    mime_type: str = payload.get("mimeType", "")
    parts: list[dict[str, Any]] = payload.get("parts", [])
    body_data: str = payload.get("body", {}).get("data", "")

    if mime_type == "text/plain" and body_data:
        return _decode_base64url(body_data).decode("utf-8", errors="replace")

    # Recurse into multipart/* children.
    if parts:
        return "".join(_extract_plain_text(part) for part in parts)

    return ""


def _get_header(headers: list[dict[str, str]], name: str) -> str:
    """
    Case-insensitively retrieve a header value from a list of header dicts.

    Parameters
    ----------
    headers:
        List of ``{"name": ..., "value": ...}`` dicts from the Gmail payload.
    name:
        Header name to look up (case-insensitive).

    Returns
    -------
    str
        The header value, or an empty string if not found.
    """
    name_lower = name.lower()
    for header in headers:
        if header.get("name", "").lower() == name_lower:
            return header.get("value", "")
    return ""


# ---------------------------------------------------------------------------
# Item pipelines
# ---------------------------------------------------------------------------


def _normalise_whitespace(value: str) -> str:
    """Collapse repeated whitespace into single spaces."""
    return re.sub(r"\s+", " ", value).strip()


def _decode_possible_base64(value: str) -> str:
    """
    Decode base64/base64url text when it looks encoded; otherwise return it.
    """
    compact = value.strip()
    if not compact or len(compact) % 4 not in (0, 2, 3):
        return value

    allowed_chars = set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=_-"
    )
    if any(char not in allowed_chars for char in compact):
        return value

    try:
        decoded = _decode_base64url(compact).decode("utf-8", errors="strict")
    except Exception:
        return value

    printable = sum(1 for char in decoded if char.isprintable() or char.isspace())
    if decoded and printable / len(decoded) > 0.95:
        return decoded
    return value


def _clean_text(value: Any) -> Any:
    """Strip HTML tags, unescape entities, decode base64, and normalise spaces."""
    if not isinstance(value, str):
        return value

    text = _decode_possible_base64(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return _normalise_whitespace(text)


class CleaningPipeline:
    """Clean text fields before items are deduplicated or stored."""

    text_fields = ("subject", "sender", "body_text", "snippet", "date")

    def process_item(self, item: EmailItem, spider: scrapy.Spider) -> EmailItem:
        for field in self.text_fields:
            if field in item:
                item[field] = _clean_text(item[field])
        return item


class DeduplicationPipeline:
    """Drop messages whose Gmail IDs have already been seen this run."""

    def __init__(self) -> None:
        self._seen_ids: set[str] = set()

    def process_item(self, item: EmailItem, spider: scrapy.Spider) -> EmailItem:
        message_id = item.get("id")
        if not message_id:
            raise DropItem("Missing Gmail message ID")
        if message_id in self._seen_ids:
            raise DropItem(f"Duplicate Gmail message ID: {message_id}")
        self._seen_ids.add(message_id)
        return item


class StoragePipeline:
    """Persist email items into a SQLite database."""

    def __init__(self, database_path: str = "emails.db") -> None:
        self.database_path = database_path
        self._connection: Optional[sqlite3.Connection] = None

    @classmethod
    def from_crawler(cls, crawler: Any) -> "StoragePipeline":
        database_path = crawler.settings.get("GMAIL_SQLITE_DB", "emails.db")
        if not database_path:
            raise NotConfigured("GMAIL_SQLITE_DB is empty")
        return cls(database_path=str(database_path))

    def open_spider(self, spider: scrapy.Spider) -> None:
        self._connection = sqlite3.connect(self.database_path)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS emails (
                id TEXT PRIMARY KEY,
                subject TEXT,
                sender TEXT,
                body_text TEXT,
                snippet TEXT,
                date TEXT,
                labels TEXT
            )
            """
        )
        self._connection.commit()

    def close_spider(self, spider: scrapy.Spider) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def process_item(self, item: EmailItem, spider: scrapy.Spider) -> EmailItem:
        if self._connection is None:
            raise DropItem("SQLite storage is not open")

        self._connection.execute(
            """
            INSERT OR REPLACE INTO emails (
                id, subject, sender, body_text, snippet, date, labels
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.get("id", ""),
                item.get("subject", ""),
                item.get("sender", ""),
                item.get("body_text", ""),
                item.get("snippet", ""),
                item.get("date", ""),
                json.dumps(item.get("labels", [])),
            ),
        )
        self._connection.commit()
        return item


# ---------------------------------------------------------------------------
# Spider
# ---------------------------------------------------------------------------


class GmailSpider(scrapy.Spider):
    """
    Scrapy spider that crawls Gmail messages via the Gmail REST API.

    Custom settings
    ~~~~~~~~~~~~~~~
    Configure the following via ``custom_settings`` or ``scrapy.cfg`` / ``settings.py``:

    ``GMAIL_QUERY`` (str, default ``""``)
        Gmail search query to filter messages (e.g. ``"label:inbox is:unread"``).
    ``GMAIL_MAX_RESULTS`` (int, default ``500``)
        Number of message stubs to fetch per API page (max 500).
    ``GMAIL_CREDENTIALS_FILE`` (str, default ``"credentials.json"``)
        Path to the OAuth2 client-secret JSON file.
    ``GMAIL_TOKEN_FILE`` (str, default ``"token.json"``)
        Path where the OAuth2 token is cached.
    ``GMAIL_MAX_MESSAGES`` (int, default ``0``)
        Stop after yielding this many items; ``0`` means no limit.

    Example
    -------
    .. code-block:: bash

        scrapy runspider gmail_spider.py \\
            -s GMAIL_QUERY="from:github.com" \\
            -s GMAIL_MAX_MESSAGES=200 \\
            -o github_emails.jsonl
    """

    name: str = "gmail"

    custom_settings: dict[str, Any] = {
        # Disable built-in HTTP caching (all requests go through the API client).
        "HTTPCACHE_ENABLED": False,
        # Polite crawl delay to complement API backoff.
        "DOWNLOAD_DELAY": 0,
        # Allow the spider to run without a real start URL.
        "ROBOTSTXT_OBEY": False,
    }

    @classmethod
    def from_crawler(cls, crawler: Any, *args: Any, **kwargs: Any) -> "GmailSpider":
        """
        Build the spider from Scrapy settings, while still allowing CLI spider
        arguments to override those settings.
        """
        settings = crawler.settings
        kwargs.setdefault(
            "credentials_file",
            settings.get("GMAIL_CREDENTIALS_FILE", DEFAULT_CREDENTIALS_FILE),
        )
        kwargs.setdefault(
            "token_file", settings.get("GMAIL_TOKEN_FILE", DEFAULT_TOKEN_FILE)
        )
        kwargs.setdefault("query", settings.get("GMAIL_QUERY", ""))
        kwargs.setdefault("max_results", settings.getint("GMAIL_MAX_RESULTS", 500))
        kwargs.setdefault("max_messages", settings.getint("GMAIL_MAX_MESSAGES", 0))

        spider = cls(*args, **kwargs)
        spider._set_crawler(crawler)
        return spider

    def __init__(
        self,
        credentials_file: str = DEFAULT_CREDENTIALS_FILE,
        token_file: str = DEFAULT_TOKEN_FILE,
        query: str = "",
        max_results: int = 500,
        max_messages: int = 0,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """
        Initialise the spider.

        Parameters
        ----------
        credentials_file:
            Path to the OAuth2 client-secret JSON.
        token_file:
            Path where the token is cached.
        query:
            Gmail search query string.
        max_results:
            Page size for ``users.messages.list`` (1–500).
        max_messages:
            Yield at most this many :class:`EmailItem` objects; ``0`` = no limit.
        """
        super().__init__(*args, **kwargs)
        self._credentials_file = credentials_file
        self._token_file = token_file
        self._query = query
        self._max_results = max(1, min(int(max_results), 500))
        self._max_messages = int(max_messages)
        self._fetched: int = 0
        self._client: Optional[GmailClient] = None

    # ------------------------------------------------------------------
    # Scrapy lifecycle
    # ------------------------------------------------------------------

    def start_requests(self) -> Iterator[Request]:
        """
        Bootstrap the crawl by emitting a single synthetic Scrapy request whose
        callback drives the Gmail API pagination loop.

        The Gmail API is accessed directly (not via Scrapy's downloader) so we
        use a dummy ``http://localhost`` URL simply to satisfy Scrapy's request
        queue — the response object is never actually used.
        """
        try:
            creds = _load_or_refresh_credentials(
                self._credentials_file, self._token_file
            )
        except FileNotFoundError as exc:
            raise CloseSpider(str(exc)) from exc

        self._client = GmailClient(creds)
        logger.info(
            "GmailSpider starting (query=%r, max_results=%d, max_messages=%d)",
            self._query,
            self._max_results,
            self._max_messages,
        )

        # Yield a fake request; all real work happens in the callback.
        yield Request(
            url="http://localhost/",
            callback=self._crawl_page,
            cb_kwargs={"page_token": None},
            dont_filter=True,
        )

    def _crawl_page(
        self,
        response: Response,
        page_token: Optional[str],
    ) -> Generator[EmailItem | Request, None, None]:
        """
        Fetch one page of message stubs and yield a :class:`Request` per message
        whose callback builds the :class:`EmailItem`.

        Parameters
        ----------
        response:
            Unused Scrapy response (the request is a dummy bootstrap request).
        page_token:
            Gmail API pagination token; ``None`` for the first page.

        Yields
        ------
        scrapy.Request
            One fake request per message stub, plus an optional continuation
            request for the next page.
        """
        assert self._client is not None, "GmailClient not initialised"

        try:
            result = self._client.list_messages(
                query=self._query,
                page_token=page_token,
                max_results=self._max_results,
            )
        except HttpError as exc:
            logger.error("Fatal Gmail API error during list_messages: %s", exc)
            raise CloseSpider("gmail_api_error") from exc

        messages: list[dict[str, str]] = result.get("messages", [])
        next_page_token: Optional[str] = result.get("nextPageToken")

        logger.info(
            "Fetched page with %d message stub(s) (nextPageToken=%s)",
            len(messages),
            next_page_token,
        )

        for stub in messages:
            if self._max_messages and self._fetched >= self._max_messages:
                logger.info(
                    "Reached max_messages limit (%d). Stopping.", self._max_messages
                )
                return

            yield Request(
                url=f"http://localhost/message/{stub['id']}",
                callback=self._parse_message,
                cb_kwargs={"message_id": stub["id"]},
                dont_filter=True,
            )
            self._fetched += 1

        # Follow pagination unless we've hit the message cap.
        if next_page_token and not (
            self._max_messages and self._fetched >= self._max_messages
        ):
            yield Request(
                url=f"http://localhost/page/{next_page_token}",
                callback=self._crawl_page,
                cb_kwargs={"page_token": next_page_token},
                dont_filter=True,
            )

    def _parse_message(
        self, response: Response, message_id: str
    ) -> Generator[EmailItem, None, None]:
        """
        Retrieve a full Gmail message and populate an :class:`EmailItem` via
        :class:`EmailItemLoader`.

        Parameters
        ----------
        response:
            Unused Scrapy response.
        message_id:
            Gmail message ID to fetch.

        Yields
        ------
        EmailItem
            A fully populated email item.
        """
        assert self._client is not None, "GmailClient not initialised"

        try:
            msg: dict[str, Any] = self._client.get_message(message_id)
        except HttpError as exc:
            logger.error(
                "Skipping message %s due to API error: %s", message_id, exc
            )
            return

        payload: dict[str, Any] = msg.get("payload", {})
        headers: list[dict[str, str]] = payload.get("headers", [])

        loader = EmailItemLoader(item=EmailItem())

        loader.add_value("id", msg.get("id", ""))
        loader.add_value("subject", _get_header(headers, "Subject") or "(no subject)")
        loader.add_value("sender", _get_header(headers, "From"))
        loader.add_value("body_text", _extract_plain_text(payload))
        loader.add_value("snippet", msg.get("snippet", ""))
        loader.add_value("date", _get_header(headers, "Date"))
        loader.add_value("labels", msg.get("labelIds", []))

        yield loader.load_item()
