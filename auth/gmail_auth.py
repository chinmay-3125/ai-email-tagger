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


# The one canonical redirect URI used everywhere in the OAuth flow.
# Value: "http://localhost:5000/callback" — must match Google Cloud Console exactly.
_REDIRECT_URI = "http://localhost:5000/callback"


def create_flow(state: str | None = None) -> Flow:
    """Create a Google OAuth flow for the Flask app.

    The redirect_uri is hardcoded to ``_REDIRECT_URI`` so every part of the
    flow always uses the same literal string — no caller can accidentally
    pass a different value.

    Args:
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

    # redirect_uri is passed directly into from_client_secrets_file so it is
    # baked into the flow object before authorization_url() is ever called.
    # Value: "http://localhost:5000/callback"
    flow = Flow.from_client_secrets_file(
        str(CREDENTIALS_PATH),
        scopes=SCOPES,
        state=state,
        redirect_uri=_REDIRECT_URI,
    )
    # --- DEBUG ---
    print(f"[FLOW] flow.redirect_uri set to: {flow.redirect_uri}")
    return flow


def get_authorization_url() -> tuple[str, str, str | None]:
    """Create a Gmail OAuth authorization URL.

    Returns:
        Tuple of (authorization URL, OAuth state, PKCE code_verifier).
        code_verifier may be None if the library did not generate one.
    """
    # redirect_uri is now owned entirely by create_flow() — no argument needed.
    flow = create_flow()
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    # Capture the PKCE code_verifier the library generated (if any).
    # It must be passed back to fetch_token() or Google rejects with
    # "invalid_grant: Missing code verifier".
    code_verifier = flow.code_verifier
    return authorization_url, state, code_verifier


def save_callback_token(
    authorization_response: str,
    state: str,
    code_verifier: str | None = None,
) -> Credentials:
    """Exchange OAuth callback data for credentials and cache token.json.

    Args:
        authorization_response: Full callback URL received from Google.
        state: OAuth state stored in Flask session.
        code_verifier: PKCE verifier generated during authorization. Must be
            passed when the authorization URL included a code_challenge.

    Returns:
        Authorized Gmail OAuth credentials.
    """
    # redirect_uri is now owned entirely by create_flow() — no argument needed.
    flow = create_flow(state=state)
    # --- DEBUG ---
    print(f"[TOKEN] fetching token with redirect_uri={flow.redirect_uri}")
    print(f"[TOKEN] authorization_response={authorization_response}")
    print(f"[TOKEN] code_verifier={'<present>' if code_verifier else '<None>'}")
    flow.fetch_token(
        authorization_response=authorization_response,
        code_verifier=code_verifier,
    )
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
