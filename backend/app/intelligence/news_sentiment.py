import os

NEWS_API_KEY = os.getenv("NEWS_API_KEY")


async def fetch() -> dict:
    """News sentiment score in [-1, 1].

    No news-sentiment provider is configured (would need e.g. CryptoPanic or
    NewsAPI). Rather than fabricate a number, this returns a neutral,
    clearly-flagged placeholder. Set NEWS_API_KEY and wire a real provider
    here to activate it - the rest of the intelligence pipeline already
    treats a neutral/unavailable reading as a zero-weight contributor.
    """
    if not NEWS_API_KEY:
        return {
            "news_sentiment": 0.0,
            "status": "unavailable",
            "reason": "no news provider configured",
        }

    return {
        "news_sentiment": 0.0,
        "status": "unavailable",
        "reason": "provider integration not implemented",
    }
