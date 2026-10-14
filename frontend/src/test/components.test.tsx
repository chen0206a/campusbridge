import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Login } from '../App';
import { MessageText, SourceDialog } from '../components';
import { api, checkResponse } from '../api';
import { clearSession, readSession, saveSession } from '../session';
import type { Citation } from '../types';

const citation: Citation = { source_id: 'source-original', document_id: 'library', title: '图书馆预约', revision: 'v1', quote: '原始资料', source_hash: 'hash' };
afterEach(() => vi.unstubAllGlobals());
test('model text is escaped, grounded citations are clickable', async () => {
  const open = vi.fn(); render(<MessageText text={'<script>alert("x")</script>资料[source:source-original]'} citations={[citation]} onSource={open}/>);
  expect(document.querySelector('script')).toBeNull(); expect(screen.getByText(/<script>/)).toBeInTheDocument();
  expect(screen.queryByText('[source:source-original]')).not.toBeInTheDocument(); await userEvent.click(screen.getByRole('button', { name: /图书馆预约/ })); expect(open).toHaveBeenCalledWith(citation);
});
test('source details load authorized text and Escape closes dialog', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ title: citation.title, text: '原始资料', author: 'CampusBridge', license: 'MIT', active_revision: true }), { status: 200 })));
  const close = vi.fn(); render(<SourceDialog citation={citation} onClose={close}/>);
  expect(await screen.findByText('原始资料')).toBeInTheDocument(); expect(screen.getByText(/MIT/)).toBeInTheDocument();
  await userEvent.keyboard('{Escape}'); expect(close).toHaveBeenCalled();
});
test('source failures offer retry', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(new Response('{"detail":"REQUEST_UNAVAILABLE"}', { status: 503 })).mockResolvedValueOnce(new Response(JSON.stringify({ text: '恢复的资料', author: 'Original', license: 'MIT' })));
  vi.stubGlobal('fetch', fetcher); render(<SourceDialog citation={citation} onClose={() => {}}/>);
  await userEvent.click(await screen.findByRole('button', { name: '重试' })); expect(await screen.findByText('恢复的资料')).toBeInTheDocument();
});
test('login uses server profile, not selected role; password is never stored', async () => {
  const handler = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ access_token: 'synthetic-token', expires_in: 3600 })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ account_id: 'counselor', role: 'counselor', display_name: '辅导员' })));
  vi.stubGlobal('fetch', handler); const logged = vi.fn(); render(<Login onLogin={logged}/>);
  await userEvent.click(screen.getByRole('button', { name: '填入演示账号' })); await userEvent.click(screen.getByRole('button', { name: '进入学生空间' }));
  await waitFor(() => expect(logged).toHaveBeenCalledWith(expect.objectContaining({ role: 'counselor' })));
  expect(readSession()?.token).toBe('synthetic-token'); expect(JSON.stringify(sessionStorage)).not.toContain('CampusDemo-2026!');
});
test('bad login shows a useful error and leaves no session', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"INVALID_CREDENTIALS"}', { status: 401 })));
  render(<Login onLogin={() => {}}/>); await userEvent.click(screen.getByRole('button', { name: '填入演示账号' })); await userEvent.click(screen.getByRole('button', { name: '进入学生空间' }));
  expect(await screen.findByText(/账号或密码不正确/)).toBeInTheDocument(); expect(readSession()).toBeNull();
});
test('expired/corrupt sessions are rejected and logout clears account-scoped metadata', () => {
  saveSession({ token: 'old', expiresAt: 1 }); expect(readSession()).toBeNull(); sessionStorage.setItem('campusbridge.session', 'broken'); expect(readSession()).toBeNull();
  sessionStorage.setItem('campusbridge.student.pending', '{"message":"private"}'); sessionStorage.setItem('other-app', 'keep'); clearSession(); expect(sessionStorage.getItem('campusbridge.student.pending')).toBeNull(); expect(sessionStorage.getItem('other-app')).toBe('keep');
});
test('API 401 broadcasts logout and a failed run can be recognized', async () => {
  const handler = vi.fn(); window.addEventListener('campusbridge:unauthorized', handler, { once: true });
  await expect(checkResponse(new Response('{"detail":"INVALID_TOKEN"}', { status: 401 }))).rejects.toThrow(); expect(handler).toHaveBeenCalled();
  await expect(checkResponse(new Response('{"state":"FAILED"}', { status: 503 }))).rejects.toMatchObject({ code: 'RUN_FAILED' });
});
test('wrong login credentials do not trigger a misleading expired-session notice', async () => {
  const handler = vi.fn(); window.addEventListener('campusbridge:unauthorized', handler);
  await expect(checkResponse(new Response('{"detail":"INVALID_CREDENTIALS"}', { status: 401 }))).rejects.toThrow('账号或密码');
  expect(handler).not.toHaveBeenCalled(); window.removeEventListener('campusbridge:unauthorized', handler);
});
test('aborted requests propagate timeout/cancellation', async () => {
  vi.stubGlobal('fetch', vi.fn((_url, init) => new Promise((_resolve, reject) => init.signal.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError'))))));
  const controller = new AbortController(); const promise = api('/auth/me', { signal: controller.signal }); controller.abort(); await expect(promise).rejects.toMatchObject({ name: 'AbortError' });
});
