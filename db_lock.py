import asyncio
_db_lock = asyncio.Lock()

def get_db_lock():
    return _db_lock