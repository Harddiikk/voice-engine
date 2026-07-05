"""ARQ redis pool + job enqueue — the light *producer* side.

Kept separate from ``api.tasks.arq`` (which imports every task function for the
worker, pulling heavy pipeline deps) so services/tasks can enqueue jobs without
that import cost. ``api.tasks.arq`` re-exports these for backward compatibility.
"""

import ssl
from urllib.parse import urlparse

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from api.constants import REDIS_URL
from api.tasks.function_names import FunctionNames

_parsed_url = urlparse(REDIS_URL)
_use_ssl = _parsed_url.scheme == "rediss"

REDIS_SETTINGS = RedisSettings(
    host=_parsed_url.hostname or "localhost",
    port=_parsed_url.port or 6379,
    password=_parsed_url.password,
    conn_timeout=10,
    ssl=_use_ssl,
    ssl_ca_certs=None,
    ssl_certfile=None,
    ssl_keyfile=None,
    ssl_check_hostname=False if _use_ssl else None,
)

_redis_pool: ArqRedis | None = None


async def get_arq_redis() -> ArqRedis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = await create_pool(REDIS_SETTINGS)
    return _redis_pool


async def enqueue_job(function_name: FunctionNames, *args):
    redis = await get_arq_redis()
    await redis.enqueue_job(function_name, *args)
