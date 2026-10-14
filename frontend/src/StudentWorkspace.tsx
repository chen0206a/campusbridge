import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, errorText } from './api';
import { Avatar, Brand, Icon } from './icons';
import { Loading, MessageText, Notice, SourceDialog } from './components';
import { activeConversation, readPending, savePending } from './session';
import { streamReply } from './sse';
import type { Citation, Conversation, Message, Pending, Reply, User } from './types';

const starters = [
  { icon: 'book', title: '了解校园事务', text: '预约、借阅、助学与校园生活', question: '图书馆预约座位需要做什么？' },
  { icon: 'leaf', title: '梳理当下的感受', text: '给压力一个出口，找到小步骤', question: '最近作业有些多，我想先梳理一下压力。' },
  { icon: 'chat', title: '一起理清计划', text: '从学习安排到日常的小目标', question: '我想把接下来一周的学习安排梳理清楚。' },
];

export function StudentWorkspace({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [active, setActive] = useState<string | null>(activeConversation(user.account_id));
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(true); const [listError, setListError] = useState(''); const [error, setError] = useState('');
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
  return <div className="student-shell">
    {sidebar && <button className="sidebar-backdrop" aria-label="关闭侧边栏" onClick={() => setSidebar(false)}/>}
    <aside id="student-sidebar" className={'student-sidebar ' + (sidebar ? 'is-open' : '')}>
      <Brand/><div className="space-label"><span className="accent-dot"/>学生支持空间</div>
      <button className="primary new-chat" onClick={newConversation} disabled={busy || creating || !!pending}><Icon name="plus"/>新建对话</button>
      <div className="sidebar-section">最近的对话<button className="icon-button" aria-label="刷新会话列表" onClick={() => refreshList().catch(e => setListError(errorText(e)))}><Icon name="refresh" size={15}/></button></div>
      <nav className="conversation-list" aria-label="历史会话">{conversations.map(item => <button key={item.conversation_id} className={'conversation-link ' + (active === item.conversation_id ? 'active' : '')} disabled={busy} onClick={() => { setActive(item.conversation_id); setSidebar(false); }}><Icon name="chat" size={17}/><span>{item.title}<small>{new Date(item.updated_at).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })} · {item.message_count ? '已保存' : '新对话'}</small></span></button>)}{!conversations.length && !loading && <p className="sidebar-empty">你的对话会保存在这里。</p>}</nav>
      {listError && <Notice retry={() => refreshList().catch(e => setListError(errorText(e)))}>{listError}</Notice>}
      <div className="sidebar-support"><Icon name="leaf"/><strong>慢慢说，我们一起梳理。</strong><p>从一件小事开始，也是一种前进。</p></div>
      <footer className="account"><Avatar name={user.display_name}/><span>{user.display_name}<small>学生账号</small></span><button className="icon-button" aria-label="退出登录" onClick={onLogout}><Icon name="logout"/></button></footer>
    </aside>
    <main className="student-main">
      <header className="workspace-header"><div><button className="icon-button mobile-menu" aria-label="打开会话列表" aria-expanded={sidebar} aria-controls="student-sidebar" onClick={() => setSidebar(true)}><Icon name="menu"/></button><span className="breadcrumb">CampusBridge <span>/</span> 学生支持</span></div><span className="private-label"><Icon name="shield" size={16}/>你的专属对话空间</span></header>
      <div className="chat-scroll">
        {loading ? <Loading text="正在恢复对话…"/> : !messages.length && !currentPending ? <section className="welcome"><div className="welcome-emblem"><Icon name="leaf" size={34}/></div><span className="eyebrow">A LITTLE SUPPORT, A NEW START</span><h1>今天，想从哪里开始？</h1><p>校园里的事务、学习中的压力，或是一个还没想清楚的念头。<br/>在这里，给自己一点整理的时间。</p><div className="starter-grid">{starters.map(item => <button className="starter-card" key={item.title} onClick={() => send(item.question)} disabled={busy || !!pending}><span className="starter-icon"><Icon name={item.icon} size={23}/></span><strong>{item.title}</strong><span>{item.text}</span><Icon name="arrow" size={18}/></button>)}</div><div className="welcome-foot"><Icon name="book" size={16}/>校园资料有来源，支持建议有温度。</div></section> : <section className="message-list" aria-label="对话内容">
          {messages.map(message => <article key={message.message_id} className={'message-row ' + message.role}><span className={'message-avatar ' + message.role}>{message.role === 'assistant' ? <Brand compact/> : <Avatar name={user.display_name}/>}</span><div className="message-content"><div className="message-author">{message.role === 'assistant' ? 'CampusBridge' : '你'}<small>{message.role === 'assistant' ? '校园支持' : ''}</small></div><MessageText text={message.text} citations={message.citations} onSource={setCitation}/></div></article>)}
          {currentPending && !pendingAlreadySaved && <article className="message-row user"><span className="message-avatar user"><Avatar name={user.display_name}/></span><div className="message-content"><div className="message-author">你</div><div className="message-text">{currentPending.message}</div></div></article>}
          {currentPending && !streamingAlreadySaved && <article className="message-row assistant"><span className="message-avatar assistant"><Brand compact/></span><div className="message-content"><div className="message-author">CampusBridge</div>{streamText ? <div className="message-text streaming">{streamText.replace(/\[source:[^\]]*(?:\]|$)/g, '')}</div> : busy ? <Loading text="正在准备回复…"/> : <span className="muted">等待继续接收回复</span>}</div></article>}
          <div ref={bottom}/>
        </section>}
      </div>
      <div className="composer-wrap">{error && <Notice retry={() => { if (currentPending) void receive(currentPending); else if (active) void savedHistory(); }}>{error}{currentPending && !busy && <button className="text-button" onClick={() => receive(currentPending)}>继续接收</button>}</Notice>}{currentPending && !busy && <button className="text-button saved-link" onClick={savedHistory}>查看已保存的对话</button>}
        <form className="composer" onSubmit={event => { event.preventDefault(); void send(); }}><textarea aria-label="输入消息" placeholder={pending ? '先继续接收上一次回复，或查看已保存的对话' : '说说你想了解的，或此刻的感受…'} rows={2} value={draft} maxLength={3000} disabled={busy || !!pending || loading} onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); } }}/><div className="composer-bottom"><span><Icon name="leaf" size={15}/>{busy ? '正在回复，可以慢慢等一会儿' : 'Enter 发送 · Shift + Enter 换行'}</span><button className="send-button" aria-label="发送消息" type="submit" disabled={busy || !!pending || loading || !draft.trim()}><Icon name="send" size={18}/></button></div></form>
        <p className="support-footnote">这里提供校园支持与信息参考。如有即时危险，请尽快联系身边可信任的人或当地紧急服务。</p>
      </div>
    </main>{citation && <SourceDialog citation={citation} onClose={closeSource}/>}
  </div>;
}
