"""存储层抽象基类 — 定义统一的 CRUD 接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Generic, List, Optional, TypeVar

T = TypeVar("T")


class BaseRepository(ABC, Generic[T]):
    """通用仓储接口，所有存储后端实现此接口。"""

    @abstractmethod
    async def get(self, id: str) -> Optional[T]:
        """按 ID 获取单个实体。"""

    @abstractmethod
    async def list(
        self,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[T]:
        """列表查询，支持过滤、分页。"""

    @abstractmethod
    async def create(self, entity: T) -> T:
        """创建实体，返回持久化后的实体。"""

    @abstractmethod
    async def update(self, id: str, data: Dict[str, Any]) -> Optional[T]:
        """部分更新实体。"""

    @abstractmethod
    async def delete(self, id: str) -> bool:
        """删除实体，返回是否成功。"""

    @abstractmethod
    async def exists(self, id: str) -> bool:
        """检查实体是否存在。"""

    @abstractmethod
    async def count(self, filters: Optional[Dict[str, Any]] = None) -> int:
        """统计满足条件的实体数量。"""


class BaseConnection(ABC):
    """数据库连接抽象基类。"""

    @abstractmethod
    async def connect(self) -> None:
        """建立连接。"""

    @abstractmethod
    async def disconnect(self) -> None:
        """断开连接。"""

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查，返回连接是否正常。"""

    @abstractmethod
    async def ping(self) -> float:
        """Ping 测试，返回延迟（毫秒）。"""
