"""Flask web app for AI Email Tagger."""

from __future__ import annotations

import os
import secrets
from collections import Counter
from typing import Any

from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from auth.gmail_auth import (
    allow_local_http_oauth,
    get_authorization_url,
    get_gmail_service,
    has_token,
    save_callback_token,
)
from classifier.spacy_tagger import CATEGORIES, classify_email
from db.store import get_all_emails, get_emails_by_label, init_db, save_emails
from fetcher.gmail_fetcher import fetch_emails

DEFAULT_FETCH_COUNT = 50
MAX_FETCH_COUNT = 500
LOW_CONFIDENCE_LABEL = "Other"

load_dotenv()
allow_local_http_oauth()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)


@app.before_request
def ensure_database() -> None:
    """Ensure SQLite schema exists before handling each request."""
    init_db()


@app.get("/")
def index() -> str:
    """Render the home page."""
    authenticated = bool(session.get("authenticated")) or has_token()
    if authenticated:
        session["authenticated"] = True
    return render_template(
        "index.html",
        authenticated=authenticated,
        default_fetch_count=DEFAULT_FETCH_COUNT,
    )


@app.get("/auth")
def auth() -> Any:
    """Start Gmail OAuth2 flow."""
    try:
        redirect_uri = url_for("callback", _external=True)
        authorization_url, state = get_authorization_url(redirect_uri)
        session["oauth_state"] = state
        return redirect(authorization_url)
    except FileNotFoundError as exc:
        flash(str(exc), "error")
    except Exception:
        flash("Could not start Gmail connection. Please try again.", "error")
    return redirect(url_for("index"))


@app.get("/callback")
def callback() -> Any:
    """Handle Gmail OAuth2 callback."""
    expected_state = session.get("oauth_state")
    actual_state = request.args.get("state")

    if not expected_state or expected_state != actual_state:
        flash("Gmail connection failed because the OAuth session expired.", "error")
        return redirect(url_for("index"))

    try:
        redirect_uri = url_for("callback", _external=True)
        save_callback_token(
            redirect_uri=redirect_uri,
            authorization_response=request.url,
            state=expected_state,
        )
        session["authenticated"] = True
        session.pop("oauth_state", None)
        flash("Gmail connected successfully.", "success")
    except Exception:
        flash("Gmail authorization failed. Please try connecting again.", "error")

    return redirect(url_for("index"))


@app.post("/fetch")
def fetch() -> Any:
    """Fetch Gmail messages, classify them, and save them to SQLite."""
    if not (session.get("authenticated") or has_token()):
        flash("Please connect Gmail before fetching emails.", "error")
        return redirect(url_for("index"))

    try:
        requested_count = int(request.form.get("max_results", DEFAULT_FETCH_COUNT))
        max_results = max(1, min(requested_count, MAX_FETCH_COUNT))
    except ValueError:
        flash("Please enter a valid number of emails to fetch.", "error")
        return redirect(url_for("index"))

    try:
        service = get_gmail_service()
        fetched_emails = fetch_emails(service, max_results=max_results)
        classified_emails = [_classify_and_shape(email) for email in fetched_emails]
        saved_count = save_emails(classified_emails)
        session["authenticated"] = True
        flash(f"Fetched and classified {saved_count} email(s).", "success")
    except RuntimeError as exc:
        session["authenticated"] = False
        flash(str(exc), "error")
        return redirect(url_for("index"))
    except Exception:
        flash("Could not fetch emails from Gmail. Please try again.", "error")
        return redirect(url_for("index"))

    return redirect(url_for("emails"))


@app.get("/emails")
def emails() -> str:
    """Render classified emails as an HTML table."""
    selected_label = request.args.get("label", "All")
    try:
        all_emails = get_all_emails()
        displayed_emails = (
            all_emails
            if selected_label == "All"
            else get_emails_by_label(selected_label)
        )
        counts = Counter(email["label"] for email in all_emails)
    except Exception:
        flash("Could not load saved emails. Please try again.", "error")
        all_emails = []
        displayed_emails = []
        counts = Counter()

    return render_template(
        "emails.html",
        emails=displayed_emails,
        categories=CATEGORIES,
        selected_label=selected_label,
        total_count=len(all_emails),
        counts=counts,
    )


@app.get("/api/emails")
def api_emails() -> Any:
    """Return classified emails as JSON."""
    try:
        label = request.args.get("label")
        emails_data = get_emails_by_label(label) if label else get_all_emails()
        return jsonify(emails_data)
    except Exception:
        return jsonify({"error": "Could not load emails."}), 500


def _classify_and_shape(email: dict[str, Any]) -> dict[str, Any]:
    """Add spaCy classification fields to one fetched email."""
    result = classify_email(email.get("subject", ""), email.get("body_snippet", ""))
    confidence = float(result["confidence"])
    label = str(result["label"])
    if confidence < 0.6:
        label = LOW_CONFIDENCE_LABEL

    return {
        "id": email.get("id", ""),
        "subject": email.get("subject", ""),
        "sender": email.get("sender", ""),
        "date": email.get("date", ""),
        "body": email.get("body_snippet", ""),
        "label": label,
        "confidence": confidence,
    }


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
