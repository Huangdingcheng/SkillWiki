"""Redis 缓存层 — 热点 Skill 缓存和分布式锁。"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from ..models.skill_model import Skill
from ..utils.logger import get_logger
from .base import BaseConnection

logger = get_logger(__name__)

# 缓存键前缀
_SKILL_PREFIX = "skill:"
_GRAPH_STATS_KEY = "graph:stats"
_SKILL_LIST_PREFIX = "skill_list:"
_LOCK_PREFIX = "lock:"

# 默认 TTL（秒）
DEFAULT_SKILL_TTL = 300       # 5 分钟
DEFAULT_STATS_TTL = 60        # 1 分钟
DEFAULT_LIST_TTL = 120        # 2 分钟
DEFAULT_LOCK_TTL = 30         # 30 秒


class RedisConnection(BaseConnection):
    """Redis 异步连接管理器。"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: Optional[str] = None,
        max_connections: int = 20,
    ) -> None:
        self._host = host
        self._port = port
        self._db = db
        self._password = password
        self._max_connections = max_connections
        self._client: Optional[Any] = None

    async def connect(self) -> None:
        try:
            import redis.asyncio as aioredis
            self._client = aioredis.Redis(
                host=self._host,
                port=self._port,
                db=self._db,
                password=self._password,
                max_connections=self._max_connections,
                decode_responses=True,
            )
            await self._client.ping()
            logger.info(f"Redis 连接成功: {self._host}:{self._port}/{self._db}")
        except ImportError:
            raise RuntimeError("请安装 redis 驱动: pip install redis")

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("Redis 连接已关闭")

    async def health_check(self) -> bool:
        if not self._client:
            return False
        try:
            await self._client.ping()
            return True
        except Exception as e:
            logger.error(f"Redis 健康检查失败: {e}")
            return False

    async def ping(self) -> float:
        start = time.monotonic()
        await self.health_check()
        return (time.monotonic() - start) * 1000

    @property
    def client(self) -> Any:
        if not self._client:
            raise RuntimeError("Redis 未连接，请先调用 connect()")
        return self._client


# ---------------------------------------------------------------------------
# Skill Cache
# ---------------------------------------------------------------------------

class SkillCache:
    """Skill 对象的 Redis 缓存。"""

    def __init__(self, conn: RedisConnection) -> None:
        self._conn = conn

    def _key(self, skill_id: str) -> str:
        return f"{_SKILL_PREFIX}{skill_id}"

    async def get(self, skill_id: str) -> Optional[Skill]:
        try:
            data = await self._conn.client.get(self._key(skill_id))
            if data:
                return Skill.model_validate_json(data)
        except Exception as e:
            logger.warning(f"缓存读取失败 [{skill_id}]: {e}")
        return None

    async def set(self, skill: Skill, ttl: int = DEFAULT_SKILL_TTL) -> None:
        try:
            await self._conn.client.setex(
                self._key(skill.skill_id),
                ttl,
                skill.model_dump_json(),
            )
        except Exception as e:
            logger.warning(f"缓存写入失败 [{skill.skill_id}]: {e}")

    async def delete(self, skill_id: str) -> None:
        try:
            await self._conn.client.delete(self._key(skill_id))
        except Exception as e:
            logger.warning(f"缓存删除失败 [{skill_id}]: {e}")

    async def invalidate_pattern(self, pattern: str) -> int:
        """按模式批量删除缓存键，返回删除数量。"""
        try:
            keys = await self._conn.client.keys(f"{_SKILL_PREFIX}{pattern}")
            if keys:
                return await self._conn.client.delete(*keys)
        except Exception as e:
            logger.warning(f"批量缓存删除失败: {e}")
        return 0

    async def get_many(self, skill_ids: List[str]) -> Dict[str, Optional[Skill]]:
        """批量获取，使用 pipeline 减少 RTT。"""
        result: Dict[str, Optional[Skill]] = {}
        if not skill_ids:
            return result
        try:
            pipe = self._conn.client.pipeline()
            for sid in skill_ids:
                pipe.get(self._key(sid))
            values = await pipe.execute()
            for sid, val in zip(skill_ids, values):
                result[sid] = Skill.model_validate_json(val) if val else None
        except Exception as e:
            logger.warning(f"批量缓存读取失败: {e}")
            result = {sid: None for sid in skill_ids}
        return result

    async def set_many(self, skills: List[Skill], ttl: int = DEFAULT_SKILL_TTL) -> None:
        """批量写入缓存。"""
        if not skills:
            return
        try:
            pipe = self._conn.client.pipeline()
            for skill in skills:
                pipe.setex(self._key(skill.skill_id), ttl, skill.model_dump_json())
            await pipe.execute()
        except Exception as e:
            logger.warning(f"批量缓存写入失败: {e}")


# ---------------------------------------------------------------------------
# Distributed Lock
# ---------------------------------------------------------------------------

class DistributedLock:
    """基于 Redis SET NX 的简单分布式锁。"""

    def __init__(self, conn: RedisConnection, name: str, ttl: int = DEFAULT_LOCK_TTL) -> None:
        self._conn = conn
        self._key = f"{_LOCK_PREFIX}{name}"
        self._ttl = ttl
        self._token: Optional[str] = None

    async def acquire(self) -> bool:
        """尝试获取锁，返回是否成功。"""
        import uuid
        token = str(uuid.uuid4())
        acquired = await self._conn.client.set(
            self._key, token, nx=True, ex=self._ttl
        )
        if acquired:
            self._token = token
        return bool(acquired)

    async def release(self) -> bool:
        """释放锁（仅释放自己持有的锁）。"""
        if not self._token:
            return False
        # Lua 脚本保证原子性
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        result = await self._conn.client.eval(lua_script, 1, self._key, self._token)
        if result:
            self._token = None
        return bool(result)

    async def __aenter__(self) -> "DistributedLock":
        acquired = await self.acquire()
        if not acquired:
            raise RuntimeError(f"无法获取分布式锁: {self._key}")
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.release()


# ---------------------------------------------------------------------------
# Stats Cache
# ---------------------------------------------------------------------------

class StatsCache:
    """图统计信息缓存。"""

    def __init__(self, conn: RedisConnection) -> None:
        self._conn = conn

    async def get_graph_stats(self) -> Optional[Dict[str, Any]]:
        try:
            data = await self._conn.client.get(_GRAPH_STATS_KEY)
            return json.loads(data) if data else None
        except Exception:
            return None

    async def set_graph_stats(self, stats: Dict[str, Any], ttl: int = DEFAULT_STATS_TTL) -> None:
        try:
            await self._conn.client.setex(_GRAPH_STATS_KEY, ttl, json.dumps(stats))
        except Exception as e:
            logger.warning(f"统计缓存写入失败: {e}")

    async def increment_usage(self, skill_id: str) -> int:
        """原子递增 Skill 使用计数（用于热点排行）。"""
        key = f"usage_count:{skill_id}"
        try:
            return await self._conn.client.incr(key)
        except Exception:
            return 0

    async def get_hot_skills(self, top_n: int = 20) -> List[str]:
        """获取使用量最高的 Skill ID 列表。"""
        try:
            keys = await self._conn.client.keys("usage_count:*")
            if not keys:
                return []
            pipe = self._conn.client.pipeline()
            for k in keys:
                pipe.get(k)
            values = await pipe.execute()
            counts = [
                (k.replace("usage_count:", ""), int(v or 0))
                for k, v in zip(keys, values)
            ]
            counts.sort(key=lambda x: x[1], reverse=True)
            return [sid for sid, _ in counts[:top_n]]
        except Exception as e:
            logger.warning(f"热点 Skill 查询失败: {e}")
            return []
