"""Gmail OAuth2 helpers for the Flask AI Email Tagger app."""

from __future__ import annotations

import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import Resource, build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_PATH = Path("credentials.json")
TOKEN_PATH = Path("token.json")
GMAIL_SERVICE_NAME = "gmail"
GMAIL_SERVICE_VERSION = "v1"


def allow_local_http_oauth() -> None:
    """Allow OAuth callback redirects over localhost HTTP for development."""
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")


def has_token() -> bool:
    """Return True when a cached OAuth token exists."""
    return TOKEN_PATH.exists()


def load_credentials() -> Credentials | None:
    """Load cached credentials and refresh them when possible.

    Returns:
        Valid Gmail OAuth credentials, or None when the user must connect Gmail.
    """
    if not TOKEN_PATH.exists():
        return None

    credentials = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if credentials.valid:
        return credentials

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        TOKEN_PATH.write_text(credentials.to_json(), encoding="utf-8")
        return credentials

    return None


def create_flow(redirect_uri: str, state: str | None = None) -> Flow:
    """Create a Google OAuth flow for the Flask app.

    Args:
        redirect_uri: Absolute callback URL for the current Flask request.
        state: Optional OAuth state value from the user's session.

    Returns:
        Configured OAuth flow.

    Raises:
        FileNotFoundError: If root-level credentials.json is missing.
    """
    if not CREDENTIALS_PATH.exists():
        raise FileNotFoundError(
            "credentials.json was not found in the project root. "
            "Download OAuth client credentials from Google Cloud Console first."
        )

    flow = Flow.from_client_secrets_file(
        str(CREDENTIALS_PATH),
        scopes=SCOPES,
        state=state,
    )
    flow.redirect_uri = redirect_uri
    return flow


def get_authorization_url(redirect_uri: str) -> tuple[str, str]:
    """Create a Gmail OAuth authorization URL.

    Args:
        redirect_uri: Absolute callback URL for the current Flask request.

    Returns:
        Tuple of authorization URL and OAuth state.
    """
    flow = create_flow(redirect_uri=redirect_uri)
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return authorization_url, state


def save_callback_token(
    redirect_uri: str,
    authorization_response: str,
    state: str,
) -> Credentials:
    """Exchange OAuth callback data for credentials and cache token.json.

    Args:
        redirect_uri: Absolute callback URL for the current Flask request.
        authorization_response: Full callback URL received from Google.
        state: OAuth state stored in Flask session.

    Returns:
        Authorized Gmail OAuth credentials.
    """
    flow = create_flow(redirect_uri=redirect_uri, state=state)
    flow.fetch_token(authorization_response=authorization_response)
    credentials = flow.credentials
    TOKEN_PATH.write_text(credentials.to_json(), encoding="utf-8")
    return credentials


def get_gmail_service() -> Resource:
    """Return an authorized Gmail API service.

    Returns:
        Gmail API service object.

    Raises:
        RuntimeError: If Gmail has not been connected yet.
    """
    credentials = load_credentials()
    if credentials is None:
        raise RuntimeError("Gmail is not connected. Please connect Gmail first.")

    return build(
        GMAIL_SERVICE_NAME,
        GMAIL_SERVICE_VERSION,
        credentials=credentials,
        cache_discovery=False,
    )
