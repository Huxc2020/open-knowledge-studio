/* OKS Mail Phase 1 · 协作观察面板
 * 版式照设计图：左栏导航 + 关系网络图 + 右侧详情面板。
 * 原则：文件即真相 / 移交而非代办 / 消息是数据不是指令（全部文本经 textContent，
 * 绝不内插 HTML）。acknowledged 永不表述为「已完成」。
 */
const $ = (id) => document.getElementById(id);
const SVG_NS = 'http://www.w3.org/2000/svg';
const state = {
  timeline: null, status: null, knowledge: null, guide: null,
  // 默认只给「关键协作」：把成堆的「已确认收到」回执收进筛选按钮里，
  // 首屏留给真正推进了事情的那些记录。想做审计时点「全部事实」即可。
  tab: 'timeline', scope: 'key', kmScope: 'all',
  path: [], depth: 2, zoom: 1, panX: 0, panY: 0, pathKey: '',
  showCounts: true, showSource: true,
  hiddenRel: new Set(), search: '',
  selNode: null, selPoint: null,
};
const DOMAIN_COLORS = ['--n-blue', '--n-violet', '--n-orange', '--n-green', '--n-pink', '--n-teal', '--n-indigo', '--n-slate'];
const REL_COLORS = {
  related: '--n-orange', depends_on: '--n-violet', supports: '--n-blue',
  contrast: '--n-pink', applies_to: '--n-green', supersedes: '--n-teal', unknown: '--n-slate',
};
const AGENT_COLORS = ['--n-blue', '--n-violet', '--n-orange', '--n-green', '--n-pink', '--n-teal'];
const VERIFY_LABELS = { verified: '已验证', observed: '已观察到', unverified: '未验证' };
const cvar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#94a3b8';

/* ── 小工具 ── */
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}
function fmtTime(iso, withDate) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d)) return String(iso);
  const p = (n) => String(n).padStart(2, '0');
  const hm = `${p(d.getHours())}:${p(d.getMinutes())}`;
  return withDate ? `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${hm}` : hm;
}
function fmtDay(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return '';
  return `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`;
}
const p2 = (n) => String(n).padStart(2, '0');
/* 两个真实时间之间隔了多久。负数返回空串：宁可什么都不显示，
   也不要让阶梯出现「-3 小时」这种自相矛盾的间隔。不足 1 分钟同样返回空串，
   因为「+0 分」只是噪声，不是信息。 */
function fmtDelta(fromIso, toIso) {
  const a = new Date(fromIso), b = new Date(toIso);
  if (isNaN(a) || isNaN(b)) return '';
  const mins = Math.floor((b - a) / 60000);
  if (!(mins >= 1)) return '';
  if (mins < 60) return `${mins} 分`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小时`;
  const days = Math.floor(hours / 24);
  return hours % 24 ? `${days} 天 ${hours % 24} 小时` : `${days} 天`;
}
function toast(msg) {
  const t = $('toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._h);
  toast._h = setTimeout(() => { t.hidden = true; }, 3000);
}
function copyText(text, okMsg) {
  const done = () => toast(okMsg || '已复制');
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
  } else fallbackCopy(text, done);
}
function fallbackCopy(text, done) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select();
  try { document.execCommand('copy'); done(); } catch (e) { toast('复制失败，请手动选择文本'); }
  ta.remove();
}
async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error('HTTP ' + res.status);
  return res.json();
}
function svgEl(tag, attrs) {
  const n = document.createElementNS(SVG_NS, tag);
  for (const k in attrs) n.setAttribute(k, attrs[k]);
  return n;
}
function textWidth(s, size) {
  const per = size * 0.62;
  let w = 0;
  for (const ch of String(s)) w += /[\u3000-\u9fff\uff00-\uffef]/.test(ch) ? size : per;
  return w;
}

/* ── 页签与左栏 ── */
document.querySelectorAll('.rail-tab').forEach((tab) => {
  tab.addEventListener('click', () => switchTab(tab.dataset.tab));
});
function switchTab(which) {
  state.tab = which;
  document.querySelectorAll('.rail-tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === which));
  document.querySelectorAll('.rail-pane').forEach((p) => { p.hidden = p.dataset.pane !== which; });
  $('view-timeline').hidden = which !== 'timeline';
  $('view-knowledge').hidden = which !== 'knowledge';
  $('pageTitle').textContent = which === 'timeline' ? '协作时间线' : 'Wiki 知识图';
  if (which === 'knowledge') { resizeGraph(); }
  else closeDetail();
}
$('refresh').addEventListener('click', () => boot().catch((e) => { $('runtimeText').textContent = '刷新失败：' + e.message; }));
$('detailClose').addEventListener('click', closeDetail);

/* ── 助手与连接 ── */
function renderAgents(status) {
  const box = $('agentAvatars');
  box.replaceChildren();
  const agents = Array.isArray(status.agents) ? status.agents : [];
  const railBox = $('railAgents');
  railBox.replaceChildren();
  if (!agents.length) {
    box.appendChild(el('p', 'muted small', '本机知识库里还没有任何助手 Session 档案。接入第一位助手后，这里会出现它的协作痕迹。'));
  }
  agents.forEach((a, i) => {
    const id = String(a.agent_id || '').replace(/^@/, '');
    const st = a.verification_status in VERIFY_LABELS ? a.verification_status : 'unverified';
    const chip = el('div', 'avatar-chip');
    const av = el('span', 'av', (id || '?').slice(0, 1).toUpperCase());
    av.style.background = cvar(AGENT_COLORS[i % AGENT_COLORS.length]);
    chip.appendChild(av);
    const who = el('div');
    who.appendChild(el('b', null, id || '未知身份'));
    const bits = [];
    if (a.session_count) bits.push(a.session_count + ' 个 Session');
    if (Array.isArray(a.machine_ids) && a.machine_ids.length) bits.push(a.machine_ids.length + ' 台机器');
    bits.push(VERIFY_LABELS[st]);
    who.appendChild(el('small', null, bits.join(' · ')));
    chip.appendChild(who);
    const dot = el('span', 'av-state');
    dot.style.background = st === 'verified' ? cvar('--ok') : st === 'observed' ? cvar('--n-blue') : cvar('--n-slate');
    chip.appendChild(dot);
    box.appendChild(chip);

    const item = el('button', 'rail-item');
    item.type = 'button';
    item.appendChild(el('span', null, id));
    item.appendChild(el('em', null, VERIFY_LABELS[st]));
    railBox.appendChild(item);
  });
  const machines = status.machine_count ?? (status.machines || []).length;
  $('liveBadge').textContent = agents.length ? `${agents.length} 位成员 · ${machines} 台机器` : '尚无成员';
}
$('copyGuide').addEventListener('click', () => {
  if (state.guide) copyText(state.guide, '接入说明已复制，粘贴到宿主对话区即可让 Agent 接入');
  else copyText('OKS Mail 接入说明（未能读取本地 connection-guide，请让 Agent 运行 oks mail 的接入安装流程）', '已复制兜底说明');
});

/* ── 协作时间线 ── */
document.querySelectorAll('[data-scope]').forEach((b) => {
  b.addEventListener('click', () => {
    state.scope = b.dataset.scope;
    document.querySelectorAll('[data-scope]').forEach((x) => x.classList.toggle('active', x === b));
    renderTimeline();
  });
});
function visibleNodes() {
  const all = (state.timeline && state.timeline.nodes) || [];
  if (state.scope === 'key') return all.filter((n) => n.key_action);
  if (state.scope === 'ack') return all.filter((n) => n.action_key === 'ack');
  return all;
}
/* ── 任务阶段：骨架固定，四阶段永远都在 ──
   状态 / 时间 / 证据都只来自真实事件；未到达的阶段不显示时间，
   因为「还没走到」和「没有证据」是两件事，不能混成一个。 */
const STAGE_STATUS_LABEL = { done: '已完成', active: '进行中', pending: '未到达' };
function shortId(id) {
  const t = String(id || '');
  return t.length > 14 ? t.slice(0, 10) + '…' : t;
}
function renderStages() {
  const data = state.timeline;
  const box = $('stageLadder');
  if (!box || !data) return;
  const stages = data.stages || [];
  box.replaceChildren();
  $('stageNote').textContent = data.stages_coherent === false
    // 后端保证阶段时间递增；万一没有，宁可明说也不要画一条倒着走的箭头。
    ? '阶段时间出现了倒序：这张阶梯暂时不能按箭头顺序读，请以下面的时间线为准。'
    : (data.stages_note || '');
  $('stageWhy').textContent = data.stages_why || '';
  const done = stages.filter((s) => s.status === 'done').length;
  $('stageCounts').textContent = stages.length ? `已到达 ${done} / ${stages.length}` : '';
  if (!stages.length) {
    box.appendChild(el('li', 'stage pending', '当前投影里还没有阶段信息。'));
    return;
  }
  stages.forEach((s, i) => {
    const li = el('li', 'stage ' + (s.status || 'pending'));
    li.appendChild(el('span', 'stage-dot', ''));
    const main = el('div', 'stage-main');
    const line = el('div', 'stage-line');
    line.appendChild(el('span', 'stage-name', s.label || s.key || ''));
    line.appendChild(el('span', 'stage-status', STAGE_STATUS_LABEL[s.status] || s.status || ''));
    if (s.at) line.appendChild(el('span', 'stage-time', fmtTime(s.at, true)));
    main.appendChild(line);
    // 刻意不渲染 s.note：那是这条阶段的「判定规则」原文，里面带着
    // handoff / result / review_request / acknowledgement 这类协议词。
    // 设计包 02 要求协议字段不得出现在默认阅读路径上，规则说明留在
    // payload 里供展开与排查用，不上默认界面。
    // 默认只给一句人话：这个阶段是被哪个真实事件推动的。
    // 消息号 / 线程号不摆在普通用户面前，全部收进「这些时间是怎么来的」里的证据清单。
    const lead = (s.evidence || [])[0];
    if (lead && lead.summary) {
      main.appendChild(el('p', 'stage-lead', lead.summary));
    } else if (s.status === 'pending') {
      main.appendChild(el('p', 'stage-sub', s.pending_reason === 'no_evidence'
        ? '本库还没有能证明这个阶段的事件。'
        : '还没有走到这一步。'));
    }
    li.appendChild(main);
    if (i < stages.length - 1) {
      const arrow = el('span', 'stage-arrow', '→');
      // 相邻两阶段的真实间隔。只在两头都有真时间时才显示。
      const gap = (s.at && stages[i + 1].at) ? fmtDelta(s.at, stages[i + 1].at) : '';
      if (gap) arrow.appendChild(el('b', 'stage-gap', '+' + gap));
      li.appendChild(arrow);
    }
    box.appendChild(li);
  });
  renderStageEvidence(stages);
}
/* 完整证据清单：只在展开区里出现。这里保留消息号与线程号，
   因为「关系来源可追溯」是硬要求 —— 只是不把它塞进默认阅读路径。 */
function renderStageEvidence(stages) {
  const box = $('stageEvidence');
  if (!box) return;
  box.replaceChildren();
  for (const s of stages) {
    const rows = s.evidence || [];
    if (!rows.length) continue;
    const group = el('div', 'ev-group');
    group.appendChild(el('b', 'ev-group-title', s.label || s.key || ''));
    for (const e of rows) {
      const item = el('span', 'stage-ev-item');
      if (e.message_id) item.appendChild(el('b', null, shortId(e.message_id)));
      if (e.summary) item.appendChild(document.createTextNode(' · ' + e.summary));
      if (e.at) item.appendChild(document.createTextNode(' · ' + fmtTime(e.at, true)));
      if (e.thread) {
        // 线程号要能被读到，不能只藏在 title 里 —— 触屏和键盘都拿不到 tooltip。
        item.appendChild(el('small', 'ev-thread', ` · 线程 ${shortId(e.thread)}`));
        item.title = `消息 ${e.message_id} · 线程 ${e.thread}`;
        item.dataset.threadId = e.thread;
      }
      group.appendChild(item);
    }
    box.appendChild(group);
  }
}
function renderTimeline() {
  const data = state.timeline;
  if (!data) return;
  renderStages();
  const list = $('timeline');
  list.replaceChildren();
  const c = data.counts || {};
  $('scopeAllCount').textContent = String((data.nodes || []).length);
  $('scopeKeyCount').textContent = String(c.primary ?? 0);
  $('scopeAckCount').textContent = String(c.acknowledged ?? 0);
  $('tlScopeNote').textContent = (data.scope && data.scope.note) || '';
  $('tlWhy').textContent = (data.scope && data.scope.why) || '';
  $('tlCounts').textContent = `关键协作 ${c.primary ?? 0} · 参考 ${c.supporting ?? 0}` +
    (c.acknowledged ? ` · 已确认收到 ${c.acknowledged}` : '') +
    (c.cross_session ? ` · 跨 Session ${c.cross_session}` : '');
  const nodes = visibleNodes();
  const empty = el('p', 'tl-empty', '这个范围里还没有协作事实。Agent 之间通过 Mail 留下交接、审核请求或结果后，这里会按人话显示。');
  if (!nodes.length) { list.replaceChildren(empty); return; }
  let index = 0;
  for (const n of nodes) {
    index += 1;
    list.appendChild(renderStep(n, index));
  }
  const more = $('tlMore');
  if (data.scope && data.scope.truncated) {
    more.hidden = false;
    more.textContent = `以上显示最新 ${nodes.length} 条（库内对该身份可见 ${data.scope.visible_messages ?? '?'} 条记录）。时间线是有损投影：它只呈现协作事实，不是完整聊天记录。`;
  } else more.hidden = true;
}
function renderStep(n, index) {
  const li = el('li', 'step');
  if (n.action_key === 'ack') li.classList.add('ack');
  else if (n.key_action) li.classList.add('key');
  li.tabIndex = 0;
  li.appendChild(el('span', 'step-idx', String(index)));

  const main = el('div', 'step-main');
  const line = el('div', 'step-line');
  line.appendChild(el('span', 'step-action', n.action || ''));
  line.appendChild(el('span', 'step-actor', n.actor_label || n.actor || ''));
  line.appendChild(el('span', 'step-time', fmtTime(n.time)));
  main.appendChild(line);
  if (n.result) main.appendChild(el('p', 'step-sum', n.result));
  if (n.thread && n.thread.title && n.thread.title !== n.result) {
    const thumb = el('span', 'thumb');
    thumb.appendChild(el('b', null, n.thread.title));
    main.appendChild(thumb);
  }
  li.appendChild(main);

  const side = el('div', 'step-side');
  // 投递口径由后端给（delivery_chip）。前端不再自己拼这套文案：两边各写一版
  // 的话，谁改了另一边都收不到通知 —— 「已确认收到」后面那句「不代表完成」
  // 就是这么被吞掉的，而测试断言的一直是带限定的版本。
  const chip = n.delivery_chip || { label: '已留在知识库', tone: 'wait' };
  side.appendChild(el('span', 'chip ' + (chip.tone || 'wait'), chip.label));
  if (n.cross_session) side.appendChild(el('span', 'chip cross', '跨 Session'));
  li.appendChild(side);

  const open = () => { selectStep(n, li); };
  li.addEventListener('click', open);
  li.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
  return li;
}
function selectStep(n, li) {
  document.querySelectorAll('.step.sel').forEach((x) => x.classList.remove('sel'));
  if (li) li.classList.add('sel');
  state.selNode = n;
  showDetail(stepDetail(n));
}

/* ── 详情面板 ── */
/* 只读：治理位只展示，面板不写入。
   原先这里有一个启用 / 停用开关，配 /api/mail/knowledge/toggle 写回知识库文件。
   2026-09-22 移除：那个位当时没有任何模块读取，界面却据此声称「不再被 Skill 层启用」，
   是在许诺一个没实现的下游效果。要改文件，走知识库与人的流程，不经这个只读面板。 */
function govStateRow(t) {
  const row = el('div', 'plug-state');
  const text = el('span', 'plug-text');
  text.appendChild(el('b', null, t.enabled ? '已启用' : '已停用'));
  text.appendChild(el('small', null, t.note || ''));
  row.appendChild(text);
  return row;
}
async function refreshKnowledge() {
  try {
    const km = await fetchJSON('/api/mail/knowledge-map');
    state.knowledge = km;
    $('kmScopeAll').textContent = String(km.counts.points);
    $('kmScopeWiki').textContent = String(km.counts.reviewed);
    $('kmScopeCand').textContent = String(km.counts.candidates);
    renderGovernance(km);
    drawGraph();
  } catch (err) {
    toast('刷新知识图谱失败：' + err.message);
  }
}
function showDetail(model) {
  const panel = $('detailPanel');
  $('detailKind').textContent = model.kind || '';
  $('detailTitle').textContent = model.title || '';
  const badges = $('detailBadges');
  badges.replaceChildren();
  for (const b of model.badges || []) {
    const chip = el('span', 'chip ' + (b.tone || 'wait'), b.label);
    badges.appendChild(chip);
  }
  $('detailSummary').textContent = model.summary || '';
  const body = $('detailBody');
  const frameWrap = $('detailFrameWrap');
  const frame = $('detailFrame');
  if (model.embedPath) {
    // 知识条目直接内嵌服务端渲染的 Wiki 原文页：抽屉里看到的和新标签里是同一份东西，
    // 也就不用在前端再写一个 Markdown 渲染器（两套实现必然漂移）。
    frame.src = `/api/mail/wiki-page?path=${encodeURIComponent(model.embedPath)}&embed=1`;
    frameWrap.hidden = false;
    body.hidden = true;
    body.textContent = '';
  } else {
    frame.removeAttribute('src');
    frameWrap.hidden = true;
    body.textContent = model.body || '';
    body.hidden = !model.body;
  }
  const plug = $('detailPlug');
  plug.replaceChildren();
  if (model.govState) {
    plug.hidden = false;
    plug.appendChild(govStateRow(model.govState));
  } else {
    plug.hidden = true;
  }
  const meta = $('detailMeta');
  meta.replaceChildren();
  for (const [k, v] of model.meta || []) {
    meta.appendChild(el('dt', null, k));
    meta.appendChild(el('dd', null, v));
  }
  meta.hidden = !(model.meta || []).length;
  const relBox = $('detailRelations');
  relBox.replaceChildren();
  if ((model.relations || []).length) {
    relBox.hidden = false;
    relBox.appendChild(el('div', 'detail-rel-title', '相关知识'));
    for (const r of model.relations) {
      const row = el('div', 'rel-row');
      const chip = el('span', 'rel-type', r.type_label || r.type);
      chip.style.color = cvar(REL_COLORS[r.color_key || r.type] || '--n-slate');
      row.appendChild(chip);
      row.appendChild(el('span', 'rel-target', r.target_title || r.target || ''));
      row.appendChild(el('span', 'rel-src', r.source_label || r.source || ''));
      relBox.appendChild(row);
    }
  } else relBox.hidden = true;
  const actions = $('detailActions');
  actions.replaceChildren();
  for (const a of model.actions || []) {
    if (a.href) {
      const link = el('a', a.primary ? 'primary' : 'ghost', a.label);
      link.href = a.href;
      link.target = '_blank';
      link.rel = 'noreferrer';
      actions.appendChild(link);
      continue;
    }
    const btn = el('button', a.primary ? 'primary' : 'ghost', a.label);
    btn.type = 'button';
    btn.addEventListener('click', a.onClick);
    actions.appendChild(btn);
  }
  $('detailFootnote').textContent = model.footnote || '';
  panel.hidden = false;
  document.querySelector('.app').classList.add('with-detail');
}
function closeDetail() {
  $('detailPanel').hidden = true;
  document.querySelector('.app').classList.remove('with-detail');
  document.querySelectorAll('.step.sel').forEach((x) => x.classList.remove('sel'));
  state.selNode = null;
}
function stepDetail(n) {
  const d = (n.delivery || [])[0] || null;
  const meta = [];
  meta.push(['发生时间', fmtTime(n.time, true)]);
  if (n.thread && n.thread.title) meta.push(['所属 Thread', n.thread.title]);
  if (d) meta.push(['投递对象', `${d.peer_label || d.peer} · ${d.state_label || d.state}`]);
  if ((n.sessions || []).length) meta.push(['Session', n.sessions.join('、')]);
  if ((n.machines || []).length) meta.push(['机器来源', n.machines.join('、')]);
  if ((n.evidence_refs || []).length) meta.push(['关联知识', n.evidence_refs.map((r) => r.path || r.id || '').join('、')]);
  if (n.protocol) {
    meta.push(['协作动作', `${n.protocol.record_kind_label || n.protocol.record_kind || '—'}`]);
    meta.push(['投递原因', n.protocol.delivery_reason_label || n.protocol.delivery_reason || '—']);
    if (n.protocol.origin_session_id) meta.push(['发起 Session', n.protocol.origin_session_id]);
    if (n.protocol.origin_machine_id) meta.push(['发起机器', n.protocol.origin_machine_id]);
    // 回执节点的 protocol 只有 {ack_of, ack_peer}，没有 message_id；不加兜底
    // 会让 createTextNode(undefined) 把「undefined」当成消息 ID 显示出来。
    meta.push(['消息 ID', n.protocol.message_id || '—']);
  }
  const steps = [];
  for (const item of n.delivery || []) {
    for (const s of item.steps || []) steps.push(`${item.peer_label || item.peer} · ${s.label}（${fmtTime(s.at)}）`);
  }
  const actions = [];
  if (n.thread && n.thread.id) actions.push({ label: '复制 Thread ID', onClick: () => copyText(n.thread.id, 'Thread ID 已复制：' + n.thread.id) });
  for (const ref of n.evidence_refs || []) {
    const path = ref.path || ref.id;
    if (path && /\.md$/.test(path)) {
      actions.push({ label: '定位关联知识', onClick: () => { switchTab('knowledge'); locatePoint(path); } });
      break;
    }
  }
  return {
    kind: '协作事实', title: n.result || n.action,
    badges: [
      { label: n.action_key === 'ack' ? '已确认收到' : n.key_action ? '关键协作' : '参考记录', tone: n.action_key === 'ack' ? 'ok' : n.key_action ? 'cross' : 'wait' },
      n.cross_session ? { label: '跨 Session', tone: 'cross' } : null,
    ].filter(Boolean),
    summary: n.excerpt ? `「${n.excerpt}」` : '',
    body: steps.length ? '回执轨迹：\n' + steps.join('\n') : '',
    meta, relations: [],
    actions,
    footnote: '详情来自本机 Mail 文件。「已确认收到」只代表对方确认读到，不等于事情已办妥。',
  };
}

/* ── Wiki 知识图 ── */
document.querySelectorAll('[data-km-scope]').forEach((b) => {
  b.addEventListener('click', () => {
    state.kmScope = b.dataset.kmScope;
    document.querySelectorAll('[data-km-scope]').forEach((x) => x.classList.toggle('active', x === b));
    drawGraph();
  });
});
$('kmBack').addEventListener('click', () => { state.path = []; state.selPoint = null; drawGraph(); });
$('kmSearch').addEventListener('input', (e) => { state.search = e.target.value.trim(); applyHighlight(); });
$('zoomIn').addEventListener('click', () => setZoom(state.zoom + 0.1));
$('zoomOut').addEventListener('click', () => setZoom(state.zoom - 0.1));
$('fitView').addEventListener('click', () => { state.panX = 0; state.panY = 0; setZoom(1); });
$('fullscreen').addEventListener('click', () => {
  const stage = $('graphStageGlobal');
  if (document.fullscreenElement) document.exitFullscreen();
  else if (stage.requestFullscreen) stage.requestFullscreen().catch(() => toast('浏览器不允许全屏'));
});
$('focusBack').addEventListener('click', () => { state.path = []; state.selPoint = null; drawGraph(); });
// 逐级返回：只有一层了就没得退，按钮自己藏起来，不留一个点了没反应的按钮。
$('focusUp').addEventListener('click', () => {
  state.path = state.path.slice(0, Math.max(0, state.path.length - 1));
  state.selPoint = null;
  drawGraph();
});
$('depthUp').addEventListener('click', () => setDepth(state.depth + 1));
$('depthDown').addEventListener('click', () => setDepth(state.depth - 1));
$('showCounts').addEventListener('change', (e) => { state.showCounts = e.target.checked; drawGraph(); });
$('showSource').addEventListener('change', (e) => { state.showSource = e.target.checked; drawGraph(); });
function setZoom(z) {
  state.zoom = Math.max(0.5, Math.min(1.6, Math.round(z * 100) / 100));
  $('zoomLabel').textContent = Math.round(state.zoom * 100) + '%';
  drawGraph();
}
// 展开层级与缩放同构：改了 state 就必须把标签写回去，
// 否则渲染变了、控件却恒显示初始的「2 级」（2026-09-20 审计发现）。
function setDepth(d) {
  state.depth = Math.max(1, Math.min(3, d));
  $('depthLabel').textContent = state.depth + ' 级';
  drawGraph();
}
function renderLegend(km) {
  const box = $('relationLegend');
  box.replaceChildren();
  for (const item of km.relation_legend || []) {
    if (!item.count && item.id === 'unknown') continue;
    const row = el('div', 'legend-item' + (state.hiddenRel.has(item.id) ? ' off' : ''));
    const dot = el('span', 'legend-dot');
    dot.style.background = cvar(REL_COLORS[item.color_key] || '--n-slate');
    row.appendChild(dot);
    row.appendChild(el('span', null, item.label));
    row.appendChild(el('em', null, String(item.count)));
    row.addEventListener('click', () => {
      if (state.hiddenRel.has(item.id)) state.hiddenRel.delete(item.id);
      else state.hiddenRel.add(item.id);
      drawGraph();
    });
    box.appendChild(row);
  }
}
function visibleDomains() {
  const km = state.knowledge;
  if (!km) return [];
  const flat = (d) => ({
    ...d,
    clusters: d.clusters.map((c) => ({
      ...c,
      points: c.points.filter((p) => state.kmScope === 'all' || p.kind === state.kmScope),
    })).filter((c) => c.points.length),
  });
  return km.domains.map(flat).filter((d) => d.clusters.length);
}
function findPoint(id) {
  for (const d of (state.knowledge && state.knowledge.domains) || []) {
    for (const c of d.clusters) {
      const p = c.points.find((x) => x.id === id || x.path === id);
      if (p) return { dom: d, cl: c, p };
    }
  }
  return null;
}
function locatePoint(pathOrId) {
  const found = findPoint(pathOrId) || findPoint(pathOrId.replace(/^.*\//, ''));
  if (!found) { toast('这条知识点不在当前图谱范围内'); return; }
  state.path = [found.dom.id, found.cl.id, found.p.id];
  state.selPoint = found.p;
  drawGraph();
}
function pathLabel(domains, path) {
  if (!path.length) return '';
  if (path.length === 1) return (domains.find((d) => d.id === path[0]) || {}).label || path[0];
  if (path.length === 2) {
    const dom = domains.find((d) => d.id === path[0]) || {};
    return ((dom.clusters || []).find((c) => c.id === path[1]) || {}).label || path[1];
  }
  const found = findPoint(path[2]);
  return found ? found.p.title : '知识点';
}
/* 大图嵌套小图：全局图（domains）常驻，聚焦图（path）在同屏下方展开。
   设计包 03 明确要求「全局大图始终作为背景认知」，所以这里不是替换，是并存。 */
function drawGraph() {
  const km = state.knowledge;
  if (!km) return;
  const domains = visibleDomains();
  const path = state.path;
  // 换了层级就把平移复位：否则从别处跳进来会落在上一层的偏移上，像"图飘了"。
  const key = path.join('/');
  if (key !== state.pathKey) { state.panX = 0; state.panY = 0; state.pathKey = key; }
  const label = pathLabel(domains, path);
  $('graphTitle').textContent = label ? `OKS 知识图谱 · ${label}` : 'OKS 知识图谱';
  $('graphSub').textContent = label
    ? `全局图保持在上方作为背景认知；下面是「${label}」的局部图`
    : '全局图常驻；点开知识域后，下方展开局部图';
  $('graphNote').textContent = (km.scope && km.scope.note) || '';
  $('graphWhy').textContent = (km.scope && km.scope.why) || '';
  renderCrumbs(domains);
  renderLegend(km);
  const empty = $('graphEmpty');
  if (!domains.length) {
    $('graphSvgGlobal').replaceChildren();
    $('graphSvgLocal').replaceChildren();
    empty.hidden = false;
    empty.textContent = '当前范围内没有知识点。切换左栏的视图范围，或先审核 Candidate 候选知识。';
    $('focusCard').hidden = true;
    return;
  }
  empty.hidden = true;
  renderSvg(buildGraphModel(domains, []), $('graphSvgGlobal'), $('graphStageGlobal'), { minimap: true });
  const focus = $('focusCard');
  if (path.length) {
    focus.hidden = false;
    $('focusTitle').textContent = '当前聚焦：' + label;
    $('focusSub').textContent = `第 ${path.length} 层（知识域 → 知识簇 → 知识点）；点画布空白处退回上一层`;
    $('focusUp').hidden = path.length <= 1;
    renderSvg(buildGraphModel(domains, path), $('graphSvgLocal'), $('graphStageLocal'), { pop: true });
  } else {
    focus.hidden = true;
    $('focusUp').hidden = true;
    $('graphSvgLocal').replaceChildren();
  }
  applyHighlight();
}
function renderCrumbs(domains) {
  const nav = $('kmCrumbs');
  nav.replaceChildren();
  const path = state.path;
  const add = (label, depth, clickable) => {
    const c = el('span', 'crumb' + (clickable ? ' clickable' : ' current'), label);
    if (clickable) c.addEventListener('click', () => { state.path = path.slice(0, depth); state.selPoint = null; drawGraph(); });
    nav.appendChild(c);
  };
  add('知识全景', 0, path.length > 0);
  if (path[0] != null) {
    nav.appendChild(el('span', 'crumb-sep', '›'));
    add((domains.find((d) => d.id === path[0]) || {}).label || path[0], 1, path.length > 1);
  }
  if (path[1] != null) {
    const cl = ((domains.find((d) => d.id === path[0]) || {}).clusters || []).find((c) => c.id === path[1]);
    nav.appendChild(el('span', 'crumb-sep', '›'));
    add(cl ? cl.label : path[1], 2, path.length > 2);
  }
  if (path[2] != null) {
    const f = findPoint(path[2]);
    nav.appendChild(el('span', 'crumb-sep', '›'));
    add(f ? f.p.title : '知识点', 3, false);
  }
}
/* 图模型：中心 + 分环节点 + 关系边（照设计图的中心辐射结构） */
function buildGraphModel(domains, path) {
  const nodes = [];
  const edges = [];
  const center = (label, count, colorVar) => ({ id: '__center', label, count, color: cvar(colorVar), level: 0, kind: 'center' });
  const colorOfDomain = (i) => cvar(DOMAIN_COLORS[i % DOMAIN_COLORS.length]);
  const dotColor = (p) => cvar(p.kind === 'wiki' ? '--n-blue' : '--n-orange');

  if (path.length === 0) {
    nodes.push(center('OKS 知识库', state.knowledge.counts.points, '--brand'));
    domains.forEach((d, i) => {
      nodes.push({ id: d.id, label: d.label, count: d.count, color: colorOfDomain(i), level: 1, kind: 'domain', sub: `${d.clusters.length} 组` });
      edges.push({ from: '__center', to: d.id, color: colorOfDomain(i), relKey: 'related' });
      if (state.depth >= 2) {
        d.clusters.forEach((c, j) => {
          nodes.push({ id: `${d.id}::${c.id}`, label: c.label, count: c.count, color: colorOfDomain(i), level: 2, kind: 'cluster', parent: d.id, sub: '知识簇' });
          edges.push({ from: d.id, to: `${d.id}::${c.id}`, color: colorOfDomain(i), relKey: 'related' });
          if (state.depth >= 3) {
            c.points.forEach((p, k) => {
              nodes.push({ id: `${d.id}::${c.id}::${p.id}`, label: p.title, count: null, color: dotColor(p), level: 3, kind: 'point', parent: `${d.id}::${c.id}`, point: p });
              edges.push({ from: `${d.id}::${c.id}`, to: `${d.id}::${c.id}::${p.id}`, color: dotColor(p), relKey: p.relations[0] ? p.relations[0].color_key : 'related' });
            });
          }
        });
      }
    });
    return { nodes, edges };
  }

  const dom = domains.find((d) => d.id === path[0]);
  if (!dom) { state.path = []; return buildGraphModel(domains, []); }

  if (path.length === 1) {
    nodes.push(center(dom.label, dom.count, '--brand'));
    dom.clusters.forEach((c, j) => {
      nodes.push({ id: `${dom.id}::${c.id}`, label: c.label, count: c.count, color: colorOfDomain(j), level: 1, kind: 'cluster', sub: '知识簇' });
      edges.push({ from: '__center', to: `${dom.id}::${c.id}`, color: colorOfDomain(j), relKey: 'related' });
      if (state.depth >= 2) {
        c.points.forEach((p) => {
          nodes.push({ id: `${dom.id}::${c.id}::${p.id}`, label: p.title, count: null, color: dotColor(p), level: 2, kind: 'point', parent: `${dom.id}::${c.id}`, point: p });
          edges.push({ from: `${dom.id}::${c.id}`, to: `${dom.id}::${c.id}::${p.id}`, color: dotColor(p), relKey: 'related' });
        });
      }
    });
    return { nodes, edges };
  }

  if (path.length === 2) {
    const cl = dom.clusters.find((c) => c.id === path[1]);
    if (!cl) { state.path = [dom.id]; return buildGraphModel(domains, state.path); }
    nodes.push(center(cl.label, cl.count, '--brand'));
    cl.points.forEach((p, i) => {
      const key = `${dom.id}::${cl.id}::${p.id}`;
      nodes.push({ id: key, label: p.title, count: null, color: colorOfDomain(i), level: 1, kind: 'point', point: p, sub: p.kind_label });
      edges.push({ from: '__center', to: key, color: colorOfDomain(i), relKey: p.relations[0] ? p.relations[0].color_key : 'related' });
      if (state.depth >= 2) {
        p.relations.forEach((r, ri) => {
          const rid = `${key}::rel::${ri}`;
          nodes.push({
            id: rid, label: r.target_title || r.target, count: null, color: cvar(REL_COLORS[r.color_key] || '--n-slate'),
            level: 2, kind: 'relation-target', parent: key, sub: r.type_label,
            relation: r, targetPoint: r.target_id ? findPoint(r.target_id) : null,
          });
          edges.push({ from: key, to: rid, color: cvar(REL_COLORS[r.color_key] || '--n-slate'), relKey: r.color_key || 'related', source: r.source_label });
        });
      }
    });
    return { nodes, edges };
  }

  const found = findPoint(path[2]);
  if (!found) { state.path = [dom.id, path[1]]; return buildGraphModel(domains, state.path); }
  const p = found.p;
  state.selPoint = p;
  nodes.push(center(p.title, null, '--brand'));
  const groups = new Map();
  for (const r of p.relations) {
    const key = r.color_key || r.type;
    if (!groups.has(key)) groups.set(key, { label: r.type_label || r.type, color: cvar(REL_COLORS[key] || '--n-slate'), items: [] });
    groups.get(key).items.push(r);
  }
  let gi = 0;
  for (const [key, group] of groups) {
    const gid = `${p.id}::g::${key}`;
    nodes.push({ id: gid, label: group.label, count: group.items.length, color: group.color, level: 1, kind: 'rel-group', sub: '关系' });
    edges.push({ from: '__center', to: gid, color: group.color, relKey: key });
    if (state.depth >= 2) {
      group.items.forEach((r, ri) => {
        const rid = `${gid}::i::${ri}`;
        nodes.push({
          id: rid, label: r.target_title || r.target, count: null, color: group.color, level: 2,
          kind: 'relation-target', parent: gid, sub: state.showSource ? (r.source_label || r.source) : '',
          relation: r, targetPoint: r.target_id ? findPoint(r.target_id) : null,
        });
        edges.push({ from: gid, to: rid, color: group.color, relKey: key, source: r.source_label });
      });
    }
    gi += 1;
  }
  return { nodes, edges };
}
/* 递归扇区布局（对应设计图的中心辐射结构）：
 * 子节点按父节点分配的扇区展开，扇区宽度按子树"像素宽度"加权，
 * 相邻节点因此不会挤在一起；径向步长逐级递减，整体再做内容自适应视图。 */
function layoutGraph(model, w, h) {
  const cx = w / 2, cy = h / 2;
  const pos = new Map([['__center', { x: cx, y: cy }]]);
  const kids = new Map();
  for (const n of model.nodes) {
    if (n.id === '__center') continue; // 中心节点不是自己的子节点
    const p = n.parent || '__center';
    if (!kids.has(p)) kids.set(p, []);
    kids.get(p).push(n);
  }
  const boxOf = (n) => nodeBox(n);
  const widthCache = new Map();
  const widthOf = (n) => {
    if (!widthCache.has(n.id)) widthCache.set(n.id, boxOf(n).w + 26);
    return widthCache.get(n.id);
  };
  const weight = (id) => {
    const cs = kids.get(id) || [];
    if (!cs.length) return 12;
    return cs.reduce((s, c) => s + weight(c.id), 0);
  };
  const base = Math.min(w, h);
  const stepFor = (depth) => (depth === 1 ? base * 0.3 : depth === 2 ? base * 0.235 : base * 0.19);
  function place(id, a0, a1, depth) {
    const cs = kids.get(id) || [];
    if (!cs.length) return;
    const weights = cs.map((c) => Math.max(weight(c.id), widthOf(c) * (depth === 1 ? 1 : 1.6)));
    const total = weights.reduce((s, v) => s + v, 0);
    let a = a0;
    cs.forEach((c, i) => {
      const span = (a1 - a0) * (weights[i] / total);
      const mid = a + span / 2;
      const parent = pos.get(id) || { x: cx, y: cy };
      const r = stepFor(depth);
      const p = { x: parent.x + Math.cos(mid) * r, y: parent.y + Math.sin(mid) * r };
      pos.set(c.id, p);
      c.x = p.x; c.y = p.y;
      place(c.id, a + span * 0.06, a + span * 0.94, depth + 1);
      a += span;
    });
  }
  const rootKids = kids.get('__center') || [];
  if (rootKids.length) {
    const weights = rootKids.map((n) => weight(n.id) + widthOf(n) * 2);
    const total = weights.reduce((s, v) => s + v, 0);
    let a = -Math.PI;
    rootKids.forEach((c, i) => {
      const span = Math.PI * 2 * (weights[i] / total);
      const mid = a + span / 2;
      const p = { x: cx + Math.cos(mid) * stepFor(1), y: cy + Math.sin(mid) * stepFor(1) };
      pos.set(c.id, p);
      c.x = p.x; c.y = p.y;
      place(c.id, a + span * 0.02, a + span * 0.98, 2);
      a += span;
    });
  }
  const centerNode = (model.nodes.find((n) => n.id === '__center'));
  if (centerNode) { centerNode.x = cx; centerNode.y = cy; }
  relaxOverlaps(model);
  return pos;
}
/* 避让迭代：矩形相交就沿连心方向互推（中心固定）。确定性、无随机，收敛即停。 */
function relaxOverlaps(model, iterations = 160) {
  const movable = model.nodes.filter((n) => n.x != null && n.id !== '__center');
  const boxes = new Map(movable.map((n) => [n.id, nodeBox(n)]));
  const padX = 22, padY = 16;
  for (let iter = 0; iter < iterations; iter += 1) {
    let moved = false;
    for (let i = 0; i < movable.length; i += 1) {
      for (let j = i + 1; j < movable.length; j += 1) {
        const a = movable[i], b = movable[j];
        const ba = boxes.get(a.id), bb = boxes.get(b.id);
        const dx = a.x - b.x, dy = a.y - b.y;
        const overlapX = (ba.w + bb.w) / 2 + padX - Math.abs(dx);
        const overlapY = (ba.h + bb.h) / 2 + padY - Math.abs(dy);
        if (overlapX <= 0 || overlapY <= 0) continue;
        const len = Math.hypot(dx, dy) || 1;
        const ux = (dx || 0.02) / len, uy = (dy || 0.02) / len;
        const push = Math.min(overlapX, overlapY) * 0.55 + 1.5;
        a.x += ux * push; a.y += uy * push;
        b.x -= ux * push; b.y -= uy * push;
        moved = true;
      }
    }
    if (!moved) break;
  }
  const pos = new Map(model.nodes.map((n) => [n.id, { x: n.x, y: n.y }]));
  return pos;
}
/* 内容自适应视图：算所有节点包围盒，返回 viewBox（等宽高比、带留白） */
function contentViewBox(model, w, h) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const n of model.nodes) {
    if (n.x == null) continue;
    const b = nodeBox(n);
    minX = Math.min(minX, n.x - b.w / 2 - 28);
    maxX = Math.max(maxX, n.x + b.w / 2 + 28);
    minY = Math.min(minY, n.y - b.h / 2 - 30);
    maxY = Math.max(maxY, n.y + b.h / 2 + 30);
  }
  if (!isFinite(minX)) return `0 0 ${w} ${h}`;
  let bw = maxX - minX, bh = maxY - minY;
  const aspect = w / h;
  if (bw / bh > aspect) { const nh = bw / aspect; minY -= (nh - bh) / 2; bh = nh; }
  else { const nw = bh * aspect; minX -= (nw - bw) / 2; bw = nw; }
  const pad = 1 / state.zoom;
  bw *= pad; bh *= pad;
  // 平移只挪视窗中心，不动内容；与缩放互不干扰。
  const cxx = (minX + maxX) / 2 + state.panX, cyy = (minY + maxY) / 2 + state.panY;
  return `${cxx - bw / 2} ${cyy - bh / 2} ${bw} ${bh}`;
}
function nodeBox(n) {
  const override = state.boxOverride && state.boxOverride.get(n.id);
  if (override) return override;
  if (n.kind === 'center') {
    const w = Math.max(132, textWidth(n.label, 13) + 40);
    return { w, h: 58, rx: 17 };
  }
  const size = n.level === 1 ? 12 : 11.5;
  const countW = n.count != null && state.showCounts ? 34 : 22;
  const w = Math.max(104, textWidth(n.label, size) + countW + 26);
  return { w: Math.min(w, 232), h: n.sub ? (n.level === 1 ? 46 : 40) : (n.level === 1 ? 38 : 34), rx: 10 };
}
/* 渲染后用真实文本宽度回填节点框，避免估算偏差导致文字溢出 */
function fitNodeWidths(svg) {
  state.boxOverride = new Map();
  svg.querySelectorAll('.graph-node').forEach((grp) => {
    const id = grp.getAttribute('data-id');
    const label = grp.querySelector('.gn-label');
    const rects = grp.querySelectorAll('rect');
    const rect = rects[0];
    const bar = rects[1];
    const count = grp.querySelector('.gn-count');
    const sub = grp.querySelector('.gn-sub');
    const isCenter = id === '__center';
    let need = (label ? label.getComputedTextLength() : 0) + (isCenter ? 34 : 28);
    if (count && !isCenter) need += count.getComputedTextLength() + 16;
    if (sub) need = Math.max(need, sub.getComputedTextLength() + 28);
    const cur = Number(rect.getAttribute('width'));
    const width = Math.max(cur, Math.ceil(need));
    const height = Math.max(Number(rect.getAttribute('height')), 32);
    rect.setAttribute('width', width);
    rect.setAttribute('x', -width / 2);
    if (bar) { bar.setAttribute('height', height - 14); bar.setAttribute('y', -height / 2 + 7); }
    if (count && !isCenter) count.setAttribute('x', width / 2 - 9);
    if (sub) sub.setAttribute('x', -width / 2 + 13);
    const box = { w: width, h: Number(rect.getAttribute('height')), rx: Number(rect.getAttribute('rx')) };
    state.boxOverride.set(id, box);
    if (label && !isCenter) label.setAttribute('x', -width / 2 + 13);
  });
}
function renderSvg(model, svg, stage, opts = {}) {
  const w = Math.max(stage.clientWidth, 640), h = Math.max(stage.clientHeight, 420);
  svg.replaceChildren();
  state.boxOverride = new Map();
  layoutGraph(model, w, h);
  const g = svgEl('g', {});
  const hiddenRels = state.hiddenRel;
  const isHidden = (n) => n.relation && hiddenRels.has(n.relation.color_key || n.relation.type);
  const byId = new Map(model.nodes.map((n) => [n.id, n]));
  for (const e of model.edges) {
    if (hiddenRels.has(e.relKey)) continue;
    const a = byId.get(e.from), b = byId.get(e.to);
    if (!a || !b || a.x == null || b.x == null || isHidden(b)) continue;
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    const dx = b.x - a.x, dy = b.y - a.y;
    const nx = -dy * 0.1, ny = dx * 0.1;
    const path = svgEl('path', {
      d: `M ${a.x} ${a.y} Q ${mx + nx} ${my + ny} ${b.x} ${b.y}`,
      class: 'graph-edge',
      stroke: e.color,
      'stroke-width': a.level === 0 ? 1.8 : 1.2,
      opacity: a.level === 0 ? 0.55 : 0.4,
      'data-from': e.from, 'data-to': e.to,
    });
    g.appendChild(path);
  }
  const zoomG = svgEl('g', {});
  const nodeById = new Map();
  for (const n of model.nodes) {
    if (n.x == null) continue;
    const box = nodeBox(n);
    const grp = svgEl('g', { class: 'graph-node' + (n.kind === 'relation-target' ? ' alt' : ''), 'data-id': n.id, 'data-label': n.label });
    grp.setAttribute('transform', `translate(${n.x} ${n.y})`);
    const isCenter = n.kind === 'center';
    const rect = svgEl('rect', {
      x: -box.w / 2, y: -box.h / 2, width: box.w, height: box.h, rx: box.rx,
      fill: isCenter ? (n.color || cvar('--brand')) : '#fff',
      stroke: n.color || cvar('--n-slate'),
      'stroke-width': isCenter ? 0 : 1.4,
    });
    grp.appendChild(rect);
    if (!isCenter) {
      grp.appendChild(svgEl('rect', { x: -box.w / 2 + 1, y: -box.h / 2 + 7, width: 3, height: box.h - 14, rx: 2, fill: n.color }));
    }
    const label = svgEl('text', {
      x: isCenter ? 0 : -box.w / 2 + 13, y: isCenter ? 2 : n.sub ? -3 : 1,
      'text-anchor': isCenter ? 'middle' : 'start',
      class: 'gn-label' + (isCenter ? ' center' : ''), 'dominant-baseline': 'middle',
    });
    label.textContent = n.label;
    grp.appendChild(label);
    if (n.count != null && state.showCounts) {
      const count = svgEl('text', {
        x: isCenter ? 0 : box.w / 2 - 9, y: isCenter ? 18 : n.sub ? -3 : 1,
        'text-anchor': isCenter ? 'middle' : 'end', 'dominant-baseline': 'middle',
        class: 'gn-count', fill: isCenter ? '#ffffffdd' : n.color,
      });
      count.textContent = isCenter ? `共 ${n.count} 条` : String(n.count);
      grp.appendChild(count);
    }
    if (n.sub && !isCenter) {
      const sub = svgEl('text', { x: -box.w / 2 + 13, y: 10, class: 'gn-sub', 'dominant-baseline': 'middle' });
      sub.textContent = n.sub;
      grp.appendChild(sub);
    }
    grp.addEventListener('click', (ev) => { ev.stopPropagation(); onNodeClick(n); });
    // 治理位被停用的知识点在图上就要看得出来，而不是只在详情里说一句。
    if (n.point && n.point.governance && !n.point.governance.enabled) grp.classList.add('off');
    zoomG.appendChild(grp);
    nodeById.set(n.id, grp);
  }
  g.appendChild(zoomG);
  svg.appendChild(g);
  fitNodeWidths(svg);
  svg.setAttribute('viewBox', contentViewBox(model, w, h));
  bindPan(svg);
  // 只有聚焦图点空白才「退上一层」；全局图常驻，点它不该改变路径。
  // 刚拖动过就不算「点空白」—— 否则拖一下画布就被当成退回，体验很糟。
  svg.onclick = opts.pop
    ? () => {
        if (svg.__panMoved && svg.__panMoved()) return;
        state.path = state.path.slice(0, Math.max(0, state.path.length - 1));
        state.selPoint = null;
        drawGraph();
      }
    : null;
  if (opts.minimap) renderMiniMap(model);
}
/* 拖动平移：抓画布空白处拖，改的是 viewBox。只绑一次（重渲染不会重复挂）。
   两个坑：① 点在节点上不算拖动，否则点节点会被吃掉；② 拖动结束后要抑制一次 click。 */
function bindPan(svg) {
  if (svg.__panBound) return;
  svg.__panBound = true;
  let dragging = false, moved = false, lx = 0, ly = 0;
  svg.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0) return;
    if (ev.target.closest && ev.target.closest('.graph-node')) return;
    dragging = true; moved = false; lx = ev.clientX; ly = ev.clientY;
    svg.classList.add('panning');
    try { svg.setPointerCapture(ev.pointerId); } catch (_) { /* 老浏览器就算了 */ }
  });
  svg.addEventListener('pointermove', (ev) => {
    if (!dragging) return;
    const dx = ev.clientX - lx, dy = ev.clientY - ly;
    if (!moved && Math.abs(dx) + Math.abs(dy) < 3) return;   // 3px 以内当点击
    moved = true;
    const vb = (svg.getAttribute('viewBox') || '0 0 1 1').split(/\s+/).map(Number);
    const kx = vb[2] / Math.max(svg.clientWidth, 1);
    const ky = vb[3] / Math.max(svg.clientHeight, 1);
    state.panX += dx * kx; state.panY += dy * ky;
    lx = ev.clientX; ly = ev.clientY;
    svg.setAttribute('viewBox', `${vb[0] - dx * kx} ${vb[1] - dy * ky} ${vb[2]} ${vb[3]}`);
  });
  const end = (ev) => {
    if (!dragging) return;
    dragging = false;
    svg.classList.remove('panning');
    try { svg.releasePointerCapture(ev.pointerId); } catch (_) { /* 已释放 */ }
    if (moved) setTimeout(() => { moved = false; }, 0);      // 让紧随的 click 先被挡掉
  };
  svg.addEventListener('pointerup', end);
  svg.addEventListener('pointercancel', end);
  svg.__panMoved = () => moved;
}
/* 右上角全局缩略图（对应设计图的「全局视图」浮窗） */
function renderMiniMap(model) {
  const box = $('miniMap');
  const nodes = model.nodes.filter((n) => n.x != null);
  if (nodes.length < 4) { box.hidden = true; box.replaceChildren(); return; }
  box.hidden = false;
  box.replaceChildren();
  box.appendChild(el('div', 'mini-title', '当前视图'));
  const svg = svgEl('svg', { viewBox: '0 0 140 66', width: '140', height: '66' });
  const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const sx = (x) => 8 + ((x - minX) / Math.max(maxX - minX, 1)) * 124;
  const sy = (y) => 4 + ((y - minY) / Math.max(maxY - minY, 1)) * 58;
  for (const n of nodes) {
    const r = n.level === 0 ? 4.5 : n.level === 1 ? 3.4 : 2.4;
    svg.appendChild(svgEl('circle', { cx: sx(n.x), cy: sy(n.y), r, fill: n.color, opacity: n.level === 0 ? 0.95 : 0.7 }));
  }
  box.appendChild(svg);
}
function onNodeClick(n) {
  toggleNodeSelected(n.id);
  if (n.kind === 'domain') { state.path = [n.id]; state.selPoint = null; drawGraph(); return; }
  if (n.kind === 'cluster') {
    const [domId, clId] = n.id.split('::');
    state.path = [domId, clId];
    state.selPoint = null;
    drawGraph();
    const cl = ((state.knowledge.domains.find((d) => d.id === domId) || {}).clusters || []).find((c) => c.id === clId);
    if (cl) showDetail(clusterDetail(cl, domId));
    return;
  }
  if (n.kind === 'point') {
    const parts = n.id.split('::');
    state.path = [parts[0], parts[1], parts[2]];
    state.selPoint = n.point;
    drawGraph();
    showDetail(pointDetail(n.point));
    return;
  }
  if (n.kind === 'relation-target') {
    const r = n.relation;
    if (n.targetPoint) {
      // `targetPoint` 已经是 findPoint() 的返回值 {dom, cl, p}。之前又把它当 id
      // 传回 findPoint()，拿到的必然是 undefined，于是回退分支把包装对象当成
      // 知识点交给 pointDetail()，在 `p.relations.length` 处抛错、抽屉直接空白。
      const f = n.targetPoint;
      state.path = [state.path[0], state.path[1], f.p.id];
      state.selPoint = f.p;
      drawGraph();
      showDetail(pointDetail(f.p));
    } else {
      showDetail({
        kind: '关系指向 · 尚未收录', title: r.target_title || r.target,
        badges: [{ label: r.type_label || r.type, tone: 'wait' }],
        summary: r.note || '这条关系指向的条目当前不在本图谱范围内（可能尚未建条目或未通过审核）。',
        meta: [['关系来源', r.source_label || r.source || '—']],
        relations: [], actions: [],
        footnote: '面板只投影已有文件；这里不推断缺失条目的内容。',
      });
    }
  }
}
function clusterDetail(cl, domId) {
  const dom = state.knowledge.domains.find((d) => d.id === domId);
  return {
    kind: '知识簇', title: cl.label,
    badges: [{ label: `${dom ? dom.label : domId}`, tone: 'cross' }, { label: `${cl.count} 个知识点`, tone: 'wait' }],
    summary: `位于知识域「${dom ? dom.label : domId}」下的知识簇，由条目的 frontmatter 标签推导。`,
    meta: [['知识簇来源', cl.points[0] ? cl.points[0].cluster_source : '—']],
    // 这两个字段在「相关知识」里分别回答「这是一条什么关系」和「它从哪来」。
    // 之前塞的是条目的类型与审核状态，渲染出来成了「关系类型＝已审核 Wiki、
    // 来源＝可复用」——关系类型的位置放了条目类型，来源的位置放了审核状态。
    relations: cl.points.slice(0, 8).map((p) => ({
      type: 'related',
      type_label: '同簇成员',
      color_key: 'related',
      target: p.title,
      target_title: p.title,
      source_label: `${p.kind_label} · ${p.status_label}`,
    })),
    actions: [{ label: '展开第一个知识点', onClick: () => { state.path = [domId, cl.id, cl.points[0].id]; drawGraph(); showDetail(pointDetail(cl.points[0])); } }],
    footnote: '知识簇只是分组视图；它不改变任何知识条目的治理状态。',
  };
}
function pointDetail(p) {
  const gov = p.governance || {};
  const enabled = gov.enabled !== false;
  const meta = [
    ['类型', p.kind_label],
    ['路径', p.path],
    ['审核状态', p.status_label],
    ['最近更新', `${fmtTime(p.updated_at, true)}（来源：${p.timestamp_source_label || '记录时间'}）`],
  ];
  if (gov.type_label) meta.push(['治理类型', `${gov.type_label}${gov.type_question ? `（${gov.type_question}）` : ''}`]);
  meta.push(['enabled 标记', enabled ? 'true' : 'false']);
  if (p.skill) meta.push(['Skill 状态', p.skill.label + (p.skill.source ? `（依据：${p.skill.source}）` : '')]);
  if (Array.isArray(p.tag_labels) && p.tag_labels.length) meta.push(['标签', p.tag_labels.join('、')]);
  const srcCount = Array.isArray(p.wiki_refs) ? p.wiki_refs.length : 0;
  meta.push(['关联知识', `${p.relations.length} 条`]);
  meta.push(['Wiki 引用', `${srcCount} 处`]);
  const href = `/api/mail/wiki-page?path=${encodeURIComponent(p.path)}`;
  return {
    kind: p.kind_label, title: p.title,
    badges: [
      { label: p.status_label, tone: p.status === 'active' ? 'ok' : 'wait' },
      gov.type_label ? { label: gov.type_label, tone: 'cross' } : null,
      enabled ? null : { label: '已停用', tone: 'warn' },
      p.body_truncated ? { label: '正文已截断', tone: 'warn' } : null,
    ].filter(Boolean),
    summary: p.summary || '',
    body: p.body || '',
    embedPath: p.path,
    govState: {
      path: p.path,
      enabled,
      note: '知识库文件 frontmatter 里的 enabled 位。面板只读展示、不改写；当前版本没有任何模块读取这个位。',
    },
    meta,
    relations: p.relations,
    actions: [
      { label: '在完整 Wiki 中打开 ↗', primary: true, href },
      { label: '复制正文', onClick: () => copyText(p.body || p.summary || '', '知识正文已复制') },
      { label: '复制路径', onClick: () => copyText(p.path, `路径已复制：${p.path}`) },
    ],
    footnote: '图谱与详情是只读投影，不替代 Wiki 本体；这个面板不写任何文件。正文与治理状态的变更仍在知识库与人的流程里完成。',
  };
}
function nodeIndex() {
  return new Map([...document.querySelectorAll('.graph-node')].map((n) => [n.getAttribute('data-id'), n]));
}
function toggleNodeSelected(id) {
  document.querySelectorAll('.graph-node').forEach((node) => {
    node.classList.toggle('sel', node.getAttribute('data-id') === id);
  });
}
/* 搜索高亮对「全局图 + 聚焦图」同时生效，所以从 DOM 取节点而不是缓存的单张表。 */
function applyHighlight() {
  const q = state.search.toLowerCase();
  const index = nodeIndex();
  for (const node of index.values()) {
    const label = (node.getAttribute('data-label') || '').toLowerCase();
    node.classList.toggle('dim', !!q && !label.includes(q));
  }
  document.querySelectorAll('.graph-edge').forEach((edge) => {
    if (!q) { edge.classList.remove('dim'); return; }
    const from = index.get(edge.getAttribute('data-from'));
    const to = index.get(edge.getAttribute('data-to'));
    const hit = (from && !from.classList.contains('dim')) || (to && !to.classList.contains('dim'));
    edge.classList.toggle('dim', !hit);
  });
}
function resizeGraph() {
  if (!state.knowledge) return;
  const stage = $('graphStageGlobal');
  if (stage.clientWidth > 0) drawGraph();
}
let resizeTimer = null;
window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(resizeGraph, 180); });
document.querySelectorAll('[data-goto]').forEach((b) => {
  b.addEventListener('click', () => {
    const target = $(b.dataset.goto);
    if (target) { target.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
  });
});

/* ── Skills 治理 · 可插拔 ── */
function renderGovernance(km) {
  const g = km.governance || {};
  const box = $('govBody');
  box.replaceChildren();
  const types = el('div', 'gov-types');
  for (const t of g.types || []) {
    const chip = el('span', 'gov-chip');
    chip.appendChild(document.createTextNode(`${t.label} `));
    chip.appendChild(el('em', null, `${t.enabled}/${t.count}`));
    chip.title = `${t.label}：启用 ${t.enabled} 条，共 ${t.count} 条`;
    types.appendChild(chip);
  }
  if (g.unclassified) {
    const chip = el('span', 'gov-chip');
    chip.appendChild(document.createTextNode('未标注 '));
    chip.appendChild(el('em', null, String(g.unclassified)));
    chip.title = '这些条目还没有治理分类';
    types.appendChild(chip);
  }
  box.appendChild(types);

  const pluggable = g.pluggable || [];
  if (pluggable.length) {
    box.appendChild(el('div', 'detail-rel-title', '可插拔清单（只读展示）'));
    const list = el('div', 'plug-list');
    for (const member of pluggable) {
      const row = el('div', 'plug-item');
      const left = el('button', 'plug-item-open');
      left.type = 'button';
      left.appendChild(el('b', null, member.title));
      left.appendChild(el('small', null, `${member.kind_label} · ${member.enabled ? 'enabled: true' : 'enabled: false'}`));
      left.addEventListener('click', () => { switchTab('knowledge'); locatePoint(member.path); });
      row.appendChild(left);
      list.appendChild(row);
    }
    box.appendChild(list);
  }

  // 阶段边界（允许做什么 / 明确不做）属于维护者口径，默认收在展开说明里，
  // 不占用普通读者的第一眼阅读路径。功能性的类型计数与可插拔清单仍留在 card 上。
  const sb = g.skill_boundary;
  // #govBody 每轮都被 replaceChildren() 清空，独立的折叠容器 #govPolicy 没有：
  // 不清它，「本阶段允许 / 明确不做」两段会随着每次轮询不停地叠加下去。
  const policy = $('govPolicy');
  if (policy) policy.replaceChildren();
  const host = policy || box;
  if (sb && typeof sb === 'object') {
    if (Array.isArray(sb.allowed) && sb.allowed.length) {
      host.appendChild(el('div', 'detail-rel-title', '本阶段允许'));
      const ul = el('ul', 'gov-list');
      for (const item of sb.allowed) ul.appendChild(el('li', null, item));
      host.appendChild(ul);
    }
    if (Array.isArray(sb.not_done) && sb.not_done.length) {
      host.appendChild(el('div', 'detail-rel-title', '明确不做'));
      const ul = el('ul', 'gov-list');
      for (const item of sb.not_done) ul.appendChild(el('li', null, item));
      host.appendChild(ul);
    }
    if (sb.next_gate) host.appendChild(el('p', 'muted small', sb.next_gate));
  } else if (typeof sb === 'string' && sb) host.appendChild(el('p', 'muted small', sb));
  const c = km.counts || {};
  const t = (g.toggle && g.toggle.counts) || {};
  $('govCounts').textContent = `${t.enabled ?? 0} 启用 · ${t.disabled ?? 0} 停用`;
  $('govNote').textContent = `${g.note || ''} 三分类只是治理属性，不参与图谱分组。Skill 候选 ${c.skill_candidates ?? 0} 条 · 已成包 ${c.skill_packaged ?? 0} 条。面板不做安装、发布或打包；唯一的写入就是启用 / 停用这个治理位（写前备份、原子替换）。`;
}

/* ── 启动 ── */
/* ── 实时更新：短轮询 ──
   面板不是静态只读快照：Agent 写了 Mail、审核改了 Wiki、治理开关动了，
   这里要在几秒内跟上。只重渲染「真的变了」的那一块，避免每轮都闪一次。
   诚实边界：这只是本机文件轮询，不表示任何 Agent 在线，也不代表远端实时。 */
const POLL_MS = 5000;
let pollTimer = null;
function sameJson(a, b) {
  try { return JSON.stringify(a) === JSON.stringify(b); } catch (_) { return false; }
}
function setRuntime(text) { $('runtimeText').textContent = text; }
function stamp() { return fmtTime(new Date().toISOString(), true); }
async function refresh() {
  const results = await Promise.allSettled([
    fetchJSON('/api/mail/timeline'),
    fetchJSON('/api/mail/status'),
    fetchJSON('/api/mail/knowledge-map'),
  ]);
  const changed = [];
  if (results[0].status === 'fulfilled' && !sameJson(results[0].value, state.timeline)) {
    state.timeline = results[0].value; renderTimeline(); changed.push('时间线');
  }
  if (results[1].status === 'fulfilled' && !sameJson(results[1].value, state.status)) {
    state.status = results[1].value; renderAgents(state.status); changed.push('成员');
  }
  if (results[2].status === 'fulfilled' && !sameJson(results[2].value, state.knowledge)) {
    state.knowledge = results[2].value;
    $('kmScopeAll').textContent = String(state.knowledge.counts.points);
    $('kmScopeWiki').textContent = String(state.knowledge.counts.reviewed);
    $('kmScopeCand').textContent = String(state.knowledge.counts.candidates);
    renderGovernance(state.knowledge);
    drawGraph();
    changed.push('知识图');
  }
  return { failed: results.filter((r) => r.status === 'rejected').length, changed };
}
function startPolling() {
  if (pollTimer) return;
  const tick = async () => {
    if (document.hidden) return;                 // 页面在后台就空转，不做无用功
    const { failed, changed } = await refresh();
    if (failed) { setRuntime('自动刷新暂时失败，显示的是上一次读到的内容'); return; }
    if (changed.length) {
      setRuntime(`已读取本机知识库 · ${stamp()} · 刚刚更新了${changed.join('、')}`);
      setTimeout(() => { if (!document.hidden) setRuntime(`已读取本机知识库 · ${stamp()}`); }, 4000);
    } else {
      setRuntime(`已读取本机知识库 · ${stamp()}`);
    }
  };
  pollTimer = setInterval(tick, POLL_MS);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) tick(); });
}
async function boot() {
  setRuntime('正在读取本机知识库…');
  const { failed } = await refresh();
  try {
    const g = await fetchJSON('/api/connection-guide');
    if (g) state.guide = typeof g === 'string' ? g : (g.guide || g.text || JSON.stringify(g, null, 2));
  } catch (_) { /* 读不到就退回兜底文案，不阻塞面板 */ }
  setRuntime(failed
    ? `部分数据读取失败（${failed}/3）：面板显示的是能读到的部分`
    : `已读取本机知识库 · ${stamp()}`);
  startPolling();
}
boot().catch((e) => { setRuntime('无法连接本地 OKS Mail：' + e.message); });
