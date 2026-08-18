"""Fusion backend — native 主 top-3 + fts5 补盲 2。

实验验证最优（15 模糊 query）：
- native scoped+goal: R@1=0.600, MRR=0.689
- fts5 alone: R@1=0.133（差，但独有命中 4 个 native 漏的）
- RRF 1:1: R@1=0.467（fts5 噪声稀释 native top-1）
- **fusion (native主+fts5补盲)**: R@1=0.667, MRR=0.722 ✅

设计：native 保 R@1 优势（6+1 因子 + scope/goal boost），fts5 补 native 漏的
（BM25 + 结构化关键词命中 native 的 substring 盲区）。
"""
from __future__ import annotations

from typing import Any

from . import SearchHit


class FusionBackend:
    """native 主排序 + fts5 独有补盲。

    - native 取 top-N（默认 3）作主排序
    - fts5 取全部候选，去重后补 M 个（默认 2）native 没命中的
    - 最终 = native top-N + fts5 独有 M

    参数可调（实验数据支持调优，非盲调）。
    """

    def __init__(
        self,
        root: str | None = None,
        native_top: int = 3,
        fts5_supplement: int = 2,
        **kwargs: Any,
    ) -> None:
        from .native import NativeBackend
        from .fts5 import FTS5Backend

        self._native = NativeBackend()
        self._fts5 = FTS5Backend(root=root, **{
            k: v for k, v in kwargs.items() if k in ("db_path", "weights")
        })
        self._native_top = native_top
        self._fts5_supplement = fts5_supplement

    def index(self, pages: list[dict[str, Any]]) -> None:
        """native no-op；fts5 预索引。"""
        self._native.index(pages)
        self._fts5.index(pages)

    def search(
        self, query: str, *, limit: int = 10, scope: str | None = None, **kwargs: Any
    ) -> list[SearchHit]:
        # v0.6.0: fts5 node-level 召回 + native 6+1 re-rank。
        # 之前 native 主召回 + fts5 补盲，但 native(54%) 拖累 fts5(96%)。
        # 现在 fts5 作主召回（精度高），native 6+1 因子（memory curve/goal
        # boost/review bonus）作归一化加权 re-rank——保留 oks 灵魂又不丢精度。
        fts5_hits = self._fts5.search(query, limit=limit * 2, scope=scope, **kwargs)
        if not fts5_hits:
            return self._native.search(query, limit=limit, scope=scope, **kwargs)
        native_hits = self._native.search(
            query, limit=limit * 2, scope=scope, **kwargs
        )
        native_map = {h.slug: h.score for h in native_hits}
        fts5_max = max((h.score for h in fts5_hits), default=1.0) or 1.0
        native_max = max(native_map.values(), default=1.0) or 1.0
        for h in fts5_hits:
            f_norm = h.score / fts5_max  # 0-1
            n_norm = native_map.get(h.slug, 0) / native_max if native_map else 0
            h.score = 0.7 * f_norm + 0.3 * n_norm  # fts5 主 + native 灵魂
        fts5_hits.sort(key=lambda h: -h.score)
        return fts5_hits[:limit]

        seen = {h.slug for h in native_hits}
        # v0.6.0: limit < native_top+fts5_supplement 时缩 native_top 给 fts5 留位。
        # 之前 limit=3 native_top=3 占满，fts5 supplement 被截 → fusion 退化成 native。
        total_budget = self._native_top + self._fts5_supplement
        if limit < total_budget:
            nt = min(self._native_top, max(1, limit - 1))
        else:
            nt = self._native_top
        supplement = [
            h for h in fts5_hits if h.slug not in seen
        ][:max(self._fts5_supplement, limit - nt)]

        # native top-N + fts5 独有 M，截到 limit
        return (native_hits[:nt] + supplement)[:limit]


__all__ = ["FusionBackend"]
