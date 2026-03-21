from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

analyzer = SentimentIntensityAnalyzer()

def analyze_news(news_list):

    score = 0

    for news in news_list:

        result = analyzer.polarity_scores(news["title"])

        score += result["compound"]

    if score > 0.2:
        return "Positive Market Sentiment"

    elif score < -0.2:
        return "Negative Market Sentiment"

    else:
        return "Neutral Market Sentiment"
    