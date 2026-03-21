import requests


def get_news():

    try:

        url = "https://newsapi.org/v2/top-headlines?category=business&language=en&apiKey=demoa089b99acb7d4294af1f7464e9896c92"

        response = requests.get(url)

        data = response.json()

        articles = data.get("articles", [])

        news_list = []

        for article in articles[:5]:

            news_list.append({
                "title": article["title"],
                "url": article["url"]
            })

        return news_list

    except:

        return []