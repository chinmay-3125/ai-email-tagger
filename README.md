# AI Email Tagger

A pure Flask web app that connects to Gmail, fetches inbox emails, classifies them locally with spaCy NLP, stores results in SQLite, and displays them in a clean browser UI.

No Scrapy, PRAW, Jupyter, Reddit, React, or JavaScript frameworks are used.

## Step 1: Google OAuth Setup

1. Create a Google Cloud project.
2. Enable the Gmail API.
3. Configure the OAuth consent screen.
4. Create OAuth client credentials.
5. Choose a Web application client type.
6. Add `http://localhost:5000/callback` as an authorized redirect URI.
7. Download the JSON file.
8. Save it in this project root as `credentials.json`.

Do not commit `credentials.json`, `token.json`, or `emails.db`.

## Step 2: Install

```bash
bash setup.sh
```

This installs Python dependencies and downloads spaCy `en_core_web_md`.

## Step 3: Run

```bash
python app.py
```

## Step 4: Connect Gmail

Open [http://localhost:5000](http://localhost:5000) and click **Connect Gmail**. After Google OAuth succeeds, `token.json` is created automatically.

## Step 5: Fetch And Classify

Enter how many recent inbox emails to fetch, then click **Fetch & Classify Emails**. Classification runs locally with spaCy and works offline after the Gmail emails have been fetched.

## Categories

Work, Finance, Newsletters, Social, Promotions, Travel, Health, Shopping, Family, Other.
