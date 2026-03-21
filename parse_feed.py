import glob
import json
import os
import re
import sys
import requests
import anthropic
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()
from htpy import body, h1, h2, head, html, li, meta, title, ul, a, span, style, p

url_kagi = "https://kite.kagi.com/"

url_batch_list = url_kagi+"api/batches/"
url_categories = "/categories/"
url_stories = "/stories/"

date_from = datetime.today() - timedelta(days=7)
url_date_from = "?from="+str(date_from)

SYSTEM_PROMPT = """You are a news editor organizing a week of world news headlines into topic clusters.
Your task:
1. Deduplicate ruthlessly. Drop ALL "Update:" headlines — they are always duplicates. For regular headlines, if two cover the same event (same strike, same vote, same election, same leader), keep only the single best one and drop the rest. When in doubt, drop.
2. Group surviving unique headlines into named topic clusters.
3. Give each cluster a concise, neutral label (3-7 words).
Respond ONLY with JSON. No markdown fences, no explanation."""

USER_PROMPT_TEMPLATE = """Here are the headlines to process:

{headlines}

Return a JSON object with this exact schema:
{{"clusters": [{{"label": "Topic Label", "story_ids": [0, 1, 3, ...]}}]}}

Rules:
- Each story_id appears in exactly ONE cluster. Story IDs not in any cluster are silently dropped.
- Drop ALL "Update:" headlines, no exceptions.
- Hard limit: at most 2 stories per regular cluster. Drop the weaker ones if over.
- "Miscellaneous events" is the final cluster. It holds at most 4 truly standalone stories that share no theme with any named cluster. If a story fits a named cluster, it goes there — not in Miscellaneous. If it fits nowhere and you already have 4 misc stories, drop it.
- Aim for 8-12 clusters total (including "Miscellaneous events").
- Order clusters by significance (most important first), with "Miscellaneous events" always last.
- Within each cluster, order story_ids chronologically (lowest ID first is fine)."""

REORDER_PROMPT = """You are given a numbered list of news topic cluster labels.
Reorder them so that thematically related topics are adjacent (e.g. all Middle East topics together, all US politics together, all economics together). "Miscellaneous events" must always be last.
Respond ONLY with a JSON array of the original indices in the new order, e.g. [2, 0, 4, 1, 3]. No explanation."""


def getCategoryId(categories, selected_category):
    for category in categories:
        if category['categoryId'] == selected_category:
            return category['id']

    raise Exception("categoryId "+selected_category+" not found")

def getCategories(batch):
    response = requests.get(url_batch_list+batch['id']+url_categories)
    categories = json.loads(response.text)['categories']

    return categories

def getStories(batch, categories, selected_category):
    categ_id = getCategoryId(categories, selected_category)

    response = requests.get(url_batch_list+batch['id']+url_categories+categ_id+url_stories)
    stories = json.loads(response.text)

    return stories

def getBatches():
    response = requests.get(url_batch_list+url_date_from)
    batches = json.loads(response.text)['batches']

    return batches


def cluster_stories_with_ai(all_stories):
    headlines = "\n".join(
        f"{s['id']}. {s['title']}" for s in all_stories
    )
    user_prompt = USER_PROMPT_TEMPLATE.format(headlines=headlines)

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    raw_text = response.content[0].text
    clusters = parse_cluster_response(raw_text, all_stories)
    return reorder_clusters(client, clusters)


def reorder_clusters(client, clusters):
    labels = "\n".join(f"{i}. {c['label']}" for i, c in enumerate(clusters))
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        system=REORDER_PROMPT,
        messages=[{"role": "user", "content": labels}],
    )
    raw = response.content[0].text.strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if fenced:
        raw = fenced.group(1)
    order = json.loads(raw)
    n = len(clusters)
    valid_order = [i for i in order if isinstance(i, int) and 0 <= i < n]
    # append any missing indices at the end
    seen = set(valid_order)
    valid_order += [i for i in range(n) if i not in seen]
    return [clusters[i] for i in valid_order]


def parse_cluster_response(raw_text, all_stories):
    # Strip markdown fences if present
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw_text)
    if fenced:
        raw_text = fenced.group(1)

    data = json.loads(raw_text)
    if "clusters" not in data:
        raise ValueError("Response missing 'clusters' key")

    n = len(all_stories)
    result = []
    seen_ids = set()

    for cluster in data["clusters"]:
        label = cluster["label"]
        raw_ids = cluster["story_ids"]

        clean_ids = []
        for sid in raw_ids:
            if sid < 0 or sid >= n:
                print(f"Warning: story_id {sid} out of range, skipping")
                continue
            if sid in seen_ids:
                print(f"Warning: story_id {sid} duplicated across clusters, skipping")
                continue
            seen_ids.add(sid)
            clean_ids.append(sid)

        if not clean_ids:
            continue

        # Drop "Update:" headlines — the AI sometimes ignores this instruction
        clean_ids = [sid for sid in clean_ids
                     if not all_stories[sid]["title"].startswith("Update:")]
        # If all were Updates, keep the first one so the cluster isn't empty
        if not clean_ids and raw_ids:
            first = next((sid for sid in raw_ids if 0 <= sid < n), None)
            if first is not None:
                clean_ids = [first]

        cap = 4 if label.lower() == "miscellaneous events" else 2
        clean_ids = clean_ids[:cap]

        stories = sorted(
            (all_stories[sid] for sid in clean_ids),
            key=lambda s: (s["date"], s["id"]),
        )
        result.append({"label": label, "stories": stories})

    if not result:
        raise ValueError("No valid clusters in AI response")

    return result


CSS = """
    body { font-family: system-ui, sans-serif; max-width: 680px; margin: 2rem auto; padding: 0 1rem; color: #222; }
    h1   { font-size: 1.5rem; color: #555; font-weight: normal; margin-bottom: .5rem; }
    h2   { font-size: 1rem; text-transform: uppercase; letter-spacing: .05em; color: #888; margin: 2rem 0 .5rem; }
    ul   { margin: 0; padding: 0; list-style: none; }
    li   { padding: .35rem 0; border-bottom: 1px solid #eee; line-height: 1.4; }
    a    { color: #111; text-decoration: none; }
    a:hover { text-decoration: underline; }
    .date { font-size: .8rem; color: #aaa; white-space: nowrap; }
    footer { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid #eee; font-size: .85rem; color: #aaa; }
"""

def page_content_clustered(clusters, prev_url=None, latest_url=None):
    return html[
        head[
            title["Deliberate News"],
            meta(name="viewport", content="width=device-width, initial-scale=1"),
            style[CSS],
        ],
        body[
            h1["Deliberate News"],
            p[
                (a(href=prev_url)["Previous issue"], "\u00a0\u00a0\u00a0|\u00a0\u00a0\u00a0") if prev_url and latest_url else (a(href=prev_url)["Previous issue"] if prev_url else ""),
                a(href=latest_url)["Latest issue"] if latest_url else "",
            ] if prev_url or latest_url else "",
            (
                (
                    h2[cluster["label"]],
                    ul[
                        (
                            li[
                                a(href=story["url"],
                                  target="_blank",
                                  rel="noopener noreferrer")[story["title"]],
                                " ",
                                span(".date")[story["date"]],
                            ]
                            for story in cluster["stories"]
                        )
                    ]
                )
                for cluster in clusters
            ),
        ]
    ]


def page_content(story_batches):
    return html[
        head[title["Deliberate News"]],
        body[
            h1["Deliberate News"],
            (
                (
                    h2[date],
                    ul[
                        (
                            li[
                                a(href= url_kagi+
                                            str(story_batch["batchId"])+"/"+
                                            story_batch["categoryName"].lower()+"/"+
                                            str(story["cluster_number"]-1),
                                        target="_blank",
                                        rel="noopener noreferrer")[ story["title"] ]
                            ]
                            for story in story_batch['stories']
                        )
                    ]
                )
                for date, story_batch in story_batches.items()
            )
        ]
    ]

def writeFile(file_path: str, html_content: str):
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"Successfully wrote HTML to {file_path}")
    except IOError as e:
        print(f"Error writing to file: {e}")

def makeHtml(clusters, previous_issue: str | None):
    os.makedirs("docs", exist_ok=True)
    date_str = datetime.today().strftime("%Y-%m-%d")
    writeFile("docs/index.html",
              str(page_content_clustered(clusters,
                                         prev_url=previous_issue)))
    writeFile(f"docs/{date_str}.html",
              str(page_content_clustered(clusters,
                                         prev_url=previous_issue,
                                         latest_url="index.html")))


CACHE_FILE = ".cache.json"

if __name__ == "__main__":
    selected_category = "world"
    use_cache = "--cached" in sys.argv

    if use_cache and os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            data = json.load(f)
        all_stories = data["all_stories"]
        story_batches = data["story_batches"]
        print(f"Loaded {len(all_stories)} stories from cache.")
    else:
        batches = getBatches()
        story_batches = {}
        all_stories = []

        print("Reading from API.", end='', flush=True)
        for batch in batches:
            categories = getCategories(batch)
            batch_date = str(datetime.fromisoformat(batch['createdAt']).date())
            story_batch = getStories(batch, categories, selected_category)
            story_batches[batch_date] = story_batch
            for story in story_batch["stories"]:
                url = (
                    url_kagi
                    + str(story_batch["batchId"]) + "/"
                    + story_batch["categoryName"].lower() + "/"
                    + str(story["cluster_number"] - 1)
                )
                all_stories.append({
                    "id": len(all_stories),
                    "title": story["title"],
                    "url": url,
                    "date": batch_date,
                })
            print('.', end='', flush=True)
        print('.')

        with open(CACHE_FILE, "w") as f:
            json.dump({"all_stories": all_stories, "story_batches": story_batches}, f)

        print(f"Fetched {len(all_stories)} stories total.")

    date_str = datetime.today().strftime("%Y-%m-%d")
    existing = sorted(f for f in glob.glob("docs/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].html")
                      if os.path.basename(f) != f"{date_str}.html")
    previous_issue = os.path.basename(existing[-1]) if existing else None

    try:
        clusters = cluster_stories_with_ai(all_stories)
        print(f"Clustered into {len(clusters)} topics.")
        makeHtml(clusters, previous_issue)
    except Exception as e:
        print(f"AI clustering failed ({e}), falling back to date view.")
        fallback = str(page_content(story_batches))
        writeFile("docs/index.html", fallback)
        writeFile(f"docs/{datetime.today().strftime('%Y-%m-%d')}.html", fallback)
