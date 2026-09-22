"""Read-only knowledge map projection over the curated Wiki.

The map answers one question at three zoom levels:

    level 1  knowledge domain   (frontmatter ``area``)
    level 2  knowledge cluster  (first frontmatter ``tag``)
    level 3  knowledge point    (one Markdown file in ``wiki/`` or ``drafts/``)

Everything is derived from real files. Nothing is invented:

* A relation edge exists only when the entry declares it in ``relations``, or
  when two entries share a tag. Every edge carries ``source`` so the UI can show
  where it came from.
* Concept / Strategy / Anti-Strategy are **governance attributes** of an entry,
  never a taxonomy of the Wiki. Grouping is always by domain.
* Skill packaging state is derived from frontmatter and reported as such, with
  the derivation spelled out, because phase 1 has no install or publish path.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date, datetime, time, timezone
from pathlib import Path

from knowledge_studio import store

SCHEMA = "mail.knowledge-map.v1"

#: Relation vocabulary allowed in phase 1 (design 03). Unknown declarations are
#: preserved verbatim and flagged instead of being silently remapped.
#: ``color_key`` drives the legend/palette in the panel; it is stable per kind.
RELATION_TYPES = {
    "related": "相关",
    "depends_on": "依赖 / 前置",
    "supports": "支持 / 依据",
    "contrast": "对比 / 冲突",
    "applies_to": "应用于",
    "supersedes": "更新 / 替代",
}

#: Human labels for the knowledge domains seen in real knowledge bases. The
#: ``area`` value itself stays the machine key; the panel groups by ``area``.
DOMAIN_LABELS = {
    "collaboration": "团队与协作",
    "engineering": "研发工程",
    "knowledge": "知识管理",
    "ai": "Agent 与 AI",
    "product": "产品与设计",
    "process": "流程与规范",
    "tooling": "工具与平台",
    "research": "研究与调研",
}

#: Cluster labels derive from frontmatter tags; these cover the tags we ship.
#: 这份表同时被 ``TAG_LABELS`` 复用，所以它兼顾两个职责：
#: ① 簇名（会渲染成左栏 / 面包屑的一级标题，漏一个就把机器键漏给读者）；
#: ② 知识点的标签行（``tag_label`` 对表外英文键返回空串，漏一个就静默少一行）。
#: 协议自身的词表必须全在此列，别再让 ``mail-protocol`` 这种键裸奔。
CLUSTER_LABELS = {
    "checkpoint": "人类检查点",
    "ui": "界面与交互",
    "anti-pattern": "反模式",
    "projection": "投影与有损",
    "protocol": "协作协议",
    "mail-protocol": "邮件协议",
    "view-layer": "视图层",
    "evidence": "证据与来源",
    "review": "审核与治理",
    "handoff": "交接",
    "memory": "记忆",
    "recall": "召回",
    "sync": "同步",
    "skill": "Skill 治理",
    "skill-governance": "Skill 治理",
    "human-in-the-loop": "人类介入",
    "session": "会话",
    "honesty": "如实呈现",
    "receipt": "回执",
    "identity": "身份",
    "wiki": "Wiki 条目",
}

#: Wiki 标签给读者看的人话名字。知识域与知识簇共用一套口径，
#: 合成一张表，避免同一个词在两处显示成两个名字。
TAG_LABELS = {**DOMAIN_LABELS, **CLUSTER_LABELS}


def tag_label(tag) -> str:
    """把标签翻成人话。推不出来的英文机器键不硬塞给读者，返回空串。"""
    text = str(tag or "").strip()
    if not text:
        return ""
    if text in TAG_LABELS:
        return TAG_LABELS[text]
    # 中文标签是人为写的，原样保留；纯英文残留多半是内部键，不展示。
    return text if any("\u4e00" <= ch <= "\u9fff" for ch in text) else ""


#: Governance knowledge types. They serve the Skills layer only.
GOVERNANCE_TYPES = {
    "concept": ("概念", "这是什么？"),
    "strategy": ("策略", "推荐怎么做？"),
    "anti-pattern": ("反策略", "哪些做法应该避免？"),
}

#: Phase 1 publishes no executable capability. This block is policy, not data.
SKILL_BOUNDARY = {
    "kind": "policy",
    "stage": "phase-1",
    "allowed": [
        "Skill 通过 wiki_refs 引用已审核知识，不复制知识正文",
        "只有通过 Human Review 的 Wiki 条目可以作为引用来源",
        "概念 / 策略 / 反策略只用于 Skills 治理，不作 Wiki 分类",
    ],
    "not_done": [
        "不自动安装 Skill",
        "不自动发布或更新 Skill Package",
        "不做 Marketplace、不做复杂依赖解析",
        "面板不做启用 / 停用：治理位只展示，不写盘",
    ],
    "next_gate": "Skill Candidate → Human Review → Skill Package 仍需人工判断。",
}

TIME_KEYS = (
    "updated_at", "updated", "modified_at", "ingested_at",
    "human_reviewed_at", "created",
)

#: 「这个时间是从哪来的」——把内部字段名翻成维护者也能直接读懂的人话。
#: 每个 TIME_KEYS 成员都必须有名字，测试会盯着这张表不许漏。
TIME_SOURCE_LABELS = {
    "updated_at": "条目里记录的更新时间",
    "updated": "条目里记录的更新时间",
    "modified_at": "条目里记录的更新时间",
    "ingested_at": "收录进知识库的时间",
    "human_reviewed_at": "人工审核通过的时间",
    "created": "条目创建时间",
    "file_mtime": "文件最后修改时间",
}


def time_source_label(key) -> str:
    """时间来源的人话名字；表里没有的一律退成中性的「记录时间」，不外漏字段名。"""
    return TIME_SOURCE_LABELS.get(str(key or "").strip(), "记录时间")

VISIBLE_INVISIBLE_STATUS = {"dropped", "superseded", "retired", "archived"}


def _plain(value: str, limit: int = 240) -> str:
    text = str(value or "")
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!?(?:\[([^\]]+)\]\([^)]*\))", r"\1", text)
    text = re.sub(r"[*_~]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _parse_time(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, str) and value.strip():
        raw = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            try:
                parsed = datetime.combine(date.fromisoformat(raw), time.min)
            except ValueError:
                return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _event_time(meta: dict, path: Path) -> tuple[str, str]:
    for key in TIME_KEYS:
        parsed = _parse_time(meta.get(key))
        if parsed is not None:
            return parsed.isoformat(), key
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), "file_mtime"


def _is_visible(meta: dict) -> bool:
    status = str(meta.get("status") or "active").strip().lower()
    archived = meta.get("archived") is True or str(meta.get("archived") or "").strip().lower() in {"1", "true", "yes"}
    return not archived and status not in VISIBLE_INVISIBLE_STATUS


def _as_list(value) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,\n]", value) if item.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _title(meta: dict, body: str, path: Path) -> str:
    for key in ("title", "name"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return _plain(value.strip(), 140)
    for line in body.splitlines():
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match and match.group(1).strip():
            return _plain(match.group(1), 140)
    return _plain(path.stem.replace("-", " ").replace("_", " "), 140) or "未命名知识"


def _summary(meta: dict, body: str) -> str:
    for key in ("summary", "description", "abstract"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return _plain(value)
    paragraph = []
    for line in body.splitlines():
        clean = line.strip()
        if not clean:
            if paragraph:
                break
            continue
        if clean.startswith("#") or clean.startswith("---") or clean.startswith(">"):
            continue
        paragraph.append(clean)
    return _plain(" ".join(paragraph)) or "这份知识还没有可显示的摘要，打开详情查看原文。"


def _relations(meta: dict) -> list[dict]:
    raw = meta.get("relations")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = [item.strip() for item in re.split(r"[,\n]", raw) if item.strip()]
    if not isinstance(raw, (list, tuple)):
        return []
    relations = []
    for item in raw:
        if isinstance(item, dict):
            kind = str(item.get("type") or item.get("relation") or "related").strip().lower()
            target = str(item.get("target") or item.get("to") or item.get("path") or "").strip()
            note = str(item.get("note") or "").strip()
        else:
            kind, _, target = str(item).partition(":")
            kind = kind.strip().lower() or "related"
            target = target.strip()
            note = ""
        if not target:
            continue
        known = kind in RELATION_TYPES
        relations.append({
            "type": kind if known else "unknown",
            "type_label": RELATION_TYPES.get(kind, "未识别关系"),
            "color_key": kind if known else "unknown",
            "raw_type": kind,
            "target": target,
            "note": note,
            "source": "frontmatter relations",
            "source_label": "知识条目自己声明的关系",
            "resolved": False,
        })
    return relations


def _scan(root: Path, relative: str) -> list[Path]:
    base = root / relative
    if not base.is_dir():
        return []
    return sorted(
        path for path in base.rglob("*.md")
        if path.is_file() and not path.name.startswith("_")
    )


def _skill_state(meta: dict, kind: str, governance_type: str) -> dict:
    """Derive Skill packaging state, and say how it was derived."""
    package = str(meta.get("skill_package") or "").strip()
    if package:
        return {"state": "packaged", "label": "已成包", "source": "frontmatter skill_package", "target": package}
    flagged = meta.get("skill_candidate")
    flagged = flagged is True or str(flagged).strip().lower() in {"1", "true", "yes"}
    if flagged or (kind == "candidate" and governance_type in GOVERNANCE_TYPES):
        return {"state": "candidate", "label": "候选（待 Human Review）", "source": "drafts/ 中的治理类知识" if kind == "candidate" else "frontmatter skill_candidate", "target": ""}
    return {"state": "none", "label": "尚未进入治理流程", "source": "", "target": ""}


def knowledge_map(root: Path, limit: int = 400) -> dict:
    """Project wiki/drafts into domain → cluster → point with sourced edges."""
    limit = max(1, min(int(limit or 400), 2000))
    paths: list[tuple[Path, str]] = [(path, "wiki") for path in _scan(root, "wiki")]
    paths += [(path, "candidate") for path in _scan(root, "drafts")]

    points: list[dict] = []
    hidden = 0
    for path, kind in paths:
        parsed = store.parse_wiki_file(path) or {}
        body = str(parsed.pop("body", "") or "")
        if not _is_visible(parsed):
            hidden += 1
            continue
        updated_at, timestamp_source = _event_time(parsed, path)
        tags = _as_list(parsed.get("tags"))
        # Candidates produced by the dreaming pipeline carry draft_* keys; they
        # describe the same two things, so they resolve into the same shape.
        area = str(parsed.get("area") or parsed.get("domain") or parsed.get("draft_area") or "").strip() or "未分类"
        governance_type = str(parsed.get("type") or parsed.get("draft_type") or "").strip().lower()
        if governance_type not in GOVERNANCE_TYPES:
            governance_type = "unclassified"
        enabled = parsed.get("enabled")
        enabled = True if enabled is None else (enabled is True or str(enabled).strip().lower() in {"1", "true", "yes"})
        # A cluster has to say something the domain does not.  The first tag that
        # differs from the domain name is the most specific honest grouping; when
        # every tag merely restates the domain, the entry is genuinely generic.
        cluster_tag = next((tag for tag in tags if tag.lower() != area.lower()), "")
        if cluster_tag:
            cluster, cluster_source = cluster_tag, f"首个不同于知识域的标签「{cluster_tag}」"
        elif tags:
            cluster, cluster_source = "本域通用", "标签与知识域同名，无法细分"
        else:
            cluster, cluster_source = "未分组", "这条知识没有标签"
        points.append({
            "id": path.relative_to(root).as_posix(),
            "path": path.relative_to(root).as_posix(),
            "kind": "wiki" if kind == "wiki" else "candidate",
            "kind_label": "已审核 Wiki" if kind == "wiki" else "Candidate 候选",
            "title": _title(parsed, body, path),
            "summary": _summary(parsed, body),
            "body": body[:12000],
            "body_truncated": len(body) > 12000,
            "area": area,
            "cluster": cluster,
            "cluster_source": cluster_source,
            "tags": tags[:12],
            # 给读者看的标签沿用同一套人话口径；翻不出来的英文机器键在这里就挡掉，
            # 页面拿到的是可以直接显示的清单。与独立 Wiki 页同源，两处不再各翻一套。
            "tag_labels": [label for label in (tag_label(tag) for tag in tags[:12]) if label],
            "status": str(parsed.get("status") or "active"),
            "status_label": {
                "active": "可复用",
                "pending": "待审核",
                "provisional": "待确认",
                "stale": "需要更新",
                "draft": "草稿待审",
            }.get(str(parsed.get("status") or "active"), str(parsed.get("status") or "active")),
            "updated_at": updated_at,
            "timestamp_source": timestamp_source,
            # 界面只显示人话；原始键名（created / updated_at / file_mtime）留在
            # payload 里供排查，不进默认阅读路径。前端与独立 Wiki 页共用这一份。
            "timestamp_source_label": time_source_label(timestamp_source),
            "governance": {
                "type": governance_type,
                "type_label": GOVERNANCE_TYPES.get(governance_type, ("未分类", ""))[0],
                "type_question": GOVERNANCE_TYPES.get(governance_type, ("", "这条知识还没有治理分类"))[1],
                "enabled": enabled,
                "pinned": bool(parsed.get("pinned") is True or str(parsed.get("pinned")).strip().lower() in {"1", "true", "yes"}),
                "importance": _as_float(parsed.get("importance")),
                "confidence": _as_float(parsed.get("confidence")),
            },
            "skill": _skill_state(parsed, kind, governance_type),
            "wiki_refs": [path.relative_to(root).as_posix()],
            "relations": _relations(parsed),
            "metadata": {key: str(value) for key, value in parsed.items() if key not in {"file_path", "slug"}},
        })

    by_id = {point["id"]: point for point in points}
    by_stem = {}
    by_title = {}
    for point in points:
        by_stem.setdefault(Path(point["id"]).stem.lower(), point)
        by_title.setdefault(point["title"].lower(), point)

    def resolve(target: str):
        raw = str(target or "").strip()
        if not raw:
            return None
        if raw in by_id:
            return by_id[raw]
        if raw.lower() in by_title:
            return by_title[raw.lower()]
        return by_stem.get(Path(raw).stem.lower())

    # Tag index drives derived "shared tag" edges.  The domain tag is skipped:
    # grouping already expresses it, and letting it emit edges would connect
    # every entry in a domain to every other one.
    tag_index: dict[str, list[str]] = {}
    for point in points:
        for tag in point["tags"]:
            if tag.lower() == point["area"].lower():
                continue
            tag_index.setdefault(tag, []).append(point["id"])

    for point in points:
        edges = []
        seen = set()
        for relation in point["relations"]:
            match = resolve(relation["target"])
            relation = dict(relation)
            relation["resolved"] = match is not None
            if match:
                relation["target_id"] = match["id"]
                relation["target_title"] = match["title"]
                relation["target_kind"] = match["kind"]
            else:
                relation["target_id"] = ""
                relation["target_title"] = relation["target"]
                relation["target_kind"] = "unresolved"
            key = (relation["type"], relation["target_id"] or relation["target"])
            if key in seen:
                continue
            seen.add(key)
            edges.append(relation)
        for tag in point["tags"]:
            if tag == point["cluster"]:
                # Entries already sit together in this cluster; an edge would
                # only restate the grouping.
                continue
            for other in tag_index.get(tag, []):
                if other == point["id"]:
                    continue
                key = ("related", other)
                if key in seen:
                    continue
                seen.add(key)
                edges.append({
                    "type": "related",
                    "type_label": RELATION_TYPES["related"],
                    "color_key": "related",
                    "raw_type": "related",
                    "target": other,
                    "note": "",
                    "source": f"shared tag: {tag}",
                    "source_label": f"和另一条知识共享标签「{tag}」",
                    "resolved": True,
                    "target_id": other,
                    "target_title": by_id[other]["title"],
                    "target_kind": by_id[other]["kind"],
                })
        truncated_edges = len(edges) > 12
        point["relations"] = edges[:12]
        point["relations_truncated"] = truncated_edges

    truncated = len(points) > limit
    kept = points[:limit]
    kept_ids = {point["id"] for point in kept}

    domains: dict[str, dict] = {}
    for point in kept:
        domain = domains.setdefault(point["area"], {
            "id": point["area"],
            "key": point["area"],
            # 与 tag_label 同源：翻不出来的英文机器键不能当分组名漏给读者。
            "label": tag_label(point["area"]) or "未分类",
            "count": 0,
            "clusters": {},
        })
        domain["count"] += 1
        cluster = domain["clusters"].setdefault(point["cluster"], {
            "id": point["cluster"],
            "key": point["cluster"],
            "label": tag_label(point["cluster"]) or "未分组",
            "count": 0,
            "points": [],
        })
        cluster["count"] += 1
        cluster["points"].append(point)

    def sort_key(item):
        return (0 if item["label"] != "未分类" else 1, -item["count"], item["label"])

    domain_list = []
    for domain in sorted(domains.values(), key=sort_key):
        clusters = sorted(domain["clusters"].values(), key=sort_key)
        domain_list.append({
            "id": domain["id"],
            "key": domain["key"],
            "label": domain["label"],
            "count": domain["count"],
            "clusters": clusters,
        })

    def governance_member(point: dict) -> dict:
        return {
            "path": point["path"],
            "title": point["title"],
            "kind_label": point["kind_label"],
            "enabled": point["governance"]["enabled"],
            "status_label": point["status_label"],
        }

    governance = []
    for type_id, (label, question) in GOVERNANCE_TYPES.items():
        rows = [point for point in kept if point["governance"]["type"] == type_id]
        governance.append({
            "id": type_id,
            "label": label,
            "question": question,
            "count": len(rows),
            "enabled": sum(1 for point in rows if point["governance"]["enabled"]),
            "disabled": sum(1 for point in rows if not point["governance"]["enabled"]),
            "members": [governance_member(point) for point in rows],
            "paths": [point["path"] for point in rows],
        })
    unclassified = [point["path"] for point in kept if point["governance"]["type"] == "unclassified"]
    pluggable = [
        governance_member(point) for point in kept
        if point["governance"]["type"] in GOVERNANCE_TYPES
    ]

    return {
        "schema": SCHEMA,
        "scope": {
            "knowledge_base": str(root),
            "points_total": len(points),
            "points_returned": len(kept),
            "limit": limit,
            "truncated": truncated or len(points) != len(kept_ids),
            "hidden": hidden,
            "note": (
                "这是 Wiki 的只读关系视图，每条关系都标了来源；"
                "这里不包含原始素材和 Agent 的执行过程。"
            ),
            "why": (
                "知识图只是把 Wiki 里已经写好的关系画出来，不新增任何知识。"
                "最外圈是知识域，中间是知识簇，最里面每一条都是一份已审核的 Wiki 或候选条目。"
                "每条连线都标了来源。这里不含原始素材，也不含 Agent 的执行过程。"
                "图本身不写入任何东西；能动的只有治理层的启用 / 停用开关。"
            ),
            "level_sources": ["条目自己声明的所属领域", "条目的首个标签", "一份已审核的 Wiki 或候选条目"],
        },
        "counts": {
            "domains": len(domain_list),
            "clusters": sum(len(domain["clusters"]) for domain in domain_list),
            "points": len(kept),
            # 与 relation_legend 同源：两边都只统计真正送出去的 kept 条目。
            # 否则 `limit` 截断时总数会把被截掉的关系算进去，与图例各说一套。
            "relations": sum(len(point["relations"]) for point in kept),
            "reviewed": sum(1 for point in kept if point["kind"] == "wiki"),
            "candidates": sum(1 for point in kept if point["kind"] == "candidate"),
            "skill_candidates": sum(1 for point in kept if point["skill"]["state"] == "candidate"),
            "skill_packaged": sum(1 for point in kept if point["skill"]["state"] == "packaged"),
            "enabled": sum(1 for point in kept if point["governance"]["enabled"]),
            "disabled": sum(1 for point in kept if not point["governance"]["enabled"]),
            "governance_typed": sum(1 for point in kept if point["governance"]["type"] != "unclassified"),
        },
        "domains": domain_list,
        "relation_legend": [
            {
                "id": kind,
                "label": label,
                "color_key": kind,
                "count": sum(
                    1
                    for domain in domain_list
                    for cluster in domain["clusters"]
                    for point in cluster["points"]
                    for rel in point["relations"]
                    if rel["type"] == kind
                ),
            }
            for kind, label in RELATION_TYPES.items()
        ] + [
            {
                "id": "unknown",
                "label": "未识别关系",
                "color_key": "unknown",
                "count": sum(
                    1
                    for domain in domain_list
                    for cluster in domain["clusters"]
                    for point in cluster["points"]
                    for rel in point["relations"]
                    if rel["type"] == "unknown"
                ),
            }
        ],
        "governance": {
            "note": (
                "概念 / 策略 / 反策略说的是「这条经验该怎么用」，"
                "不改变知识本身的归类；上方的分组只看知识域。"
            ),
            "types": governance,
            "pluggable": pluggable,
            "unclassified": unclassified,
            "skill_boundary": SKILL_BOUNDARY,
            "toggle": {
                "endpoint": "/api/mail/knowledge/toggle",
                "field": TOGGLE_FIELD,
                "writes": "只改这一个开关位；正文与其他字段原样不动",
                "safety": f"写前整字节备份到 {BACKUP_DIR.as_posix()}/，再用临时文件原子替换",
                "counts": {
                    "enabled": sum(1 for point in kept if point["governance"]["enabled"]),
                    "disabled": sum(1 for point in kept if not point["governance"]["enabled"]),
                },
            },
        },
    }


# ── 治理位：库能力保留，面板不再有写路径 ──────────────────────────────
#
# 边界变更（2026-09-22，取代 Owner 2026-09-19 的「面板唯一写路径」裁定）：
#   * 原因：`enabled` 位当时**没有任何消费方** —— recall / store / skill / hook 都不读它，
#     而面板文案据此声称「停用后不再被 Skill 层启用」，等于许诺一个没实现的下游效果。
#     一个只读观察面也不该是唯一能改写知识库文件的入口。
#   * 因此移除：面板的启用 / 停用开关、`/api/mail/knowledge/toggle` 路由，
#     以及 SKILL_BOUNDARY 里与之对应的那条声明。
#   * `set_enabled()` 保留为库能力（路径限于 wiki/ 与 drafts/、字节级备份、原子替换），
#     但它当前**没有生产调用方**；将来若要暴露，必须走 CLI / Agent，而不是无鉴权的 HTTP 路由。
#   * 每次写入前把原文件整字节备份到 <KB>/.oks/knowledge-backups/，
#     再用临时文件 + os.replace 原子替换；失败不留下半截文件。
#   * 没有 frontmatter 的条目直接拒绝，不做「帮你补一个」的猜测。

TOGGLE_FIELD = "enabled"
BACKUP_DIR = Path(".oks") / "knowledge-backups"
_ENABLED_LINE = re.compile(r"^([ \t]*)enabled[ \t]*:.*$", re.MULTILINE)


def knowledge_path(root: Path, relative: str) -> Path:
    """Resolve a knowledge path, refusing anything outside wiki/ or drafts/."""
    if not relative or Path(relative).is_absolute():
        raise ValueError("知识路径必须是指向 wiki/ 或 drafts/ 的相对路径")
    candidate = (root / relative).resolve()
    allowed = [(root / "wiki").resolve(), (root / "drafts").resolve()]
    if (
        candidate.suffix.lower() != ".md"
        or not candidate.is_file()
        or not any(candidate.is_relative_to(base) for base in allowed)
    ):
        raise FileNotFoundError("这条知识不在 wiki/ 或 drafts/ 下，无法读写")
    return candidate


def _backup(root: Path, relative: str, payload: bytes) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    flat = relative.replace("\\", "/").replace("/", "__")
    target = root / BACKUP_DIR / f"{flat}.{stamp}.bak"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


def set_enabled(root: Path, relative: str, enabled: bool) -> dict:
    """Flip the governance ``enabled`` bit of one knowledge entry.

    Kept as a library capability; since 2026-09-22 it has **no production caller** —
    the panel's ``/api/mail/knowledge/toggle`` route was removed along with the UI
    switch, because nothing in the system reads this bit.

    Raises ``FileNotFoundError`` for paths outside wiki/drafts and ``ValueError``
    when the file has no frontmatter block to write into.
    """
    path = knowledge_path(root, relative)
    original = path.read_bytes()
    try:
        text = original.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("这个文件不是 UTF-8，拒绝改写") from exc
    if not text.startswith("---"):
        raise ValueError("这条知识没有 frontmatter，拒绝硬造一个治理字段")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("这条知识的 frontmatter 不完整，拒绝改写")

    front = parts[1]
    previous = None
    for line in front.splitlines():
        stripped = line.strip()
        key, separator, value = stripped.partition(":")
        # 字段名必须精确相等：`startswith("enabled")` 会把 `enabled_by:` 也认成
        # 治理位，于是 previous_enabled 读的是别的字段的值，changed 跟着错。
        if separator and key.strip().lower() == TOGGLE_FIELD:
            previous = value.strip().strip('"').strip("'")
            break
    previous_enabled = True if previous is None else previous.strip().lower() in {"1", "true", "yes", "on"}

    want = "true" if enabled else "false"
    if _ENABLED_LINE.search(front):
        new_front = _ENABLED_LINE.sub(lambda m: f"{m.group(1)}{TOGGLE_FIELD}: {want}", front, count=1)
    else:
        newline = "\r\n" if "\r\n" in front else "\n"
        padded = front if front.endswith(("\n", "\r")) else front + newline
        new_front = f"{padded}{TOGGLE_FIELD}: {want}{newline}"

    updated = f"---{new_front}---{parts[2]}"
    backup = _backup(root, relative, original)

    handle, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".oks-toggle-", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(updated.encode("utf-8"))
        os.replace(temp_name, path)
    except OSError:
        Path(temp_name).unlink(missing_ok=True)
        raise

    return {
        "schema": "mail.knowledge-toggle.v1",
        "path": relative,
        "enabled": bool(enabled),
        "previous_enabled": previous_enabled,
        "changed": previous_enabled != bool(enabled),
        "field": TOGGLE_FIELD,
        "backup": backup.relative_to(root).as_posix(),
        "note": (
            "只改了 frontmatter 的 enabled 治理位；正文与其他字段原样保留。"
            "原文件已整字节备份，可据此回滚。"
        ),
    }

