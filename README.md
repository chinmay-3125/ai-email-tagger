# AI Email Tagger

AI Email Tagger is a Python project for collecting Gmail messages with Scrapy and preparing them for automated classification.

## What Is Included

- `gmail_spider.py`: Scrapy spider that authenticates with Gmail, paginates messages, extracts email fields, cleans items, deduplicates messages, and can store results in SQLite.
- `.devcontainer/devcontainer.json`: GitHub Codespaces setup using Python 3.11 and JupyterLab.
- `requirements.txt`: Python dependencies for scraping, notebooks, Reddit API experiments, and Anthropic API integration.
- `.env.example`: Template for local API credentials.

## Setup

1. Create a Google Cloud project and enable the Gmail API.
2. Download OAuth credentials for a desktop app.
3. Save the file as `credentials.json` locally, or provide it through the `GMAIL_CREDENTIALS` Codespaces secret.
4. Install dependencies:

```bash
pip install -r requirements.txt
```

5. Run the Gmail spider:

```bash
scrapy runspider gmail_spider.py -o emails.jsonl
```

## Configuration

The spider supports these Scrapy settings:

- `GMAIL_QUERY`: Gmail search query, for example `label:inbox is:unread`.
- `GMAIL_MAX_RESULTS`: API page size, from 1 to 500.
- `GMAIL_CREDENTIALS_FILE`: OAuth client credentials path.
- `GMAIL_TOKEN_FILE`: Cached OAuth token path.
- `GMAIL_MAX_MESSAGES`: Stop after this many messages; `0` means no limit.
- `GMAIL_SQLITE_DB`: SQLite database path for `StoragePipeline`.

## Codespaces

The devcontainer exposes port `8888` for JupyterLab and installs all dependencies from `requirements.txt`.

Create a Codespaces secret named `GMAIL_CREDENTIALS` for Gmail OAuth credentials. Keep `credentials.json`, `token.json`, `.env`, and `emails.db` out of Git.

## Security

Never commit API keys, OAuth tokens, personal email exports, or SQLite databases containing email content. Use `.env.example` as a template only.
