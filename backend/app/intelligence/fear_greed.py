import httpx

FEAR_GREED_URL = "https://api.alternative.me/fng/"


async def fetch() -> dict:
    """Crypto Fear & Greed Index (alternative.me). 0 = extreme fear, 100 = extreme greed."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(FEAR_GREED_URL, params={"limit": 1})
        r.raise_for_status()
        data = r.json()

    row = data["data"][0]
    return {
        "fear_greed_index": int(row["value"]),
        "fear_greed_label": row["value_classification"],
    }
