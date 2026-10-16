import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, errorText } from './api';
import { Avatar, Brand, Icon } from './icons';
import { Loading, MessageText, Notice, SourceDialog } from './components';
import { activeConversation, readPending, savePending } from './session';
import { streamReply } from './sse';
import { SupportGuide } from './SupportGuide';
import type { Citation, Conversation, Message, Pending, Reply, User } from './types';

const starters = [
  { icon: 'book', title: '校园事务', text: '图书馆预约、借阅、助学申请', question: '图书馆预约座位需要做什么？' },
  { icon: 'chat', title: '学习压力', text: '讨论作业、考试和校园适应问题', question: '最近作业有些多，我想先梳理一下压力。' },
  { icon: 'calendar', title: '学习安排', text: '整理近期任务，安排学习时间', question: '我想把接下来一周的学习安排梳理清楚。' },
  { icon: 'users', title: '联系辅导员', text: '提交需要工作人员跟进的事项', question: '我想找辅导员聊聊最近适应校园生活的困难。' },
];

export function StudentWorkspace({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [active, setActive] = useState<string | null>(activeConversation(user.account_id));
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(true); const [listError, setListError] = useState(''); const [error, setError] = useState('');
  const [topic, setTopic] = useState('全部');
  const [draft, setDraft] = useState(''); const [busy, setBusy] = useState(false); const [creating, setCreating] = useState(false);
  const [pending, setPending] = useState<Pending | null>(readPending(user.account_id));
  const [streamText, setStreamText] = useState(''); const [sidebar, setSidebar] = useState(false); const [citation, setCitation] = useState<Citation | null>(null);
  const streamController = useRef<AbortController | null>(null); const lock = useRef(false); const bottom = useRef<HTMLDivElement>(null);
  const historySequence = useRef(0);
  const submitLock = useRef(false);
  const closeSource = useCallback(() => setCitation(null), []);

  const refreshList = useCallback(async (signal?: AbortSignal) => {
    const data = await api<{ conversations: Conversation[] }>('/conversations', { signal });
    setConversations(data.conversations); setListError(''); return data.conversations;
  }, []);
  const history = useCallback(async (id: string, signal?: AbortSignal) => {
    const sequence = ++historySequence.current;
    const data = await api<{ messages: Message[] }>('/conversations/' + encodeURIComponent(id), { signal });
    const result: Message[] = [];
    // Bounded batches avoid flooding the backend on long conversations.
    for (let offset = 0; offset < data.messages.length; offset += 4) {
      const batch = await Promise.all(data.messages.slice(offset, offset + 4).map(async message => message.role === 'assistant'
        ? { ...message, citations: (await api<Reply>('/runs/' + encodeURIComponent(message.run_id), { signal })).citations } : message));
      result.push(...batch);
    }
    if (!signal?.aborted && sequence === historySequence.current) setMessages(result);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    refreshList(controller.signal).then(items => {
      const saved = activeConversation(user.account_id);
      const chosen = saved && items.some(item => item.conversation_id === saved) ? saved : items[0]?.conversation_id || null;
      setActive(chosen); if (!chosen) setLoading(false);
    }).catch(e => { if (!controller.signal.aborted) { setListError(errorText(e)); setLoading(false); } });
    return () => { controller.abort(); streamController.current?.abort(); };
  }, [user.account_id, refreshList]);
  useEffect(() => {
    if (!active) { setMessages([]); setLoading(false); return; }
    const controller = new AbortController(); activeConversation(user.account_id, active); setLoading(true);
    if (!lock.current) { setError(''); setStreamText(''); }
    history(active, controller.signal).then(() => {
      const saved = readPending(user.account_id);
      if (saved?.conversationId === active && !lock.current) { setPending(saved); setError('上次回复连接中断，点击「继续接收」可恢复。'); }
    }).catch(e => { if (!controller.signal.aborted) setError(errorText(e)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [active, user.account_id, history]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'end', behavior: 'smooth' }); }, [messages.length, streamText, busy]);

  async function newConversation() {
    if (lock.current || pending || creating) return;
    setCreating(true); setError('');
    try { const created = await api<{ conversation_id: string }>('/conversations', { method: 'POST' }); await refreshList(); setActive(created.conversation_id); setSidebar(false); }
    catch (e) { setError(errorText(e)); } finally { setCreating(false); }
  }
  async function receive(request: Pending) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(''); setStreamText(''); setPending(request); savePending(user.account_id, request);
    const controller = new AbortController(); streamController.current = controller;
    try {
      let runId = request.runId;
      await streamReply(request, event => {
        if (event.event === 'meta') {
          runId = event.data.run_id as string; request = { ...request, runId }; setPending(request); savePending(user.account_id, request);
        }
        if (event.event === 'token') setStreamText(text => text + String(event.data.text));
      }, controller.signal);
      if (!runId) throw new Error('RUN_ID_MISSING');
      await api<Reply>('/runs/' + encodeURIComponent(runId), { signal: controller.signal });
      await history(request.conversationId, controller.signal);
      savePending(user.account_id, null); setPending(null); setStreamText(''); await refreshList(controller.signal);
    } catch (e) {
      // Keep the same operation identity. A retry replays the existing run instead of creating a new turn.
      if (!controller.signal.aborted || streamController.current === controller) setError(errorText(e));
    } finally { setBusy(false); lock.current = false; streamController.current = null; }
  }
  async function send(text = draft) {
    const message = text.trim(); if (!message || message.length > 3000 || lock.current || submitLock.current || pending) return;
    submitLock.current = true; setBusy(true); setError('');
    try {
      let id = active;
      if (!id) { const created = await api<{ conversation_id: string }>('/conversations', { method: 'POST' }); id = created.conversation_id; await refreshList(); setActive(id); }
      setDraft(''); await receive({ conversationId: id, key: crypto.randomUUID(), message });
    } catch (e) { setError(errorText(e)); } finally { submitLock.current = false; setBusy(false); }
  }
  async function savedHistory() {
    if (!active || busy) return;
    setLoading(true);
    try {
      if (pending?.runId) {
        const result = await api<{ state: string }>('/runs/' + encodeURIComponent(pending.runId));
        if (result.state === 'PROCESSING') { setError('回复仍在准备中，请继续接收。'); return; }
      } else if (pending) { setError('请求状态尚未确认，请继续接收，系统将复用原来的提交。'); return; }
      await history(active); savePending(user.account_id, null); setPending(null); setStreamText(''); setError(''); await refreshList();
    }
    catch (e) {
      if (e instanceof ApiError && e.code === 'RUN_FAILED') {
        await history(active).catch(() => undefined); savePending(user.account_id, null); setPending(null); setStreamText('');
        setError('本次回复未完成，已恢复保存的对话。可以发送新消息。');
      } else setError(errorText(e));
    } finally { setLoading(false); }
  }
  const currentPending = pending?.conversationId === active ? pending : null;
  const pendingAlreadySaved = currentPending?.runId && messages.some(message => message.role === 'user' && message.run_id === currentPending.runId);
  const streamingAlreadySaved = currentPending?.runId && messages.some(message => message.role === 'assistant' && message.run_id === currentPending.runId);
  const sources = loading ? [] : Array.from(new Map(messages.flatMap(message => message.citations || []).map(source => [source.source_id, source])).values());
  const topics = ['全部', '校园事务', '情绪与计划', '人工支持'];
  const visibleStarters = starters.filter((_, index) => topic === '全部' || (topic === '校园事务' ? index === 0 : topic === '人工支持' ? index === 3 : index === 1 || index === 2));
  return <div className="student-shell">
    {sidebar && <button className="sidebar-backdrop" aria-label="关闭侧边栏" onClick={() => setSidebar(false)}/>}
    <aside id="student-sidebar" className={'student-sidebar ' + (sidebar ? 'is-open' : '')}>
      <Brand/><div className="space-label"><span className="accent-dot"/>学生端</div>
      <button className="primary new-chat" onClick={newConversation} disabled={busy || creating || !!pending}><Icon name="plus"/>新建对话</button>
      <div className="sidebar-section">最近的对话 <span className="count-pill">{conversations.length}</span><button className="icon-button" aria-label="刷新会话列表" onClick={() => refreshList().catch(e => setListError(errorText(e)))}><Icon name="refresh" size={15}/></button></div>
      <nav className="conversation-list" aria-label="历史会话">{conversations.map(item => <button key={item.conversation_id} className={'conversation-link ' + (active === item.conversation_id ? 'active' : '')} disabled={busy} onClick={() => { setActive(item.conversation_id); setSidebar(false); }}><Icon name="chat" size={17}/><span>{item.title}<small>{new Date(item.updated_at).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })} · {item.message_count ? '已保存' : '新对话'}</small></span></button>)}{!conversations.length && !loading && <p className="sidebar-empty">你的对话会保存在这里。</p>}</nav>
      {listError && <Notice retry={() => refreshList().catch(e => setListError(errorText(e)))}>{listError}</Notice>}
      <div className="sidebar-support"><Icon name="clock"/><strong>历史会话</strong><p>对话保存后，可从上方列表继续查看。</p></div>
      <footer className="account"><Avatar name={user.display_name}/><span>{user.display_name}<small>学生账号</small></span><button className="icon-button" aria-label="退出登录" onClick={onLogout}><Icon name="logout"/></button></footer>
    </aside>
    <main className="student-main">
      <header className="workspace-header"><div><button className="icon-button mobile-menu" aria-label="打开会话列表" aria-expanded={sidebar} aria-controls="student-sidebar" onClick={() => setSidebar(true)}><Icon name="menu"/></button><span className="breadcrumb">CampusBridge <span>/</span> 学生支持</span></div><span className="private-label"><Icon name="shield" size={16}/>个人会话</span></header>
      <div className="student-body"><div className="conversation-column"><div className="chat-scroll">
        {loading ? <Loading text="正在恢复对话…"/> : !messages.length && !currentPending ? <section className="welcome"><div className="welcome-intro"><span className="welcome-emblem"><Icon name="chat" size={29}/></span><span>{user.display_name}<small>学生账号</small></span><span className="welcome-edition">对话 / 资料 / 人工协助</span></div><span className="eyebrow">学生端 / 对话</span><h1>新对话</h1><p>选择一个话题，或在下方输入问题。</p><div className="topic-tabs" aria-label="话题分类">{topics.map(value => <button key={value} aria-pressed={topic === value} onClick={() => setTopic(value)}>{value}</button>)}</div><div className="starter-grid">{visibleStarters.map(item => <button className="starter-card" key={item.title} onClick={() => send(item.question)} disabled={busy || !!pending}><span className="starter-icon"><Icon name={item.icon} size={23}/></span><strong>{item.title}</strong><span>{item.text}</span><Icon name="arrow" size={18}/></button>)}</div><SupportGuide/></section> : <section className="message-list" aria-label="对话内容"><div className="conversation-heading"><span><Icon name="chat" size={17}/>对话记录</span><small>{messages.filter(message => message.role === 'user').length} 条已保存提问</small></div>
          {messages.map(message => <article key={message.message_id} className={'message-row ' + message.role}><span className={'message-avatar ' + message.role}>{message.role === 'assistant' ? <Brand compact/> : <Avatar name={user.display_name}/>}</span><div className="message-content"><div className="message-author">{message.role === 'assistant' ? 'CampusBridge' : '你'}<small>{message.role === 'assistant' ? '校园支持' : ''}</small></div><MessageText text={message.text} citations={message.citations} onSource={setCitation}/></div></article>)}
          {currentPending && !pendingAlreadySaved && <article className="message-row user"><span className="message-avatar user"><Avatar name={user.display_name}/></span><div className="message-content"><div className="message-author">你</div><div className="message-text">{currentPending.message}</div></div></article>}
          {currentPending && !streamingAlreadySaved && <article className="message-row assistant"><span className="message-avatar assistant"><Brand compact/></span><div className="message-content"><div className="message-author">CampusBridge</div>{streamText ? <div className="message-text streaming">{streamText.replace(/\[source:[^\]]*(?:\]|$)/g, '')}</div> : busy ? <Loading text="正在准备回复…"/> : <span className="muted">等待继续接收回复</span>}</div></article>}
          <div ref={bottom}/>
        </section>}
      </div>
      <div className="composer-wrap">{error && <Notice retry={() => { if (currentPending) void receive(currentPending); else if (active) void savedHistory(); }}>{error}{currentPending && !busy && <button className="text-button" onClick={() => receive(currentPending)}>继续接收</button>}</Notice>}{currentPending && !busy && <button className="text-button saved-link" onClick={savedHistory}>查看已保存的对话</button>}
        <form className="composer" onSubmit={event => { event.preventDefault(); void send(); }}><textarea aria-label="输入消息" placeholder={pending ? '先继续接收上一次回复，或查看已保存的对话' : '输入问题…'} rows={2} value={draft} maxLength={3000} disabled={busy || !!pending || loading} onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); } }}/><div className="composer-bottom"><span><Icon name="shield" size={15}/>{busy ? '正在生成回复…' : 'Enter 发送 · Shift + Enter 换行'}</span><small className="draft-count">{draft.length} / 3000</small><button className="send-button" aria-label="发送消息" type="submit" disabled={busy || !!pending || loading || !draft.trim()}><Icon name="send" size={18}/></button></div></form>
        <p className="support-footnote">这里提供校园支持与信息参考。如有即时危险，请尽快联系身边可信任的人或当地紧急服务。</p>
      </div>
      </div><aside className="student-context-panel" aria-label="对话支持信息">
        <section className="context-note"><span className="eyebrow">使用说明</span><h2>提问与资料</h2><p>校园事务可写明办理事项。回复附有引用时，可查看资料原文及版本。</p><ul className="context-instructions"><li>历史记录在左侧会话列表</li><li>Shift + Enter 输入换行</li><li>下方输入框提交问题</li></ul></section>
        <section className="reference-panel"><header><span className="rail-icon"><Icon name="book" size={18}/></span><h2>参考资料</h2><span className="count-pill">{sources.length}</span></header>{sources.length ? <div className="reference-list">{sources.map(source => <button key={source.source_id} onClick={() => setCitation(source)}><span><strong>{source.title}</strong><small>版本 {source.revision} · 查看原文</small></span><Icon name="chevron" size={15}/></button>)}</div> : <div className="reference-empty"><Icon name="layers" size={28}/><p>本次对话暂无引用资料。</p><small>有引用的回复会在此列出来源。</small></div>}</section>
        <section className="support-tip"><Icon name="users" size={19}/><div><strong>人工协助</strong><p>需要辅导员跟进时，在对话中说明具体事项。</p></div></section>
        <span className="rail-footer">CampusBridge / 学生端</span>
      </aside></div>
    </main>{citation && <SourceDialog citation={citation} onClose={closeSource}/>}
  </div>;
}
