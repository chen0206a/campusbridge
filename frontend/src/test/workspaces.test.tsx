import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StudentWorkspace } from '../StudentWorkspace';
import { CounselorWorkspace } from '../CounselorWorkspace';
import { savePending, saveSession } from '../session';
import type { CaseDetail, User } from '../types';

const user: User = { account_id: 'student', username: 'student_lan', display_name: '学生澜', role: 'student', campus_id: 'a' };
const counselor: User = { ...user, account_id: 'counselor', role: 'counselor', display_name: '辅导员' };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
afterEach(() => vi.unstubAllGlobals());

test('student displays partial SSE then committed citations and restores history', async () => {
  let stream: ReadableStreamDefaultController<Uint8Array>; let completed = false;
  const citation = { source_id: 'source', document_id: 'library', title: '图书馆资料', revision: 'v1', quote: '文档', source_hash: 'h' };
  const messages = [{ message_id: 'u', role: 'user', run_id: 'run', text: '图书馆预约' }, { message_id: 'a', role: 'assistant', run_id: 'run', text: '你好同学[source:source]' }];
  const fetcher = vi.fn(async (url: string) => {
    if (url.endsWith('/conversations')) return json({ conversations: [{ conversation_id: 'conv', title: '新的对话', updated_at: '2026-10-12T08:00:00Z', message_count: completed ? 2 : 0 }] });
    if (url.endsWith('/conversations/conv')) return json({ messages: completed ? messages : [] });
    if (url.endsWith('/runs/run')) return json({ state: 'ACCEPTED', citations: [citation] });
    if (url.endsWith('/stream')) return new Response(new ReadableStream({ start(controller) { stream = controller; controller.enqueue(new TextEncoder().encode('event: meta\ndata: {"run_id":"run"}\n\nevent: token\ndata: {"text":"你好"}\n\n')); } }), { headers: { 'Content-Type': 'text/event-stream' } });
    throw new Error('unexpected');
  });
  vi.stubGlobal('fetch', fetcher); const rendered = render(<StudentWorkspace user={user} onLogout={() => {}}/>);
  const input = screen.getByRole('textbox', { name: '输入消息' }); await waitFor(() => expect(input).toBeEnabled());
  await userEvent.type(input, '图书馆预约'); await userEvent.click(screen.getByRole('button', { name: '发送消息' }));
  expect(await screen.findByText('你好')).toBeInTheDocument(); expect(screen.getByRole('button', { name: '发送消息' })).toBeDisabled();
  await act(async () => { completed = true; stream!.enqueue(new TextEncoder().encode('event: token\ndata: {"text":"同学"}\n\nevent: done\ndata: {"run_id":"run"}\n\n')); stream!.close(); });
  expect(await within(screen.getByRole('region', { name: '对话内容' })).findByRole('button', { name: /图书馆资料/ })).toBeInTheDocument();
  expect(screen.getByText('你好同学')).toBeInTheDocument();
  rendered.unmount(); render(<StudentWorkspace user={user} onLogout={() => {}}/>);
  expect(await screen.findByText('你好同学')).toBeInTheDocument(); expect(screen.queryByText(/风险等级|MODEL_CONCERN/)).not.toBeInTheDocument();
});

test('refresh of an interrupted request replays the original run without new POST', async () => {
  savePending(user.account_id, { conversationId: 'conv', key: 'original-key', message: '已提交的问题', runId: 'original-run' });
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/conversations')) return json({ conversations: [{ conversation_id: 'conv', title: '已提交', updated_at: '2026-10-12', message_count: 1 }] });
    if (url.endsWith('/conversations/conv')) return json({ messages: [] });
    if (url.endsWith('/runs/original-run')) return json({ state: 'ACCEPTED', citations: [] });
    if (url.endsWith('/runs/original-run/stream')) return new Response('event: meta\ndata: {"run_id":"original-run"}\n\nevent: done\ndata: {"run_id":"original-run"}\n\n', { headers: { 'Content-Type': 'text/event-stream' } });
    throw new Error('unexpected ' + url + init?.method);
  });
  vi.stubGlobal('fetch', fetcher); render(<StudentWorkspace user={user} onLogout={() => {}}/>);
  await userEvent.click(await screen.findByRole('button', { name: '继续接收' }));
  await waitFor(() => expect(screen.getByRole('textbox', { name: '输入消息' })).toBeEnabled());
  expect(fetcher.mock.calls.some(([url, init]) => url.endsWith('/runs/original-run/stream') && init?.method === 'GET')).toBe(true);
  expect(fetcher.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
});

test('a terminal failed run restores saved history and permits a new explicit message', async () => {
  savePending(user.account_id, { conversationId: 'conv', key: 'failed-key', message: '原来的问题', runId: 'failed-run' });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/conversations')) return json({ conversations: [{ conversation_id: 'conv', title: '原来的问题', updated_at: '2026-10-12', message_count: 1 }] });
    if (url.endsWith('/conversations/conv')) return json({ messages: [{ message_id: 'u', role: 'user', run_id: 'failed-run', text: '原来的问题' }] });
    if (url.endsWith('/runs/failed-run')) return json({ state: 'FAILED' }, 503);
    throw new Error('unexpected request');
  }));
  render(<StudentWorkspace user={user} onLogout={() => {}}/>);
  await userEvent.click(await screen.findByRole('button', { name: '查看已保存的对话' }));
  expect(await screen.findByText(/可以发送新消息/)).toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: '输入消息' })).toBeEnabled();
  expect(sessionStorage.getItem('campusbridge.student.pending')).toBeNull();
});

test('first-conversation submission cannot create duplicate conversations while waiting', async () => {
  let release: (value: Response) => void; let created = false;
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/conversations') && init?.method === 'POST') return new Promise<Response>(resolve => { release = resolve; });
    if (url.endsWith('/conversations')) return json({ conversations: created ? [{ conversation_id: 'conv', title: '新对话', updated_at: '2026-10-12', message_count: 0 }] : [] });
    if (url.endsWith('/conversations/conv')) return json({ messages: [] });
    if (url.endsWith('/stream')) return new Response('event: meta\ndata: {"run_id":"run"}\n\nevent: done\ndata: {"run_id":"run"}\n\n', { headers: { 'Content-Type': 'text/event-stream' } });
    if (url.endsWith('/runs/run')) return json({ state: 'ACCEPTED', citations: [] });
    throw new Error('unexpected');
  });
  vi.stubGlobal('fetch', fetcher); render(<StudentWorkspace user={user} onLogout={() => {}}/>);
  const input = screen.getByRole('textbox', { name: '输入消息' }); await waitFor(() => expect(input).toBeEnabled());
  await userEvent.type(input, '新问题'); await userEvent.click(screen.getByRole('button', { name: '发送消息' }));
  expect(screen.getByRole('button', { name: '发送消息' })).toBeDisabled();
  await act(async () => { created = true; release!(json({ conversation_id: 'conv' })); });
  await waitFor(() => expect(input).toBeEnabled());
  expect(fetcher.mock.calls.filter(([url, init]) => url.endsWith('/conversations') && init?.method === 'POST')).toHaveLength(1);
});

function counselorFixture(options: { conflict?: boolean; denied?: boolean; uncertain?: boolean } = {}) {
  let detail: CaseDetail = { case_id: 'case-one', risk_event_id: 'risk', student_id: 'student', state: 'OPEN', owner_id: null, version: 0, run_id: 'run', events: [] };
  const operations: { url: string; key: string | null; body: { expected_version: number; note: string } }[] = [];
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/cases')) return json({ cases: [detail] });
    if (url.endsWith('/cases/case-one')) return json(detail);
    if (url.endsWith('/student-context')) return json({ student_display_name: '学生澜', request_text: '我想找辅导员聊聊校园生活', created_at: '2026-10-12T08:00:00Z' });
    if (init?.method === 'POST') {
      const body = JSON.parse(String(init.body)); const key = new Headers(init.headers).get('Idempotency-Key'); operations.push({ url, key, body });
      if (options.denied) return json({ detail: 'PERMISSION_DENIED' }, 403);
      if (options.conflict && url.endsWith('/process') && operations.filter(item => item.url.endsWith('/process')).length === 1) { detail = { ...detail, state: 'IN_PROGRESS', version: detail.version + 1 }; return json({ detail: 'STALE_CASE_VERSION' }, 409); }
      if (options.uncertain && operations.length === 1) return json({ detail: 'REQUEST_UNAVAILABLE' }, 503);
      detail = { ...detail, owner_id: counselor.account_id, state: url.endsWith('/claim') ? 'CLAIMED' : url.endsWith('/close') ? 'CLOSED' : 'IN_PROGRESS', version: detail.version + 1 };
      return json({ idempotent_replay: !!options.uncertain });
    }
    throw new Error('unexpected ' + url);
  });
  vi.stubGlobal('fetch', fetcher); render(<CounselorWorkspace user={counselor} onLogout={() => {}}/>);
  return operations;
}
async function openCase() { await userEvent.click(await screen.findByRole('button', { name: /CB-CASE-O/ })); }
test('counselor claim/process/close uses server version and distinct operation keys', async () => {
  const operations = counselorFixture(); await openCase(); await userEvent.click(await screen.findByRole('button', { name: '认领工单' }));
  expect(await screen.findByLabelText('沟通情况与下一步安排')).toBeInTheDocument(); expect(screen.getByRole('button', { name: '关闭工单' })).toBeDisabled();
  await userEvent.type(screen.getByLabelText('沟通情况与下一步安排'), '已与学生沟通，安排下一次跟进。'); await userEvent.click(screen.getByRole('button', { name: '保存并进入处理中' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '关闭工单' })).toBeEnabled()); await userEvent.click(screen.getByRole('button', { name: '关闭工单' }));
  await userEvent.type(screen.getByLabelText('关闭依据与后续支持安排'), '本次跟进完成，已告知后续支持渠道。'); await userEvent.click(screen.getByRole('button', { name: '确认关闭' }));
  expect(await screen.findByText('该工单已关闭，所有处理记录已保留。')).toBeInTheDocument();
  expect(operations.map(item => item.body.expected_version)).toEqual([0, 1, 2]); expect(new Set(operations.map(item => item.key)).size).toBe(3);
});
test('version conflict refreshes detail and preserves the handling note', async () => {
  const operations = counselorFixture({ conflict: true }); await openCase(); await userEvent.click(await screen.findByRole('button', { name: '认领工单' }));
  await userEvent.type(await screen.findByLabelText('沟通情况与下一步安排'), '保留这份处理意见'); await userEvent.click(screen.getByRole('button', { name: '保存并进入处理中' }));
  expect(await screen.findByText(/工单已被更新/)).toBeInTheDocument(); expect(screen.getByLabelText('沟通情况与下一步安排')).toHaveValue('保留这份处理意见');
  await userEvent.click(await screen.findByRole('button', { name: '保存处理记录' })); await waitFor(() => expect(operations.at(-1)?.body.expected_version).toBe(2));
});
test('permission refusal displays an error instead of success', async () => { counselorFixture({ denied: true }); await openCase(); await userEvent.click(await screen.findByRole('button', { name: '认领工单' })); expect(await screen.findByText(/没有执行此操作的权限/)).toBeInTheDocument(); expect(screen.queryByText('工单已认领。')).not.toBeInTheDocument(); });
test('uncertain operation retries with the same idempotency key', async () => {
  const operations = counselorFixture({ uncertain: true }); await openCase(); await userEvent.click(await screen.findByRole('button', { name: '认领工单' }));
  await screen.findByText(/服务暂时不可用/); await userEvent.click(screen.getByRole('button', { name: '认领工单' }));
  expect(await screen.findByText(/已恢复原来的处理结果/)).toBeInTheDocument(); expect(operations[0].key).toBe(operations[1].key);
});


test('topic filters expose the chosen starters without submitting a request', async () => {
  const fetcher = vi.fn(async () => json({ conversations: [] }));
  vi.stubGlobal('fetch', fetcher); render(<StudentWorkspace user={user} onLogout={() => {}}/>);
  await screen.findByRole('heading', { name: '新对话' });
  await userEvent.click(screen.getByRole('button', { name: '人工支持' }));
  expect(screen.getByRole('button', { name: /联系辅导员/ })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /^校园事务\s*图书馆/ })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '人工支持' })).toHaveAttribute('aria-pressed', 'true');
  await userEvent.click(screen.getByRole('button', { name: '全部' }));
  expect(screen.getByRole('button', { name: /^校园事务\s*图书馆/ })).toBeInTheDocument();
  expect(fetcher.mock.calls).toHaveLength(1);
});

test('reference rail deduplicates committed sources and opens authorized source details', async () => {
  const source = { source_id: 'grounded-id', title: '校园借阅指南', revision: 'v2', source_hash: 'hash', quote: '预约' };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/conversations')) return json({ conversations: [{ conversation_id: 'conv', title: '借阅', updated_at: '2026-10-16', message_count: 2 }] });
    if (url.endsWith('/conversations/conv')) return json({ messages: [{ message_id: 'a1', role: 'assistant', run_id: 'r1', text: '预约[source:grounded-id]' }, { message_id: 'a2', role: 'assistant', run_id: 'r2', text: '请查看资料[source:grounded-id]' }] });
    if (url.includes('/runs/')) return json({ citations: [source], state: 'ACCEPTED' });
    if (url.endsWith('/knowledge/sources/grounded-id')) return json({ text: '已授权的校园借阅原文', author: 'CampusBridge', license: 'MIT', active_revision: true });
    throw new Error('Unexpected source request');
  }));
  render(<StudentWorkspace user={user} onLogout={() => {}}/>);
  const rail = screen.getByRole('complementary', { name: '对话支持信息' });
  const button = await within(rail).findByRole('button', { name: /校园借阅指南/ });
  expect(within(rail).getAllByRole('button', { name: /校园借阅指南/ })).toHaveLength(1);
  await userEvent.click(button);
  expect(await screen.findByText('已授权的校园借阅原文')).toBeInTheDocument();
});

test('case search and ownership filters combine with server-derived queue counts', async () => {
  const items = [
    { case_id: 'alpha-1', student_id: 'student-one', state: 'CLAIMED', owner_id: counselor.account_id },
    { case_id: 'bravo-2', student_id: 'student-two', state: 'OPEN', owner_id: null },
    { case_id: 'charlie-3', student_id: 'student-three', state: 'CLOSED', owner_id: counselor.account_id },
  ];
  vi.stubGlobal('fetch', vi.fn(async () => json({ cases: items })));
  render(<CounselorWorkspace user={counselor} onLogout={() => {}}/>);
  await screen.findByRole('button', { name: /CB-ALPHA/ });
  await userEvent.click(screen.getByRole('button', { name: '由我跟进' }));
  expect(screen.queryByRole('button', { name: /CB-BRAVO/ })).not.toBeInTheDocument();
  expect(screen.getByText('当前显示 2 / 3 条')).toBeInTheDocument();
  await userEvent.type(screen.getByRole('textbox', { name: '搜索工单' }), 'student-three');
  expect(screen.getByRole('button', { name: /CB-CHARLI/ })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /CB-ALPHA/ })).not.toBeInTheDocument();
  expect(screen.getByText('当前显示 1 / 3 条')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '清空搜索' }));
  expect(screen.getByText('当前显示 2 / 3 条')).toBeInTheDocument();
});

test('empty queue search can reset all filters without a business mutation', async () => {
  const fetcher = vi.fn(async () => json({ cases: [] }));
  vi.stubGlobal('fetch', fetcher); render(<CounselorWorkspace user={counselor} onLogout={() => {}}/>);
  await screen.findByText('暂时没有待跟进的事项');
  await userEvent.type(screen.getByRole('textbox', { name: '搜索工单' }), 'not-found');
  expect(screen.getByText('没有匹配的工单')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '重置筛选' }));
  expect(screen.getByRole('textbox', { name: '搜索工单' })).toHaveValue('');
  expect(screen.getByText('暂时没有待跟进的事项')).toBeInTheDocument();
  expect(fetcher.mock.calls).toHaveLength(1);
});
