import asyncio
from typing import Coroutine, TypeVar, Any

T = TypeVar("T")

def run_async(coro: Coroutine[Any, Any, T]) -> T:
    """Run an async coroutine, reusing the thread's event loop to prevent asyncpg pool errors."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)
