import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, errorText } from './api';
import { Avatar, Brand, Icon } from './icons';
import { Loading, Modal, Notice } from './components';
import type { CaseDetail, CaseItem, CaseState, StudentContext, User } from './types';

export const stateLabels: Record<CaseState, string> = { OPEN: '待认领', CLAIMED: '已认领', IN_PROGRESS: '处理中', CLOSED: '已关闭' };
const states = ['OPEN', 'CLAIMED', 'IN_PROGRESS', 'CLOSED'] as CaseState[];
export function StatusBadge({ state }: { state: CaseState }) { return <span className={'status-badge ' + state.toLowerCase()}><span/>{stateLabels[state]}<small>{state}</small></span>; }
function date(value: string) { return new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }); }
type Operation = { caseId: string; action: string; note: string; version: number; key: string };

export function CounselorWorkspace({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [cases, setCases] = useState<CaseItem[]>([]); const [filter, setFilter] = useState<CaseState | 'ALL'>('ALL');
  const [search, setSearch] = useState(''); const [mineOnly, setMineOnly] = useState(false);
  const [active, setActive] = useState<string | null>(null); const [detail, setDetail] = useState<CaseDetail | null>(null); const [context, setContext] = useState<StudentContext | null>(null);
  const [loading, setLoading] = useState(true); const [detailLoading, setDetailLoading] = useState(false); const [error, setError] = useState(''); const [detailError, setDetailError] = useState('');
  const [note, setNote] = useState(''); const [acting, setActing] = useState(false); const [success, setSuccess] = useState(''); const [confirmClose, setConfirmClose] = useState(false);
  const operation = useRef<Operation | null>(null); const actionLock = useRef(false);
  const refresh = useCallback(async (signal?: AbortSignal) => { const data = await api<{ cases: CaseItem[] }>('/cases', { signal }); setCases([...data.cases].reverse()); setError(''); }, []);
  const fetchDetail = useCallback(async (id: string, signal?: AbortSignal) => {
    const [data, student] = await Promise.all([api<CaseDetail>('/cases/' + encodeURIComponent(id), { signal }), api<StudentContext>('/cases/' + encodeURIComponent(id) + '/student-context', { signal })]);
    if (!signal?.aborted) { setDetail(data); setContext(student); }
  }, []);
  useEffect(() => { const controller = new AbortController(); refresh(controller.signal).catch(e => { if (!controller.signal.aborted) setError(errorText(e)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); }); return () => controller.abort(); }, [refresh]);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController(); setDetail(null); setContext(null); setDetailError(''); setSuccess(''); setNote(''); setDetailLoading(true); operation.current = null;
    fetchDetail(active, controller.signal).catch(e => { if (!controller.signal.aborted) setDetailError(errorText(e)); }).finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [active, fetchDetail]);
  async function perform(action: 'claim' | 'process' | 'close') {
    if (!detail || actionLock.current) return;
    if (action !== 'claim' && note.trim().length < 3) { setDetailError('请填写至少 3 个字的处理记录。'); return; }
    actionLock.current = true; setActing(true); setDetailError(''); setSuccess('');
    const payload = { caseId: detail.case_id, action, note: action === 'claim' ? '' : note.trim(), version: detail.version };
    if (!operation.current || Object.keys(payload).some(key => payload[key as keyof typeof payload] !== operation.current![key as keyof typeof payload])) operation.current = { ...payload, key: crypto.randomUUID() };
    const op = operation.current;
    try {
      const result = await api<{ idempotent_replay: boolean }>('/cases/' + encodeURIComponent(op.caseId) + '/' + action,
        { method: 'POST', headers: { 'Idempotency-Key': op.key }, body: JSON.stringify({ expected_version: op.version, note: op.note }) });
      setSuccess(result.idempotent_replay ? '该操作已完成，已恢复原来的处理结果。' : action === 'claim' ? '工单已认领。' : action === 'close' ? '工单已关闭，处理记录已保存。' : '处理记录已保存。');
      await fetchDetail(op.caseId); await refresh(); setNote(''); operation.current = null; setConfirmClose(false);
    } catch (e) {
      setDetailError(errorText(e));
      if (e instanceof ApiError && e.status === 409) { operation.current = null; await fetchDetail(op.caseId).catch(() => undefined); await refresh().catch(() => undefined); }
      // Transport uncertainty keeps the same idempotency key for a safe retry.
    } finally { setActing(false); actionLock.current = false; }
  }
  const query = search.trim().toLowerCase();
  const visible = cases.filter(item => (filter === 'ALL' || item.state === filter)
    && (!mineOnly || item.owner_id === user.account_id)
    && (!query || ('CB-' + item.case_id.slice(0, 6)).toLowerCase().includes(query) || item.case_id.toLowerCase().includes(query) || item.student_id.toLowerCase().includes(query)));
  const myActive = cases.filter(item => item.owner_id === user.account_id && item.state !== 'CLOSED').length;
  const closed = cases.filter(item => item.state === 'CLOSED').length;
  const owned = detail?.owner_id === user.account_id;
  const count = (state: CaseState) => cases.filter(item => item.state === state).length;
  return <div className="counselor-shell">
    <aside className="counselor-sidebar"><Brand/><div className="space-label">辅导员工作台</div><button className="nav-selected"><Icon name="cases"/>学生支持工单<span>{cases.length}</span></button><div className="work-guide"><Icon name="shield" size={24}/><strong>处理流程</strong><p>认领工单后填写处理记录。<br/>事项处理完毕后确认关闭。</p></div><footer className="account"><Avatar name="辅导员"/><span>{user.display_name}<small>{user.username}</small></span><button className="icon-button" aria-label="退出登录" onClick={onLogout}><Icon name="logout"/></button></footer></aside>
    <main className="counselor-main"><header className="workspace-header"><span className="breadcrumb">CampusBridge <span>/</span> 辅导员工作台</span><span className="private-label"><Icon name="shield" size={16}/>人工支持与跟进</span></header>
      <div className="work-content"><div className="work-heading"><div><span className="eyebrow">辅导员端 / 工单</span><h1>工单管理</h1><p>认领事项、填写处理记录、查看操作历史。</p></div><button className="secondary" onClick={() => refresh().catch(e => setError(errorText(e)))} disabled={acting}><Icon name="refresh" size={16}/>刷新工单</button></div>
        <div className="work-overview"><div><span className="overview-icon"><Icon name="target" size={23}/></span><span><strong>{user.display_name}的工作概览</strong><small>统计范围：当前账号可访问的工单列表。</small></span></div><div className="overview-numbers"><span><strong>{myActive}</strong>由我跟进 · 未关闭</span><span><strong>{closed}<small> / {cases.length}</small></strong>已关闭 / 当前列表</span></div></div><div className="stats-grid">{states.map(state => <button aria-pressed={filter === state} className={'stat-card stat-' + state.toLowerCase() + ' ' + (filter === state ? 'selected' : '')} key={state} onClick={() => setFilter(state)}><span>{stateLabels[state]}<Icon name={state === 'CLOSED' ? 'check' : state === 'OPEN' ? 'cases' : 'clock'} size={18}/></span><strong>{count(state)}</strong><small>{state === 'OPEN' ? '等待工作人员接手' : state === 'CLAIMED' ? '已分配跟进责任人' : state === 'IN_PROGRESS' ? '持续记录支持情况' : '处理流程已完成'}</small></button>)}</div>
        {error && <Notice retry={() => refresh().catch(e => setError(errorText(e)))}>{error}</Notice>}
        <div className="queue-toolbar"><label className="case-search"><Icon name="search" size={18}/><input aria-label="搜索工单" value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索工单编号 / 学生 ID"/>{search && <button className="icon-button" aria-label="清空搜索" onClick={() => setSearch('')}><Icon name="close" size={15}/></button>}</label><button className={'ownership-filter ' + (mineOnly ? 'selected' : '')} aria-pressed={mineOnly} onClick={() => setMineOnly(!mineOnly)}><Icon name="user" size={16}/>由我跟进</button><span className="queue-summary" aria-live="polite">当前显示 {visible.length} / {cases.length} 条</span></div><div className="case-layout"><section className="case-list-panel"><header className="panel-heading"><h2>支持工单</h2><span>{visible.length} 条</span></header><div className="filter-tabs" aria-label="状态筛选"><button aria-pressed={filter === 'ALL'} className={filter === 'ALL' ? 'selected' : ''} onClick={() => setFilter('ALL')}>全部</button>{states.map(state => <button key={state} aria-pressed={filter === state} className={filter === state ? 'selected' : ''} onClick={() => setFilter(state)}>{stateLabels[state]}</button>)}</div>
          {loading ? <Loading/> : visible.length === 0 ? <div className="empty-cases"><Icon name="check" size={30}/><h3>{search || mineOnly ? '没有匹配的工单' : cases.length ? '当前筛选下暂无工单' : '暂时没有待跟进的事项'}</h3><p>{search || mineOnly ? '试试其他关键词，或调整筛选条件。' : '新的学生支持事项会出现在这里。'}</p>{(search || mineOnly || filter !== 'ALL') && <button className="text-button" onClick={() => { setSearch(''); setMineOnly(false); setFilter('ALL'); }}>重置筛选</button>}</div> : <div className="case-list">{visible.map(item => <button key={item.case_id} className={'case-card ' + (active === item.case_id ? 'selected' : '')} onClick={() => setActive(item.case_id)} disabled={acting}><div><span className="case-code">CB-{item.case_id.slice(0, 6).toUpperCase()}</span><Icon name="chevron" size={17}/></div><strong>学生支持与跟进</strong><p><Icon name="user" size={14}/>学生 {item.student_id.slice(0, 6)} · {item.owner_id ? item.owner_id === user.account_id ? '由你跟进' : '已有负责人' : '等待认领'}</p><StatusBadge state={item.state}/></button>)}</div>}
        </section><section className="case-detail-panel" aria-label="工单详情">{!active ? <div className="detail-placeholder"><span><Icon name="cases" size={36}/></span><span className="eyebrow">工单详情</span><h2>尚未选择工单</h2><p>从左侧列表选择工单，查看原始诉求和处理记录。</p><div className="placeholder-steps"><span><Icon name="user" size={17}/>认领事项</span><Icon name="arrow" size={15}/><span><Icon name="chat" size={17}/>记录跟进</span><Icon name="arrow" size={15}/><span><Icon name="check" size={17}/>确认关闭</span></div></div> : detailLoading ? <Loading text="正在加载工单…"/> : !detail ? <Notice retry={() => fetchDetail(active).catch(e => setDetailError(errorText(e)))}>{detailError || '无法加载工单，请重试。'}</Notice> : <>
          <div className="detail-heading"><div><span className="case-code">CB-{detail.case_id.slice(0, 6).toUpperCase()}</span><h2>{context?.student_display_name || '学生'}的支持事项</h2><p>{context && date(context.created_at)} · 版本 {detail.version}</p></div><StatusBadge state={detail.state}/></div>
          <div className="case-progress">{states.map((state, index) => <div key={state} className={states.indexOf(detail.state) >= index ? 'reached' : ''}><span>{states.indexOf(detail.state) > index ? <Icon name="check" size={12}/> : index + 1}</span><small>{stateLabels[state]}</small></div>)}</div>
          <section className="concern"><h3><Icon name="chat" size={17}/>学生表达</h3><blockquote>{context?.request_text}</blockquote><p>作为人工跟进线索，请结合实际情况沟通确认。</p></section>
          {detailError && <Notice>{detailError}</Notice>}{success && <div className="success-notice" role="status"><Icon name="check" size={17}/>{success}</div>}
          {detail.state === 'OPEN' ? <div className="claim-box"><div><strong>待认领</strong><p>认领后可填写处理记录。</p></div><button className="primary" disabled={acting} onClick={() => perform('claim')}><Icon name="user" size={17}/>{acting ? '正在认领…' : '认领工单'}</button></div> : detail.state === 'CLOSED' ? <div className="closed-box"><Icon name="check"/>该工单已关闭，所有处理记录已保留。</div> : !owned ? <Notice>该工单已由其他工作人员认领。你可以查看记录，处理操作由负责人完成。</Notice> : <section className="handling"><h3>添加处理记录</h3><label htmlFor="handling-note">沟通情况与下一步安排</label><textarea id="handling-note" value={note} onChange={event => setNote(event.target.value)} placeholder="记录已完成的沟通、支持措施与后续安排…" rows={4} maxLength={2000} disabled={acting}/><div className="handling-actions"><span>记录会保存到操作历史</span><button className="secondary" onClick={() => setConfirmClose(true)} disabled={acting || detail.state !== 'IN_PROGRESS'}>关闭工单</button><button className="primary" onClick={() => perform('process')} disabled={acting}>{acting ? '正在保存…' : detail.state === 'CLAIMED' ? '保存并进入处理中' : '保存处理记录'}</button></div></section>}
          <section className="timeline"><h3>操作历史 <span>{detail.events.length}</span></h3>{[...detail.events].reverse().map(event => <div className="timeline-item" key={event.version}><span className="timeline-dot"/><div><header><strong>{event.action === 'CREATE' ? '创建支持事项' : event.action === 'CLAIM' ? '认领工单' : event.action === 'PROCESS' ? '记录处理情况' : '关闭工单'}</strong><time>{date(event.created_at)}</time></header><p>{event.note || '已认领，等待进一步跟进。'}</p><small>{event.action === 'CREATE' ? '支持系统' : event.actor_id === user.account_id ? '你' : '工作人员'} · {stateLabels[event.to_state]}</small></div></div>)}</section>
        </>}</section></div>
      </div>
    </main>{confirmClose && <Modal title="确认关闭工单" onClose={() => { if (!acting) setConfirmClose(false); }}><div className="close-confirm"><p>请确认本次跟进已完成，并填写关闭依据。操作和记录将保留在工单历史中。</p><label htmlFor="close-note">关闭依据与后续支持安排</label><textarea id="close-note" rows={4} maxLength={2000} value={note} onChange={event => setNote(event.target.value)} disabled={acting}/>{detailError && <Notice>{detailError}</Notice>}<div><button className="secondary" disabled={acting} onClick={() => setConfirmClose(false)}>继续跟进</button><button className="primary" disabled={acting} onClick={() => perform('close')}>{acting ? '正在关闭…' : '确认关闭'}</button></div></div></Modal>}
  </div>;
}
