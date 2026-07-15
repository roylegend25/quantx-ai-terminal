_cache: dict[tuple[str, str, str, str, str], dict] = {}
def key(user_id, engine, version, symbol, timeframe): return (user_id, engine, version, symbol, timeframe)
def invalidate_user(user_id):
    for item in [k for k in _cache if k[0] == user_id]: _cache.pop(item, None)
