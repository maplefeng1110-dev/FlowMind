"""Minimal Key/Value + Pub/Sub abstraction backing the distributed dispatch broker.

A narrow interface (:class:`KeyValuePubSub`) is implemented twice:

* :class:`MemoryKV` -- in-process. Used when ``REDIS_URL`` is unset (single-instance
  fallback) and in tests, where two brokers can share one ``MemoryKV`` to simulate
  multiple Registry instances.
* :class:`RedisKV` -- backed by ``redis.asyncio``; lets multiple Registry instances
  coordinate (shared online table, cross-instance result/task delivery, locks).

Only the few operations the broker needs are exposed (SET NX PX, compare-and-delete,
small hash ops, publish/subscribe), so both backends stay tiny and the broker logic
is written once against this interface.
"""
from __future__ import annotations

import abc
import asyncio
import time
from typing import Dict, List, Optional, Tuple


class KeyValuePubSub(abc.ABC):
    @abc.abstractmethod
    async def set_nx(self, key: str, value: str, ttl_ms: int) -> bool:
        """Set key=value only if absent, with a TTL. Returns True if it was set."""

    @abc.abstractmethod
    async def get(self, key: str) -> Optional[str]: ...

    @abc.abstractmethod
    async def delete_if(self, key: str, expected: str) -> bool:
        """Delete key only if its value equals ``expected`` (safe lock release)."""

    @abc.abstractmethod
    async def hset(self, name: str, field: str, value: str) -> None: ...

    @abc.abstractmethod
    async def hget(self, name: str, field: str) -> Optional[str]: ...

    @abc.abstractmethod
    async def hdel(self, name: str, field: str) -> None: ...

    @abc.abstractmethod
    async def hgetall(self, name: str) -> Dict[str, str]: ...

    @abc.abstractmethod
    async def publish(self, channel: str, message: str) -> None: ...

    @abc.abstractmethod
    def subscribe(self, channel: str):
        """Return an async-iterable subscription with an async ``close()``."""

    @abc.abstractmethod
    async def close(self) -> None: ...


# --------------------------------------------------------------------------- #
# In-memory backend (single process; also used to simulate N instances in tests)
# --------------------------------------------------------------------------- #
class _MemorySubscription:
    def __init__(self, queue: "asyncio.Queue", on_close):
        self._queue = queue
        self._on_close = on_close
        self._closed = False

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if self._closed:
            raise StopAsyncIteration
        message = await self._queue.get()
        if message is None:  # close sentinel
            raise StopAsyncIteration
        return message

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._on_close(self._queue)
            await self._queue.put(None)


class MemoryKV(KeyValuePubSub):
    def __init__(self):
        self._data: Dict[str, Tuple[str, Optional[float]]] = {}
        self._hashes: Dict[str, Dict[str, str]] = {}
        self._subs: Dict[str, List["asyncio.Queue"]] = {}

    def _alive(self, key: str) -> Optional[str]:
        item = self._data.get(key)
        if item is None:
            return None
        value, expires_at = item
        if expires_at is not None and expires_at < time.monotonic():
            self._data.pop(key, None)
            return None
        return value

    async def set_nx(self, key: str, value: str, ttl_ms: int) -> bool:
        if self._alive(key) is not None:
            return False
        expires_at = time.monotonic() + ttl_ms / 1000.0 if ttl_ms else None
        self._data[key] = (value, expires_at)
        return True

    async def get(self, key: str) -> Optional[str]:
        return self._alive(key)

    async def delete_if(self, key: str, expected: str) -> bool:
        if self._alive(key) == expected:
            self._data.pop(key, None)
            return True
        return False

    async def hset(self, name: str, field: str, value: str) -> None:
        self._hashes.setdefault(name, {})[field] = value

    async def hget(self, name: str, field: str) -> Optional[str]:
        return self._hashes.get(name, {}).get(field)

    async def hdel(self, name: str, field: str) -> None:
        self._hashes.get(name, {}).pop(field, None)

    async def hgetall(self, name: str) -> Dict[str, str]:
        return dict(self._hashes.get(name, {}))

    async def publish(self, channel: str, message: str) -> None:
        for queue in list(self._subs.get(channel, [])):
            queue.put_nowait(message)

    def subscribe(self, channel: str) -> _MemorySubscription:
        queue: "asyncio.Queue" = asyncio.Queue()
        self._subs.setdefault(channel, []).append(queue)

        def _on_close(target_queue):
            subscribers = self._subs.get(channel, [])
            if target_queue in subscribers:
                subscribers.remove(target_queue)

        return _MemorySubscription(queue, _on_close)

    async def close(self) -> None:
        self._subs.clear()


# --------------------------------------------------------------------------- #
# Redis backend
# --------------------------------------------------------------------------- #
class _RedisSubscription:
    def __init__(self, redis, channel: str):
        self._redis = redis
        self._channel = channel
        self._pubsub = None
        self._closed = False

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if self._pubsub is None:
            self._pubsub = self._redis.pubsub()
            await self._pubsub.subscribe(self._channel)
        while not self._closed:
            message = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message.get("type") == "message":
                return message["data"]
        raise StopAsyncIteration

    async def close(self) -> None:
        self._closed = True
        if self._pubsub is not None:
            try:
                await self._pubsub.unsubscribe(self._channel)
                await self._pubsub.aclose()
            except Exception:  # noqa: BLE001
                pass


class RedisKV(KeyValuePubSub):
    # Lua: delete the key only if it still holds the expected token (atomic).
    _DELETE_IF = (
        "if redis.call('get', KEYS[1]) == ARGV[1] "
        "then return redis.call('del', KEYS[1]) else return 0 end"
    )

    def __init__(self, url: str):
        import redis.asyncio as redis_asyncio

        self._redis = redis_asyncio.from_url(url, decode_responses=True)

    async def set_nx(self, key: str, value: str, ttl_ms: int) -> bool:
        return bool(await self._redis.set(key, value, nx=True, px=ttl_ms))

    async def get(self, key: str) -> Optional[str]:
        return await self._redis.get(key)

    async def delete_if(self, key: str, expected: str) -> bool:
        return bool(await self._redis.eval(self._DELETE_IF, 1, key, expected))

    async def hset(self, name: str, field: str, value: str) -> None:
        await self._redis.hset(name, field, value)

    async def hget(self, name: str, field: str) -> Optional[str]:
        return await self._redis.hget(name, field)

    async def hdel(self, name: str, field: str) -> None:
        await self._redis.hdel(name, field)

    async def hgetall(self, name: str) -> Dict[str, str]:
        return await self._redis.hgetall(name)

    async def publish(self, channel: str, message: str) -> None:
        await self._redis.publish(channel, message)

    def subscribe(self, channel: str) -> _RedisSubscription:
        return _RedisSubscription(self._redis, channel)

    async def close(self) -> None:
        try:
            await self._redis.aclose()
        except Exception:  # noqa: BLE001
            pass


def build_kv(redis_url: Optional[str]) -> KeyValuePubSub:
    """MemoryKV when no Redis URL is configured, RedisKV otherwise."""
    if redis_url:
        return RedisKV(redis_url)
    return MemoryKV()
