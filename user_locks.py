# /home/ubuntu/botpub/user_locks.py
from collections import defaultdict
import asyncio

_user_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

def get_user_lock(user_id: int) -> asyncio.Lock:
    return _user_locks[user_id]