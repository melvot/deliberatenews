import json
import re
import requests
import anthropic
from datetime import datetime, timedelta
from htpy import body, h1, h2, head, html, li, title, ul, a, span

url_kagi = "https://kite.kagi.com/"

url_batch_list = url_kagi+"api/batches/"
url_categories = "/categories/"
url_stories = "/stories/"

date_from = datetime.today() - timedelta(days=7)
url_date_from = "?from="+str(date_from)

SYSTEM_PROMPT = """You are a news editor organizing a week of world news headlines into topic clusters.
Your task:
1. Identify duplicate/near-duplicate stories (same event, different phrasing, "Update:" variants). Keep only the most informative headline per duplicate group.
2. Group surviving unique headlines into coherent topic clusters.
3. Give each cluster a concise, neutral label (3-7 words).
Respond ONLY with JSON. No markdown fences, no explanation."""

USER_PROMPT_TEMPLATE = """Here are the headlines to process:

{headlines}

Return a JSON object with this exact schema:
{{"clusters": [{{"label": "Topic Label", "story_ids": [0, 1, 3, ...]}}]}}

Rules:
- Each story_id appears in exactly ONE cluster.
- Excluded story_ids are treated as deduplicated (dropped).
- Aim for 6-15 clusters.
- Order clusters by significance (most important first).
- Within each cluster, order story_ids chronologically (lowest ID first is fine)."""


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
    return parse_cluster_response(raw_text, all_stories)


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

        result.append({
            "label": label,
            "stories": [all_stories[sid] for sid in clean_ids],
        })

    if not result:
        raise ValueError("No valid clusters in AI response")

    return result


def page_content_clustered(clusters):
    return html[
        head[title["News from Kagi — By Topic"]],
        body[
            h1["News from Kagi — By Topic"],
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
                                span(style="color: grey")[f"({story['date']})"],
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
