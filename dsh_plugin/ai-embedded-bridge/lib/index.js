/**
 * ai-embedded-bridge — DSH 串口桥插件。
 *
 * 接收AI嵌入式助手推送的接收数据，把数据注入指定的 Agent 会话：
 *   - 总开关在 DSH 侧（mode: off | chat | monitor），默认 off：没开启就不注入；
 *   - chat 模式：每一条数据都 followup（唤醒 Agent 回一条，像聊天）
 *   - monitor 模式：数据进缓冲，按间隔/大小合并成一条监控消息再 followup
 *   - 目标会话可精确指定（targetAgentId，默认 '' = 自动选最新会话）
 *
 * HTTP 端点（注册在 DSH web server 上，同源 /plugins/ai-embedded-bridge/*）：
 *   POST /plugins/ai-embedded-bridge/incoming  { text, hex?, ts? }   推送数据
 *   POST /plugins/ai-embedded-bridge/mode      { mode, monitorMs? }  切换总开关/模式（off|chat|monitor）
 *   POST /plugins/ai-embedded-bridge/target    { agentId }           指定注入会话（'' = 自动最新）
 *   POST /plugins/ai-embedded-bridge/status    {}                    查询状态 + 会话列表
 *
 * UI：通过 webServer.tapIndex 向 index.html 注入一个右下角悬浮面板
 * （串口注入总开关 + 会话选择 + 实时状态），纯 vanilla JS，不依赖 DSH 前端源码。
 */
import { createUserMessage } from '@deepseek-ai/dsh-llm';
import { foldSessionTitle } from '@deepseek-ai/dsh-session-title';

export const name = 'ai-embedded-bridge';

const ROUTE_PREFIX = '/plugins/ai-embedded-bridge';
const MAX_BODY_BYTES = 1024 * 1024;

// 取会话可读标识：标题 > 工作目录 basename > id
function agentLabel(agent) {
  try {
    const session = agent?.session;
    if (session) {
      const title = foldSessionTitle(session.events ?? [])?.title;
      if (title) return title;
    }
    const cwd = session?.header?.cwd;
    if (typeof cwd === 'string' && cwd) {
      const base = cwd.replace(/[\\/]+$/, '').split(/[\\/]/).pop();
      if (base) return base;
    }
  } catch { /* 标签只是辅助，失败退回 id */ }
  return String(agent?.id ?? '');
}

export function apply(ctx, config = {}) {
  // 只注入 webServer；agents 服务在运行时懒取（避免注入依赖导致加载失败）
  ctx.inject(['webServer'], (scope) => {
    mount(ctx, scope, config).catch((error) => {
      scope.logger.warn(`ai-embedded-bridge: setup failed: ${error?.message ?? error}`);
    });
  });
}

async function mount(ctx, scope, config = {}) {
  const state = {
    mode: 'off', // 总开关：off 不注入；chat/monitor 才注入
    monitorMs: 5000,
    monitorMaxChars: 2048,
    targetAgentId: '', // '' = 自动（最新会话）
    buffer: [],
    bufferChars: 0,
  };

  function agentsRegistry() {
    try {
      return ctx.get('agents') ?? scope.get('agents') ?? undefined;
    } catch {
      return undefined;
    }
  }

  // ---- 目标 Agent：指定 id 优先，否则最近创建的会话 ----
  function resolveTarget() {
    try {
      const registry = agentsRegistry();
      if (!registry) return undefined;
      const list = typeof registry.list === 'function' ? registry.list() : [];
      if (state.targetAgentId) {
        const found = list.find((agent) => agent?.id === state.targetAgentId);
        if (found) return found;
      }
      return list[list.length - 1];
    } catch {
      return undefined;
    }
  }

  // ---- 注入一条 user 消息并唤醒 Agent ----
  function pushToAgent(text) {
    const agent = resolveTarget();
    if (!agent || typeof agent.followup !== 'function') {
      return {
        ok: false,
        error: `no followup (agent=${typeof agent}, followup=${typeof agent?.followup}, hasList=${typeof agentsRegistry()?.list})`,
      };
    }
    try {
      const message = createUserMessage({
        content: [{ type: 'text', text }],
        source: { kind: 'plugin', plugin: 'ai-embedded-bridge' },
      });
      agent.followup(message);
      return { ok: true };
    } catch (error) {
      return { ok: false, error: String(error?.message ?? error) };
    }
  }

  function flushMonitor() {
    if (state.buffer.length === 0) return;
    const text = `[AI嵌入式工具·监控] ${state.buffer.join('')}`;
    state.buffer = [];
    state.bufferChars = 0;
    return pushToAgent(text);
  }

  function handleIncoming(body) {
    const text = typeof body?.text === 'string' ? body.text : '';
    if (!text) return { ok: false, error: 'empty text' };
    // 总开关在 DSH 侧：只有 mode != off 才注入（incoming 里的 mode 字段忽略，防止AI嵌入式助手绕过开关）
    const mode = state.mode;
    if (mode === 'off') return { ok: true, note: 'ignored: mode=off' };
    if (mode === 'chat') {
      return pushToAgent(`[AI嵌入式工具] ${text}`);
    }
    if (mode === 'monitor') {
      state.buffer.push(text);
      state.bufferChars += text.length;
      if (state.bufferChars >= state.monitorMaxChars) flushMonitor();
      return { ok: true, bufferedChars: state.bufferChars };
    }
    return { ok: false, error: `unknown mode ${mode}` };
  }

  function handleMode(body) {
    const mode = String(body?.mode ?? '').toLowerCase();
    if (!['off', 'chat', 'monitor'].includes(mode)) {
      return { ok: false, error: `mode must be off|chat|monitor, got "${mode}"` };
    }
    state.mode = mode;
    const ms = Number(body?.monitorMs);
    if (Number.isFinite(ms) && ms >= 500) state.monitorMs = Math.round(ms);
    if (mode !== 'monitor') flushMonitor();
    return { ok: true, mode: state.mode, monitorMs: state.monitorMs };
  }

  function handleTarget(body) {
    const agentId = typeof body?.agentId === 'string' ? body.agentId : '';
    const registry = agentsRegistry();
    const list = typeof registry?.list === 'function' ? registry.list() : [];
    if (agentId && !list.some((agent) => agent?.id === agentId)) {
      return { ok: false, error: `unknown agent "${agentId}"` };
    }
    state.targetAgentId = agentId;
    return { ok: true, targetAgentId: state.targetAgentId };
  }

  // ---- HTTP ----
  function readJson(req) {
    return new Promise((resolve, reject) => {
      let data = '';
      let size = 0;
      req.setEncoding('utf8');
      req.on('data', (chunk) => {
        size += chunk.length;
        if (size > MAX_BODY_BYTES) {
          reject(new Error('body too large'));
          req.destroy();
          return;
        }
        data += chunk;
      });
      req.on('end', () => {
        if (!data) return resolve(undefined);
        try {
          resolve(JSON.parse(data));
        } catch {
          reject(new Error('invalid JSON'));
        }
      });
      req.on('error', reject);
    });
  }

  function send(res, status, payload) {
    res.writeHead(status, {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store',
    });
    res.end(JSON.stringify(payload));
  }

  async function handle(req, res) {
    let pathname;
    try {
      pathname = new URL(req.url ?? '/', 'http://x').pathname;
    } catch {
      return send(res, 400, { ok: false, error: 'bad request' });
    }
    if (req.method !== 'POST') {
      return send(res, 405, { ok: false, error: 'method not allowed' });
    }
    const mediaType = String(req.headers['content-type'] ?? '')
      .split(';', 1)[0].trim().toLowerCase();
    if (mediaType !== 'application/json') {
      return send(res, 415, { ok: false, error: 'content-type must be application/json' });
    }
    let body;
    try {
      body = await readJson(req);
    } catch (error) {
      return send(res, 400, { ok: false, error: error?.message ?? 'bad body' });
    }
    const sub = pathname === ROUTE_PREFIX ? '' : pathname.slice(ROUTE_PREFIX.length);
    let out;
    try {
      if (sub === '/incoming') out = handleIncoming(body ?? {});
      else if (sub === '/mode') out = handleMode(body ?? {});
      else if (sub === '/target') out = handleTarget(body ?? {});
      else if (sub === '/status') {
        const reg = agentsRegistry();
        const list = (typeof reg?.list === 'function') ? reg.list() : [];
        out = {
          ok: true,
          mode: state.mode,
          monitorMs: state.monitorMs,
          bufferedChars: state.bufferChars,
          targetAgentId: state.targetAgentId,
          hasAgentRegistry: !!reg,
          agentCount: list.length,
          hasAgent: !!resolveTarget(),
          agents: list.map((agent) => ({
            id: agent?.id,
            label: agentLabel(agent),
            status: agent?.status,
            createdAt: agent?.session?.header?.createdAt,
          })),
          latestAgentId: list.length ? list[list.length - 1]?.id : undefined,
        };
      } else {
        return send(res, 404, { ok: false, error: 'not found' });
      }
    } catch (error) {
      scope.logger.warn(`ai-embedded-bridge: ${sub} failed: ${error?.message ?? error}`);
      out = { ok: false, error: String(error?.message ?? error) };
    }
    send(res, 200, out);
  }

  const dispose = scope.webServer.register({
    kind: 'prefix',
    path: ROUTE_PREFIX,
    handler: handle,
  });
  // effect 的 setup 只返回 disposer，卸载时才调用（不能立即调用！）
  scope.effect(() => dispose, 'ai-embedded-bridge: routes');

  // ---- 注入悬浮面板（右下角弹窗：串口注入总开关 + 会话选择）----
  const disposeTap = scope.webServer.tapIndex((html) => {
    const marker = '<!--ai-embedded-bridge-->';
    if (html.includes(marker)) return html;
    if (!html.includes('</body>')) return html + marker + WIDGET_MARKUP;
    return html.replace('</body>', marker + WIDGET_MARKUP + '</body>');
  });
  scope.effect(() => disposeTap, 'ai-embedded-bridge: index-widget');

  const timer = setInterval(() => {
    try {
      flushMonitor();
    } catch { /* timer errors are non-fatal */ }
  }, Math.max(500, state.monitorMs));
  scope.effect(() => () => clearInterval(timer), 'ai-embedded-bridge: monitor-timer');

  scope.logger.info('ai-embedded-bridge: ready');
}

// ---- 悬浮面板：纯 vanilla JS，注入到 DSH 每个页面 ----
// 注意：不要在字符串里出现 "</script>" 字面量或反引号，避免破坏 HTML/JS 解析。
const WIDGET_MARKUP = `
<style id="ai-embedded-bridge-style">
#ai-embedded-bridge-widget{position:fixed;right:18px;bottom:18px;z-index:99999;font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;font-size:13px;line-height:1.4;color:#e6e6e6}
#ai-embedded-bridge-widget *{box-sizing:border-box}
.sbfab{display:flex;align-items:center;gap:6px;padding:6px 12px;border:1px solid rgba(255,255,255,.14);border-radius:999px;background:rgba(28,28,34,.92);box-shadow:0 4px 16px rgba(0,0,0,.35);cursor:pointer;user-select:none;transition:background .15s}
.sbfab:hover{background:rgba(48,48,58,.95)}
.sbfab .dot{width:9px;height:9px;border-radius:50%;background:#6b6b76;flex:none}
.sbfab.on .dot{background:#4ade80}
.sbfab.mon .dot{background:#fbbf24}
.sbpanel{position:fixed;right:18px;bottom:56px;width:280px;max-height:70vh;overflow:auto;background:rgba(24,24,30,.97);border:1px solid rgba(255,255,255,.14);border-radius:12px;box-shadow:0 8px 32px rgba(0,0,0,.5);padding:12px 14px;display:none;z-index:100000}
#ai-embedded-bridge-widget.open .sbpanel{display:block}
.sbpanel h4{margin:0 0 10px;font-size:13px;font-weight:600}
.sbrow{margin:8px 0}
.sbrow .lbl{display:block;margin-bottom:4px;font-size:12px;color:#a0a0ac}
.sbseg{display:flex;gap:6px}
.sbseg button{flex:1;padding:5px 0;border:1px solid rgba(255,255,255,.16);border-radius:8px;background:rgba(255,255,255,.05);color:#d0d0d8;cursor:pointer;font-size:12px}
.sbseg button.on{background:#2563eb;border-color:#3b82f6;color:#fff}
.sbseg button.danger.on{background:#b91c1c;border-color:#ef4444;color:#fff}
.sbselect{width:100%;padding:6px 8px;border:1px solid rgba(255,255,255,.16);border-radius:8px;background:#1c1c24;color:#e6e6e6;font-size:12px}
.sbstatus{margin-top:10px;padding-top:8px;border-top:1px solid rgba(255,255,255,.1);font-size:11px;color:#9ca3af;word-break:break-all}
.sberr{color:#f87171;font-size:11px;margin-top:6px;white-space:pre-wrap}
.sbmini{display:flex;gap:6px;align-items:center}
.sbmini button{padding:4px 8px;border:1px solid rgba(255,255,255,.16);border-radius:6px;background:rgba(255,255,255,.05);color:#d0d0d8;cursor:pointer;font-size:11px}
#ai-embedded-bridge-echo{width:14px;height:14px;accent-color:#2563eb;cursor:pointer;flex:none}
#ai-embedded-bridge-widget label{color:#d0d0d8;cursor:pointer;font-size:12px}
</style>
<script id="ai-embedded-bridge-script">
(function () {
  var BASE = '/plugins/ai-embedded-bridge';
  var ui = { mode: 'off', agents: [], target: '', buffered: 0, agentCount: 0, latest: undefined, err: '' };

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function api(path, body) {
    return fetch(BASE + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : '{}'
    }).then(function (r) { return r.json(); }).catch(function (e) { return { ok: false, error: String(e && e.message || e) }; });
  }

  // ---- 骨架 ----
  var root = document.createElement('div');
  root.id = 'ai-embedded-bridge-widget';

  var fab = el('div', 'sbfab');
  var dot = el('span', 'dot');
  fab.appendChild(dot);
  fab.appendChild(el('span', null, 'AI嵌入式工具'));
  fab.title = 'AI嵌入式工具';
  fab.addEventListener('click', function () {
    root.classList.toggle('open');
    if (root.classList.contains('open')) refresh();
  });

  var panel = el('div', 'sbpanel');
  panel.appendChild(el('h4', null, 'AI嵌入式工具'));

  var modeRow = el('div', 'sbrow');
  modeRow.appendChild(el('span', 'lbl', '总开关（off 不注入）'));
  var seg = el('div', 'sbseg');
  var modes = [['off', '关闭', 'danger'], ['chat', '聊天', ''], ['monitor', '监听', '']];
  var modeBtns = {};
  modes.forEach(function (m) {
    var b = el('button', m[2], m[1]);
    b.dataset.mode = m[0];
    b.addEventListener('click', function () { setMode(m[0]); });
    seg.appendChild(b);
    modeBtns[m[0]] = b;
  });
  modeRow.appendChild(seg);
  panel.appendChild(modeRow);

  var targetRow = el('div', 'sbrow');
  targetRow.appendChild(el('span', 'lbl', '注入会话（精准选择）'));
  var mini = el('div', 'sbmini');
  var sel = el('select', 'sbselect');
  sel.title = '选择数据注入到哪个 Agent 会话';
  sel.addEventListener('change', function () { setTarget(sel.value); });
  mini.appendChild(sel);
  var rbtn = el('button', null, '刷新');
  rbtn.addEventListener('click', refresh);
  mini.appendChild(rbtn);
  targetRow.appendChild(mini);
  panel.appendChild(targetRow);

  var status = el('div', 'sbstatus');
  panel.appendChild(status);
  var errBox = el('div', 'sberr');
  panel.appendChild(errBox);

  root.appendChild(fab);
  root.appendChild(panel);
  // 脚本在 </body> 前注入，document.body 一定已存在；只追加一次
  var appended = false;
  function mount() {
    if (appended) return;
    appended = true;
    document.body.appendChild(root);
  }
  document.addEventListener('DOMContentLoaded', mount);
  if (document.body) mount();

  // ---- 渲染 ----
  function render() {
    fab.className = 'sbfab ' + (ui.mode === 'chat' ? 'on' : ui.mode === 'monitor' ? 'mon' : '');
    Object.keys(modeBtns).forEach(function (k) {
      modeBtns[k].className = (k === ui.mode ? 'on ' : '') + (modeBtns[k].dataset.mode === 'off' ? 'danger' : '');
      modeBtns[k].className = modeBtns[k].className.trim();
    });
    var prev = sel.value;
    sel.textContent = '';
    var autoOpt = document.createElement('option');
    autoOpt.value = '';
    autoOpt.textContent = '自动（最新会话）' + (ui.latest ? '：' + ui.latest : '');
    sel.appendChild(autoOpt);
    (ui.agents || []).forEach(function (a) {
      var o = document.createElement('option');
      o.value = a.id || '';
      var label = (a.label || a.id || '(无 id)') + ' [' + (a.status || '?') + ']';
      o.textContent = label;
      o.title = (a.id || '') + (a.createdAt ? '\\n创建：' + new Date(a.createdAt).toLocaleString() : '');
      if (a.id === ui.latest) o.textContent += ' ★最新';
      sel.appendChild(o);
    });
    if (prev && Array.prototype.some.call(sel.options, function (o) { return o.value === prev; })) {
      sel.value = prev;
    } else {
      sel.value = ui.target || '';
    }
    var targetName = ui.target || '自动（最新）';
    if (ui.target) {
      var matched = (ui.agents || []).find(function (a) { return a.id === ui.target; });
      if (matched && matched.label) targetName = matched.label + ' (' + targetName + ')';
    }
    var modeName = ui.mode === 'off' ? '关闭' : ui.mode === 'chat' ? '聊天' : '监听';
    status.textContent = '状态：' + modeName +
      '\\n目标会话：' + targetName +
      '\\n在线会话：' + ui.agentCount +
      '，缓冲：' + ui.buffered + ' 字符';
    errBox.textContent = ui.err;
    errBox.style.display = ui.err ? 'block' : 'none';
  }

  function refresh() {
    api('/status', {}).then(function (s) {
      if (!s || !s.ok) { ui.err = (s && s.error) || '桥端点无响应'; render(); return; }
      ui.mode = s.mode || 'off';
      ui.agents = s.agents || [];
      ui.target = s.targetAgentId || '';
      ui.buffered = s.bufferedChars || 0;
      ui.agentCount = s.agentCount || 0;
      ui.latest = s.latestAgentId;
      ui.err = '';
      render();
    });
  }

  function setMode(m) {
    api('/mode', { mode: m }).then(function (r) {
      if (r && r.ok) { ui.mode = r.mode || m; ui.err = ''; }
      else ui.err = (r && r.error) || '切换失败';
      render();
    });
  }

  function setTarget(id) {
    api('/target', { agentId: id || '' }).then(function (r) {
      if (r && r.ok) { ui.target = r.targetAgentId || ''; ui.err = ''; }
      else ui.err = (r && r.error) || '设置会话失败';
      render();
    });
  }

  refresh();
  setInterval(refresh, 3000);
})();
</script>
`;
