"""Native backend — OKS 默认召回（6+1 因子 + jieba + IDF + title boost）。

包装 ``recall()``，无新依赖，实时遍历 wiki/。
"""
from __future__ import annotations

from typing import Any

from . import SearchHit

# recall() 接受的 kwargs 白名单（避免 backend 传无关参数）
_RECALL_KWARGS = ("goal", "goal_boost", "type_filter", "explain", "topic_id")


class NativeBackend:
    """包装 ``recall()`` — OKS 默认 6+1 召回。

    无预索引（实时计算），无新依赖。小库（< 1000 页）足够快。
    """

    def index(self, pages: list[dict[str, Any]]) -> None:
        """native 实时计算，无需预索引。"""
        return None

    def search(
        self, query: str, *, limit: int = 10, scope: str | None = None, **kwargs: Any
    ) -> list[SearchHit]:
        from ..recall import recall

        recall_kwargs = {k: v for k, v in kwargs.items() if k in _RECALL_KWARGS}
        r = recall(
            query=query,
            limit=limit,
            scope=scope,
            knowledge_only=True,
            search_backend="legacy",  # 强制 6+1，避免 fusion 递归
            **recall_kwargs,
        )
        return [
            SearchHit(
                slug=h["slug"],
                title=h.get("title", h["slug"]),
                score=h.get("relevance", 0.0),
                backend="native",
                extra={"confidence": h.get("confidence", 0.8)},
            )
            for h in r.get("knowledge", [])
        ]


__all__ = ["NativeBackend"]
