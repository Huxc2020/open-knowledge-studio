"""Contract tests for the knowledge map projection.

Two invariants matter more than the shape of the output:

* every relation edge states where it came from, and
* Concept / Strategy / Anti-Strategy never become the Wiki's taxonomy.
"""
import json
from pathlib import Path

import pytest

from knowledge_studio import mail_knowledge, store


@pytest.fixture
def kb(tmp_path):
    root = tmp_path / "kb"
    (root / "wiki").mkdir(parents=True)
    (root / "drafts").mkdir()
    return root


def write(root: Path, relative: str, frontmatter: str, body: str = "正文"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    store._atomic_write(path, f"---\n{frontmatter}---\n\n{body}\n")
    return path


def point(projection, path):
    for domain in projection["domains"]:
        for cluster in domain["clusters"]:
            for item in cluster["points"]:
                if item["path"] == path:
                    return item
    raise AssertionError(f"{path} not in projection")


def test_three_levels_come_from_area_and_tags(kb):
    write(kb, "wiki/a.md", 'title: "A"\narea: engineering\ntags: "engineering, view-layer"\nstatus: active\n')
    write(kb, "wiki/b.md", 'title: "B"\narea: engineering\ntags: "engineering, mail-protocol"\nstatus: active\n')
    write(kb, "wiki/c.md", 'title: "C"\narea: knowledge\ntags: "knowledge, recall"\nstatus: active\n')

    projection = mail_knowledge.knowledge_map(kb)
    domains = {domain["key"]: {cluster["key"] for cluster in domain["clusters"]} for domain in projection["domains"]}

    assert domains == {"engineering": {"view-layer", "mail-protocol"}, "knowledge": {"recall"}}
    assert projection["counts"]["points"] == 3
    assert projection["counts"]["clusters"] == 3


def test_domain_tag_does_not_create_a_hairball(kb):
    for index in range(6):
        write(kb, f"wiki/e{index}.md", f'title: "E{index}"\narea: engineering\ntags: "engineering"\nstatus: active\n')

    projection = mail_knowledge.knowledge_map(kb)
    assert projection["counts"]["relations"] == 0
    assert {cluster["key"] for domain in projection["domains"] for cluster in domain["clusters"]} == {"本域通用"}


def test_declared_relations_are_sourced_and_resolved(kb):
    write(kb, "wiki/target.md", 'title: "目标知识"\narea: knowledge\ntags: "knowledge, recall"\nstatus: active\n')
    write(
        kb,
        "wiki/source.md",
        'title: "来源知识"\narea: knowledge\ntags: "knowledge, skill-governance"\nstatus: active\n'
        "relations:\n  - type: depends_on\n    target: 目标知识\n",
    )

    item = point(mail_knowledge.knowledge_map(kb), "wiki/source.md")
    edge = item["relations"][0]
    assert edge["type"] == "depends_on"
    assert edge["type_label"] == "依赖 / 前置"
    assert edge["source"] == "frontmatter relations"
    assert edge["resolved"] is True
    assert edge["target_id"] == "wiki/target.md"
    assert edge["target_title"] == "目标知识"


def test_unknown_relation_type_and_dangling_target_are_flagged_not_remapped(kb):
    write(
        kb,
        "wiki/source.md",
        'title: "来源知识"\narea: knowledge\ntags: "knowledge, recall"\nstatus: active\n'
        "relations:\n  - type: inspired_by\n    target: 库外的东西\n",
    )

    edge = point(mail_knowledge.knowledge_map(kb), "wiki/source.md")["relations"][0]
    assert edge["type"] == "unknown"
    assert edge["type_label"] == "未识别关系"
    assert edge["raw_type"] == "inspired_by"
    assert edge["resolved"] is False
    assert edge["target_title"] == "库外的东西"
    assert edge["target_kind"] == "unresolved"


def test_shared_cluster_tag_becomes_a_sourced_relation(kb):
    write(kb, "wiki/x.md", 'title: "X"\narea: engineering\ntags: "engineering, ui, extra"\nstatus: active\n')
    write(kb, "wiki/y.md", 'title: "Y"\narea: knowledge\ntags: "knowledge, recall, extra"\nstatus: active\n')

    edge = point(mail_knowledge.knowledge_map(kb), "wiki/x.md")["relations"][0]
    assert edge["type"] == "related"
    assert edge["source"] == "shared tag: extra"
    assert edge["target_id"] == "wiki/y.md"


def test_governance_types_stay_attributes_not_taxonomy(kb):
    write(kb, "wiki/s.md", 'title: "S"\ntype: strategy\narea: engineering\ntags: "engineering, ui"\nstatus: active\n')
    write(kb, "wiki/c.md", 'title: "C"\ntype: concept\narea: engineering\ntags: "engineering, ui"\nstatus: active\n')
    write(kb, "wiki/a.md", 'title: "A"\ntype: anti-pattern\narea: engineering\ntags: "engineering, ui"\nstatus: active\n')

    projection = mail_knowledge.knowledge_map(kb)
    # Grouping still follows the domain; the three governance types never appear
    # as domains or clusters.
    assert [domain["key"] for domain in projection["domains"]] == ["engineering"]
    # 显示名与机器键分离：area 仍是机器键，label 供界面显示。
    assert [domain["label"] for domain in projection["domains"]] == ["研发工程"]
    all_clusters = {cluster["key"] for domain in projection["domains"] for cluster in domain["clusters"]}
    assert all_clusters == {"ui"}
    assert {item["label"] for item in projection["governance"]["types"]} == {"概念", "策略", "反策略"}
    assert all(item["count"] == 1 for item in projection["governance"]["types"])


def test_skill_boundary_is_policy_and_records_what_phase_one_does_not_do(kb):
    write(kb, "wiki/s.md", 'title: "S"\narea: engineering\ntags: "engineering, ui"\nstatus: active\n')

    boundary = mail_knowledge.knowledge_map(kb)["governance"]["skill_boundary"]
    assert boundary["kind"] == "policy"
    assert boundary["stage"] == "phase-1"
    assert any("不自动安装" in item for item in boundary["not_done"])
    assert any("wiki_refs" in item for item in boundary["allowed"])


def test_candidates_use_draft_keys_and_are_labelled(kb):
    write(kb, "drafts/c.md", 'title: "候选知识"\ndraft_type: strategy\ndraft_area: ai\nstatus: draft\n')

    item = point(mail_knowledge.knowledge_map(kb), "drafts/c.md")
    assert item["kind"] == "candidate"
    assert item["kind_label"] == "Candidate 候选"
    assert item["area"] == "ai"
    assert item["governance"]["type"] == "strategy"
    assert item["status_label"] == "草稿待审"
    assert item["skill"]["state"] == "candidate"
    assert item["wiki_refs"] == ["drafts/c.md"]


def test_archived_and_dropped_entries_are_reported_as_hidden(kb):
    write(kb, "wiki/live.md", 'title: "在库"\narea: engineering\ntags: "engineering, ui"\nstatus: active\n')
    write(kb, "wiki/gone.md", 'title: "已归档"\narea: engineering\ntags: "engineering, ui"\nstatus: archived\n')

    projection = mail_knowledge.knowledge_map(kb)
    assert projection["counts"]["points"] == 1
    assert projection["scope"]["hidden"] == 1


def test_projection_declares_where_each_level_comes_from(kb):
    write(kb, "wiki/live.md", 'title: "在库"\narea: engineering\ntags: "engineering, ui"\nstatus: active\n')

    projection = mail_knowledge.knowledge_map(kb)
    assert projection["scope"]["level_sources"] == [
        "条目自己声明的所属领域", "条目的首个标签", "一份已审核的 Wiki 或候选条目",
    ]
    # 口径可以改写，但「图是只读的、来源可追溯」这两条不能丢。
    why = projection["scope"]["why"]
    assert "不新增任何知识" in why and "不写入任何东西" in why and "来源" in why
    item = point(projection, "wiki/live.md")
    assert item["cluster_source"]


def test_scope_copy_leaks_no_protocol_words(kb):
    """普通读者看到的说明里不能出现内部字段名 —— 这是 R6 的硬要求。"""
    write(kb, "wiki/live.md", 'title: "在库"\narea: collaboration\ntags: "collaboration, checkpoint, ui"\nstatus: active\n')

    projection = mail_knowledge.knowledge_map(kb)
    scope = projection["scope"]
    visible = f"{scope['note']} {scope['why']} " + " ".join(scope["level_sources"])
    governance = " ".join(
        [projection["governance"]["note"]]
        + list(projection["governance"]["skill_boundary"]["allowed"])
        + list(projection["governance"]["skill_boundary"]["not_done"])
    )
    for word in ("frontmatter", "area", "tags[0]", "collaboration", "enabled"):
        assert word not in visible, word
        assert word not in governance, word


def test_every_time_source_key_has_a_human_label():
    """时间来源要显示人话；新增时间字段却忘了起名字，这里会先拦住。"""
    missing = [
        key for key in (*mail_knowledge.TIME_KEYS, "file_mtime")
        if key not in mail_knowledge.TIME_SOURCE_LABELS
    ]
    assert missing == [], f"这些时间字段还没有人话名字：{missing}"
    # 表里没有的 key 也不许把原始字段名漏出去。
    assert mail_knowledge.time_source_label("some_new_field") == "记录时间"
    assert mail_knowledge.time_source_label("updated_at") == "条目里记录的更新时间"


# ── 治理位：库能力保留，面板没有任何调用方 ──────────────────────────────
#
# 这些测试守的是数据安全，不是功能花样：
# 写进去的必须只有一个布尔字段，其他字节与正文必须原样保留，
# 越界路径和没有 frontmatter 的文件必须被拒绝，而不是被「顺手修好」。
# 注意：面板的写路径已于 2026-09-22 移除，这里是**库能力**的契约测试；
# 将来若重新暴露，必须走 CLI / Agent，而不是无鉴权的 HTTP 路由。

def test_toggle_writes_only_the_governance_bit_and_keeps_a_backup(kb):
    path = write(kb, "wiki/a.md", 'title: "A"\narea: engineering\ntags: "engineering, ui"\nstatus: active\n', "正文第一行\n正文第二行\n")
    before = path.read_bytes()

    result = mail_knowledge.set_enabled(kb, "wiki/a.md", False)

    after = path.read_text(encoding="utf-8")
    assert result["enabled"] is False
    assert result["previous_enabled"] is True
    assert result["changed"] is True
    assert "enabled: false" in after
    assert "正文第一行" in after and "正文第二行" in after
    assert 'title: "A"' in after and "status: active" in after
    backup = kb / result["backup"]
    assert backup.is_file()
    assert backup.read_bytes() == before


def test_toggle_is_reversible_byte_for_byte(kb):
    path = write(kb, "wiki/a.md", 'title: "A"\narea: engineering\ntags: "engineering, ui"\nstatus: active\n', "正文\n")
    original = path.read_text(encoding="utf-8")

    mail_knowledge.set_enabled(kb, "wiki/a.md", False)
    assert "enabled: false" in path.read_text(encoding="utf-8")
    mail_knowledge.set_enabled(kb, "wiki/a.md", True)
    restored = path.read_text(encoding="utf-8")

    assert "enabled: true" in restored
    # 去掉新增的那一行之后，其余内容必须与原始文件逐字相同
    strip = lambda text: [line for line in text.splitlines() if not line.strip().startswith("enabled:")]
    assert strip(restored) == strip(original)


def test_toggle_replaces_an_existing_enabled_line_instead_of_appending(kb):
    path = write(kb, "wiki/a.md", 'title: "A"\nenabled: false\narea: engineering\ntags: "engineering, ui"\nstatus: active\n')

    mail_knowledge.set_enabled(kb, "wiki/a.md", True)

    text = path.read_text(encoding="utf-8")
    assert text.count("enabled:") == 1
    assert "enabled: true" in text


def test_toggle_refuses_paths_outside_wiki_and_drafts(kb):
    outside = write(kb, "raw/secret.md", 'title: "raw"\narea: engineering\n')
    with pytest.raises(FileNotFoundError):
        mail_knowledge.set_enabled(kb, "raw/secret.md", False)
    assert "enabled" not in outside.read_text(encoding="utf-8")

    # 穿越到其他目录的真实文件：resolve 之后不在 wiki/ drafts/ 之内，必须拒绝
    with pytest.raises(FileNotFoundError):
        mail_knowledge.set_enabled(kb, "wiki/../raw/secret.md", False)
    assert "enabled" not in outside.read_text(encoding="utf-8")

    # 绝对路径直接拒绝
    with pytest.raises(ValueError):
        mail_knowledge.set_enabled(kb, str(outside), False)

    with pytest.raises(FileNotFoundError):
        mail_knowledge.set_enabled(kb, "wiki/missing.md", False)


def test_toggle_refuses_frontmatter_less_file_instead_of_inventing_one(kb):
    path = kb / "wiki" / "plain.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("没有 frontmatter 的正文\n", encoding="utf-8")

    with pytest.raises(ValueError):
        mail_knowledge.set_enabled(kb, "wiki/plain.md", False)
    assert path.read_text(encoding="utf-8") == "没有 frontmatter 的正文\n"


def test_projection_reports_pluggable_state_without_a_write_path(kb):
    write(kb, "wiki/on.md", 'title: "开"\ntype: strategy\narea: engineering\ntags: "engineering, ui"\nstatus: active\n')
    write(kb, "wiki/off.md", 'title: "关"\ntype: concept\narea: engineering\ntags: "engineering, ui"\nstatus: active\nenabled: false\n')

    projection = mail_knowledge.knowledge_map(kb)
    assert projection["counts"]["enabled"] == 1
    assert projection["counts"]["disabled"] == 1
    state = projection["governance"]["enabled_state"]
    assert state["field"] == "enabled"
    assert state["counts"] == {"enabled": 1, "disabled": 1}
    # 只读边界：payload 不许再宣告任何写端点或写保证 —— 面板已经不提供写路径，
    # 留一个 endpoint 在这里等于让页面替一个不存在的接口许愿。
    governance = projection["governance"]
    assert "toggle" not in governance
    assert "/api/" not in json.dumps(governance, ensure_ascii=False)
    assert [m["path"] for m in projection["governance"]["pluggable"]] == ["wiki/off.md", "wiki/on.md"]
    assert [m["enabled"] for m in projection["governance"]["pluggable"]] == [False, True]

    mail_knowledge.set_enabled(kb, "wiki/on.md", False)
    after = mail_knowledge.knowledge_map(kb)
    assert after["counts"]["enabled"] == 0
    assert after["counts"]["disabled"] == 2


def test_unmapped_machine_keys_never_become_group_titles(kb):
    """分组名与标签行同源：翻不出来的英文机器键不能当标题漏给读者。

    Regression guard: 域名与簇名各自用 `.get(key, key)` 回退，于是表外的英文键
    直接成了左栏 / 面包屑的标题，而同一条数据在 `tag_labels` 里已经被
    `tag_label` 挡掉了 —— 同一个词在两处受到两种待遇。
    """
    write(kb, "wiki/a.md",
          'title: "A"\narea: ghost-area\ntags: "ghost-area, ghost-cluster"\nstatus: active\n')

    projection = mail_knowledge.knowledge_map(kb)
    domain = projection["domains"][0]
    cluster = domain["clusters"][0]

    # 机器键留在 key 里（分组仍然稳定），给人看的一律是人话。
    assert domain["key"] == "ghost-area"
    assert cluster["key"] == "ghost-cluster"
    assert domain["label"] == "未分类"
    assert cluster["label"] == "未分组"
    # Same data, same treatment: the label row already dropped these keys.
    assert point(projection, "wiki/a.md")["tag_labels"] == []


def test_relation_count_matches_the_legend_it_sits_next_to(kb):
    """总数与图例同源：两者都只统计真正送出去的条目。

    Regression guard: the count was accumulated over every scanned entry while
    the legend walked only the kept ones, so any `limit` truncation made the two
    disagree — the header said 3 edges above a legend that added up to 2.
    """
    for index in range(3):
        write(
            kb,
            f"wiki/s{index}.md",
            f'title: "来源{index}"\narea: knowledge\ntags: "knowledge, recall"\nstatus: active\n'
            "relations:\n  - type: depends_on\n    target: 目标知识\n",
        )
    write(kb, "wiki/target.md", 'title: "目标知识"\narea: knowledge\ntags: "knowledge, recall"\nstatus: active\n')

    projection = mail_knowledge.knowledge_map(kb, limit=2)

    assert projection["counts"]["points"] == 2  # 确实被截断了，口径才有分歧的余地
    assert projection["counts"]["relations"] == sum(
        item["count"] for item in projection["relation_legend"]
    )


def test_toggle_ignores_fields_that_merely_start_with_enabled(kb):
    """`enabled_by:` 是别的字段，不是治理位。

    Regression guard: the ownership check used ``startswith("enabled")``, so
    ``enabled_by: reviewer`` was read as the governance bit and
    ``previous_enabled`` took its value — turning `changed` into a lie.
    """
    path = write(kb, "wiki/a.md",
                 'title: "A"\nenabled_by: reviewer\narea: engineering\ntags: "engineering, ui"\nstatus: active\n')

    result = mail_knowledge.set_enabled(kb, "wiki/a.md", False)

    # 没有治理位就是默认开启；`enabled_by` 的值不代表开关状态。
    assert result["previous_enabled"] is True
    assert result["changed"] is True
    text = path.read_text(encoding="utf-8")
    assert "enabled_by: reviewer" in text  # 原样保留
    assert "enabled: false" in text
