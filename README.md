# deliberatenews

Generates a weekly world news digest from Kagi, deduplicated and grouped into topic clusters using Claude AI. Run it once to get a clean, readable HTML page you can open in any browser.

## Requirements

- Python 3.9+
- An [Anthropic API key](https://console.anthropic.com/)

## Setup

```bash
git clone https://github.com/your-username/deliberatenews.git
cd deliberatenews
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and add your Anthropic API key
```

## Usage

Activate the virtual environment, then run the script:

```bash
source .venv/bin/activate
python parse_feed.py
```

This fetches the last 7 days of world news from Kagi, groups headlines into topic clusters, and writes the digest to `docs/index.html`. Open it in any browser.

## GitHub Pages setup

1. Push the repo to GitHub
2. Go to **Settings → Pages**
3. Set source to `main` branch, `/docs` folder
4. Your digest will be live at `https://<username>.github.io/<repo>/`

Each run also saves a dated copy (e.g. `docs/2026-03-21.html`) so past issues remain accessible at their own URLs.

## Attribution

News data sourced from the [Kagi News API](https://news.kagi.com/api-docs).
