import glob
import json
import os
import re
import sys
import requests
import anthropic
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from dotenv import load_dotenv

load_dotenv()
from htpy import body, h1, h2, head, html, li, meta, title, ul, a, span, style, p

url_kagi = "https://kite.kagi.com/"

url_batch_list = url_kagi+"api/batches/"
url_categories = "/categories/"
url_stories = "/stories/"

date_from = datetime.today() - timedelta(days=7)
url_date_from = "?from="+str(date_from)

CLUSTER_SYSTEM_PROMPT = """You are a news editor organizing world news headlines into topic clusters.
Group headlines by the story or situation they cover.
Respond ONLY with JSON. No markdown fences, no explanation."""

CLUSTER_USER_TEMPLATE = """Here are the headlines to organize:

{headlines}

Return a JSON object with this exact schema:
{{"clusters": [{{"label": "Topic Label", "story_ids": [0, 1, 3, ...]}}]}}

Rules:
- Give each cluster a concise, neutral label (3-7 words).
- Group by event or situation, not by country.
- Every story must appear in exactly one cluster. Do not drop any stories.
- Aim for 3-8 stories per cluster. If a topic has more than 8 stories, split it into meaningful sub-clusters (e.g. "Iran nuclear negotiations" and "Iran oil sanctions" rather than one giant "Iran" cluster).
- "Miscellaneous events" is the final cluster for stories that don't fit any named theme.
- Order clusters by significance (most important first), with "Miscellaneous events" always last."""

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


def deduplicate(all_stories):
    # Drop "Update:" headlines
    stories = [s for s in all_stories if not s["title"].startswith("Update:")]

    # Fuzzy match: compare every pair, drop shorter headline if similarity > 0.65
    drop_ids = set()
    for i, a in enumerate(stories):
        if a["id"] in drop_ids:
            continue
        for b in stories[i + 1:]:
            if b["id"] in drop_ids:
                continue
            ratio = SequenceMatcher(None, a["title"].lower(), b["title"].lower()).ratio()
            if ratio > 0.65:
                shorter = a if len(a["title"]) < len(b["title"]) else b
                drop_ids.add(shorter["id"])

    deduped = [s for s in stories if s["id"] not in drop_ids]
    print(f"Deduplicated: {len(all_stories)} → {len(deduped)} stories.")
    return deduped


SPLIT_PROMPT = """You are a news editor. The following headlines were all grouped under one topic, but there are too many.
Split them into 2-4 smaller, more specific sub-clusters.
Respond ONLY with JSON. No markdown fences, no explanation."""

SPLIT_USER_TEMPLATE = """Original topic: {label}

Headlines:
{headlines}

Return a JSON object with this exact schema:
{{"clusters": [{{"label": "Sub-topic Label", "story_ids": [0, 1, ...]}}]}}

Rules:
- Every story must appear in exactly one sub-cluster.
- Give each sub-cluster a concise label (3-7 words) more specific than the original.
- Aim for 3-6 stories per sub-cluster."""


VALIDATE_SYSTEM_PROMPT = """You are a news editor checking whether headlines were assigned to the right topic cluster.
For each headline, decide if it genuinely belongs under the given cluster label.
Respond ONLY with JSON. No markdown fences, no explanation."""

VALIDATE_USER_TEMPLATE = """For each cluster below, return the story IDs that do NOT belong under that label.

{clusters}

Return a JSON object with this exact schema:
{{"mismatches": [{{"cluster_index": 0, "bad_ids": [3, 7]}}]}}

Rules:
- Only flag clear mismatches — a story about Vietnam does not belong in "Africa elections".
- If all stories fit, return {{"mismatches": []}}."""


def validate_clusters(client, clusters):
    desc = ""
    for i, c in enumerate(clusters):
        if c["label"].lower() == "miscellaneous events":
            continue
        headlines = ", ".join(f'{s["id"]}:"{s["title"]}"' for s in c["stories"])
        desc += f'{i}. [{c["label"]}] {headlines}\n'

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=VALIDATE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": VALIDATE_USER_TEMPLATE.format(clusters=desc)}],
    )
    raw = response.content[0].text.strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if fenced:
        raw = fenced.group(1)
    mismatches = json.loads(raw).get("mismatches", [])

    evicted = []
    for m in mismatches:
        idx = m.get("cluster_index")
        bad = set(m.get("bad_ids", []))
        if not bad or not isinstance(idx, int) or idx >= len(clusters):
            continue
        c = clusters[idx]
        moved = [s for s in c["stories"] if s["id"] in bad]
        c["stories"] = [s for s in c["stories"] if s["id"] not in bad]
        evicted.extend(moved)

    if evicted:
        print(f"Validation moved {len(evicted)} misassigned stories to Miscellaneous.")
    return clusters, evicted


def split_cluster(client, cluster):
    stories = cluster["stories"]
    local = [{**s, "id": i} for i, s in enumerate(stories)]
    headlines = "\n".join(f"{s['id']}. {stories[i]['title']}" for i, s in enumerate(local))

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=SPLIT_PROMPT,
        messages=[{"role": "user", "content": SPLIT_USER_TEMPLATE.format(
            label=cluster["label"], headlines=headlines
        )}],
    )
    sub_clusters = parse_cluster_response(response.content[0].text, local)
    # Map local IDs back to original story objects
    for sc in sub_clusters:
        sc["stories"] = [stories[s["id"]] for s in sc["stories"]]
    return sub_clusters


def cluster_stories_with_ai(all_stories):
    client = anthropic.Anthropic()

    deduped = deduplicate(all_stories)
    reindexed = [{**s, "id": i} for i, s in enumerate(deduped)]
    headlines = "\n".join(f"{s['id']}. {s['title']}" for s in reindexed)

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        system=CLUSTER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": CLUSTER_USER_TEMPLATE.format(headlines=headlines)}],
    )
    clusters = parse_cluster_response(response.content[0].text, reindexed)

    # Split oversized clusters with a targeted AI call
    split_result = []
    for c in clusters:
        if c["label"].lower() != "miscellaneous events" and len(c["stories"]) > 8:
            print(f"Splitting oversized cluster '{c['label']}' ({len(c['stories'])} stories).")
            sub = split_cluster(client, c)
            split_result.extend(sub)
        else:
            split_result.append(c)
    clusters = split_result

    # Validate assignments — move mismatches to Miscellaneous
    clusters, evicted = validate_clusters(client, clusters)

    # Collect all overflow: evicted stories first
    overflow = list(evicted)
    evicted_ids = {s["id"] for s in evicted}

    # Find stories dropped by AI (not in any cluster and not already evicted)
    assigned_ids = set()
    for c in clusters:
        for s in c["stories"]:
            assigned_ids.add(s["id"])
    assigned_ids.update(evicted_ids)
    missing = [s for s in reindexed if s["id"] not in assigned_ids]
    overflow.extend(missing)
    kept = []
    for c in clusters:
        if c["label"].lower() == "miscellaneous events":
            kept.append(c)
        elif len(c["stories"]) < 2:
            overflow.extend(c["stories"])
        else:
            kept.append(c)
    clusters = kept

    if overflow:
        print(f"Moving {len(overflow)} stories to Miscellaneous.")
        misc = next((c for c in clusters if c["label"].lower() == "miscellaneous events"), None)
        if misc:
            misc["stories"].extend(sorted(overflow, key=lambda s: (s["date"], s["id"])))
        else:
            clusters.append({"label": "Miscellaneous events", "stories": sorted(overflow, key=lambda s: (s["date"], s["id"]))})

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


def page_content_fallback(all_stories):
    by_date = {}
    for s in all_stories:
        by_date.setdefault(s["date"], []).append(s)
    return html[
        head[
            title["Deliberate News"],
            meta(name="viewport", content="width=device-width, initial-scale=1"),
            style[CSS],
        ],
        body[
            h1["Deliberate News"],
            (
                (
                    h2[date],
                    ul[
                        (
                            li[
                                a(href=s["url"], target="_blank",
                                  rel="noopener noreferrer")[s["title"]],
                                " ", span(".date")[s["date"]],
                            ]
                            for s in stories
                        )
                    ]
                )
                for date, stories in sorted(by_date.items())
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
        fallback = str(page_content_fallback(all_stories))
        writeFile("docs/index.html", fallback)
        writeFile(f"docs/{datetime.today().strftime('%Y-%m-%d')}.html", fallback)
