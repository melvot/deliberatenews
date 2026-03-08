import json
import re
import requests
import anthropic
from datetime import datetime, timedelta
from htpy import body, h1, h2, head, html, li, meta, title, ul, a, span, style

url_kagi = "https://kite.kagi.com/"

url_batch_list = url_kagi+"api/batches/"
url_categories = "/categories/"
url_stories = "/stories/"

date_from = datetime.today() - timedelta(days=7)
url_date_from = "?from="+str(date_from)

SYSTEM_PROMPT = """You are a news editor organizing a week of world news headlines into topic clusters.
Your task:
1. Aggressively deduplicate: treat any headlines covering the same ongoing event as duplicates, even if details differ (e.g. casualty counts, named officials, incremental updates). Keep only the single most informative headline per event — typically the most recent or most comprehensive one. When in doubt, drop it.
2. Group surviving unique headlines into coherent topic clusters.
3. Give each cluster a concise, neutral label (3-7 words).
Respond ONLY with JSON. No markdown fences, no explanation."""

USER_PROMPT_TEMPLATE = """Here are the headlines to process:

{headlines}

Return a JSON object with this exact schema:
{{"clusters": [{{"label": "Topic Label", "story_ids": [0, 1, 3, ...]}}]}}

Rules:
- Each story_id appears in exactly ONE cluster.
- Excluded story_ids are treated as deduplicated (dropped). Exclude liberally.
- A cluster should have at most 3-4 stories; if you have more, deduplicate further.
- Aim for 8-12 clusters total.
- Order clusters by significance (most important first).
- Within each cluster, order story_ids chronologically (lowest ID first is fine)."""

REORDER_PROMPT = """You are given a numbered list of news topic cluster labels.
Reorder them so that thematically related topics are adjacent (e.g. all Middle East topics together, all US politics together, all economics together).
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
    h1   { font-size: 1.2rem; color: #555; font-weight: normal; margin-bottom: 2rem; }
    h2   { font-size: 1rem; text-transform: uppercase; letter-spacing: .05em; color: #888; margin: 2rem 0 .5rem; }
    ul   { margin: 0; padding: 0; list-style: none; }
    li   { padding: .35rem 0; border-bottom: 1px solid #eee; line-height: 1.4; }
    a    { color: #111; text-decoration: none; }
    a:hover { text-decoration: underline; }
    .date { font-size: .8rem; color: #aaa; white-space: nowrap; }
"""

def page_content_clustered(clusters):
    return html[
        head[
            title["News from Kagi"],
            meta(name="viewport", content="width=device-width, initial-scale=1"),
            style[CSS],
        ],
        body[
            h1["News from Kagi"],
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
            )
        ]
    ]


def page_content(story_batches):
    return html[
        head[title["News from Kagi"]],
        body[
            h1["News from Kagi"],
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

def makeHtml(html_content: str):
    date_today = datetime.today()
    file_path = str(date_today)+"_"+"index.html"
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"Successfully wrote HTML to {file_path}")
    except IOError as e:
        print(f"Error writing to file: {e}")


if __name__ == "__main__":
    selected_category = "world"

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

    print(f"Fetched {len(all_stories)} stories total.")

    try:
        clusters = cluster_stories_with_ai(all_stories)
        print(f"Clustered into {len(clusters)} topics.")
        html_output = str(page_content_clustered(clusters))
    except Exception as e:
        print(f"AI clustering failed ({e}), falling back to date view.")
        html_output = str(page_content(story_batches))

    makeHtml(html_output)
