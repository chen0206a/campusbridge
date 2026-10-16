import { useEffect, useState } from 'react';
import { api, errorText } from './api';
import { clearSession, readSession, saveSession } from './session';
import { Brand, Icon } from './icons';
import { Loading, Notice } from './components';
import { StudentWorkspace } from './StudentWorkspace';
import { CounselorWorkspace } from './CounselorWorkspace';
import type { User } from './types';
import { CampusScene, SupportGuide } from './SupportGuide';

export function Login({ onLogin, notice = '' }: { onLogin: (user: User) => void; notice?: string }) {
  const [mode, setMode] = useState<'student' | 'counselor'>('student'); const [username, setUsername] = useState(''); const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  async function submit(event: React.FormEvent) {
    event.preventDefault(); if (busy) return; setBusy(true); setError('');
    try {
      const result = await api<{ access_token: string; expires_in: number }>('/auth/login', { method: 'POST', body: JSON.stringify({ username: username.trim(), password }) });
      saveSession({ token: result.access_token, expiresAt: Date.now() + result.expires_in * 1000 });
      const user = await api<User>('/auth/me'); setPassword(''); onLogin(user);
    } catch (e) { clearSession(); setError(errorText(e)); } finally { setBusy(false); }
  }
  return <div className="login-page"><section className="login-story"><Brand/><div className="login-story-content"><span className="story-kicker"><span/>校园学生支持 · CAMPUSBRIDGE</span><h1>每一个小小的困惑，<br/>都值得被认真听见。</h1><p>把校园信息、支持建议与真实的关怀连起来。<br/>从一场对话开始，找到你的下一步。</p><CampusScene/><div className="story-features"><span><Icon name="chat" size={18}/>多轮支持对话</span><span><Icon name="book" size={18}/>校园知识有据可查</span><span><Icon name="user" size={18}/>人工关注与跟进</span></div></div><SupportGuide/><footer>CampusBridge · 连接信息，也连接关怀</footer></section>
    <main className="login-panel"><div className="login-mobile-brand"><Brand/></div><div className="login-card"><div className="login-card-symbol"><Icon name="spark" size={25}/></div><span className="eyebrow">WELCOME TO CAMPUSBRIDGE</span><h2>欢迎回来</h2><p>登录你的校园支持空间。</p><div className="login-tabs" role="tablist" aria-label="登录入口"><button role="tab" aria-selected={mode === 'student'} onClick={() => { setMode('student'); setUsername(''); setPassword(''); setError(''); }}><Icon name="chat" size={17}/>学生入口</button><button role="tab" aria-selected={mode === 'counselor'} onClick={() => { setMode('counselor'); setUsername(''); setPassword(''); setError(''); }}><Icon name="cases" size={17}/>辅导员入口</button></div>{notice && <Notice>{notice}</Notice>}{error && <Notice>{error}</Notice>}<form onSubmit={submit}><label htmlFor="username">账号</label><input id="username" name="username" autoComplete="username" placeholder={mode === 'student' ? '请输入学生账号' : '请输入辅导员账号'} value={username} onChange={event => setUsername(event.target.value)} maxLength={80} required disabled={busy}/><label htmlFor="password">密码</label><input id="password" type="password" name="password" autoComplete="current-password" placeholder="请输入密码" value={password} onChange={event => setPassword(event.target.value)} maxLength={128} required disabled={busy}/><button className="primary login-submit" type="submit" disabled={busy}>{busy ? '正在登录…' : mode === 'student' ? '进入学生空间' : '进入辅导员工作台'}<Icon name="arrow" size={18}/></button></form><div className="demo-account"><div><Icon name="leaf" size={17}/><strong>体验演示校园</strong><span>仅使用虚构账号与资料</span></div><button className="text-button" onClick={() => { setUsername(mode === 'student' ? 'student_lan' : 'counselor_yu'); setPassword('CampusDemo-2026!'); }}>填入演示账号</button></div><p className="login-note"><Icon name="shield" size={15}/>你的支持空间，仅向已授权的账号开放。</p></div><footer>用一点耐心，陪伴每一步。</footer></main>
  </div>;
}

export default function App() {
  const [user, setUser] = useState<User | null>(null); const [checking, setChecking] = useState(!!readSession()); const [notice, setNotice] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    if (readSession()) api<User>('/auth/me', { signal: controller.signal }).then(setUser).catch(e => { if (!controller.signal.aborted) { clearSession(); setNotice(errorText(e)); } }).finally(() => { if (!controller.signal.aborted) setChecking(false); });
    const unauthorized = () => { clearSession(); setUser(null); setChecking(false); setNotice('登录已过期，请重新登录。'); };
    window.addEventListener('campusbridge:unauthorized', unauthorized);
    return () => { controller.abort(); window.removeEventListener('campusbridge:unauthorized', unauthorized); };
  }, []);
  async function logout() { try { await api<void>('/auth/logout', { method: 'POST' }); } catch { /* Session is still removed locally if disconnected. */ } finally { clearSession(); setUser(null); setNotice(''); } }
  if (checking) return <div className="boot"><Brand/><Loading text="正在恢复登录…"/></div>;
  if (!user) return <Login notice={notice} onLogin={value => { setUser(value); setNotice(''); }}/>;
  return user.role === 'counselor' ? <CounselorWorkspace key={user.account_id} user={user} onLogout={logout}/> : <StudentWorkspace key={user.account_id} user={user} onLogout={logout}/>;
}
