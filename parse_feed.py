import json
import requests
from datetime import datetime, timedelta
from htpy import body, h1, h2, head, html, li, title, ul, a

url_kagi = "https://kite.kagi.com/"

url_batch_list = url_kagi+"api/batches/"
url_categories = "/categories/"
url_stories = "/stories/"

date_from = datetime.today() - timedelta(days=7)
url_date_from = "?from="+str(date_from)


def getCategoryId(categories, selected_category):
    for category in categories:
        if category['categoryId'] == selected_category:
            return category['id']

    raise Exception("categoryId "+category+" not found")

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

def makeHtml(story_batches):
    html_output = str(page_content(story_batches))

    date_today = datetime.today()
    file_path = str(date_today)+"_"+"index.html"
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(html_output)
        print(f"Successfully wrote HTML to {file_path}")
    except IOError as e:
        print(f"Error writing to file: {e}")


if __name__ == "__main__":
    selected_category = "world"

    batches = getBatches()
    story_batches = {}

    print("Reading from API.", end='', flush=True)
    for batch in batches:
        categories = getCategories(batch)
        batch_date = str(datetime.fromisoformat(batch['createdAt']).date())
        story_batches[batch_date] = getStories(batch, categories, selected_category)
        print('.', end='', flush=True)
    print('.')

    makeHtml(story_batches)
