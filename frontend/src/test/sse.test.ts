import { SSEParser, streamReply } from '../sse';
import { ApiError } from '../api';
import { saveSession } from '../session';

afterEach(() => vi.unstubAllGlobals());
function response(text: string) { const bytes = new TextEncoder().encode(text); return new Response(new ReadableStream({ start(controller) { for (let i = 0; i < bytes.length; i += 2) controller.enqueue(bytes.slice(i, i + 2)); controller.close(); } }), { headers: { 'Content-Type': 'text/event-stream; charset=utf-8' } }); }
test('parses split CRLF frames and multiline data, ignores comments', () => {
  const results: unknown[] = []; const parser = new SSEParser(value => results.push(value));
  ['event: meta\r', '\ndata: {"run_id":"r"}\r\n\r', '\n: ping\n\nevent: token\ndata: {\ndata: "text":"你好🌉"}\n\n'].forEach(value => parser.push(value));
  parser.finish(); expect(results).toEqual([{ event: 'meta', data: { run_id: 'r' } }, { event: 'token', data: { text: '你好🌉' } }]);
});
test.each(['event: token\ndata: []\n\n', 'event: token\ndata: {"text":5}\n\n', 'event: meta\ndata: {}\n\n', 'event: token\ndata: not-json\n\n'])('rejects malformed stream %s', text => { expect(() => new SSEParser(() => {}).push(text)).toThrow(); });
test('rejects unfinished frames', () => { const parser = new SSEParser(() => {}); parser.push('event: token\ndata: {"text":"partial"}'); expect(() => parser.finish()).toThrow('INCOMPLETE'); });
test('POST includes authorization and idempotency; UTF8 boundaries are safe', async () => {
  saveSession({ token: 'synthetic-session', expiresAt: Date.now() + 10000 });
  const fetcher = vi.fn(async () => response('event: meta\ndata: {"run_id":"run"}\n\nevent: token\ndata: {"text":"同学🌉"}\n\nevent: done\ndata: {"run_id":"run"}\n\n'));
  vi.stubGlobal('fetch', fetcher); const observed: string[] = [];
  await streamReply({ conversationId: 'conversation', key: 'fixed-key', message: '问题' }, event => { if (event.event === 'token') observed.push(event.data.text as string); }, new AbortController().signal);
  expect(observed).toEqual(['同学🌉']); const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe('/api/conversations/conversation/stream'); expect(init.method).toBe('POST');
  expect(new Headers(init.headers).get('Authorization')).toBe('Bearer synthetic-session');
  expect(new Headers(init.headers).get('Idempotency-Key')).toBe('fixed-key');
  expect(JSON.parse(String(init.body))).toEqual({ message: '问题' });
});
test('retry uses GET replay of the same run', async () => {
  const fetcher = vi.fn(async () => response('event: done\ndata: {"run_id":"original"}\n\n')); vi.stubGlobal('fetch', fetcher);
  await streamReply({ conversationId: 'c', key: 'same-key', message: 'same', runId: 'original' }, () => {}, new AbortController().signal);
  const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit]; expect(url).toBe('/api/runs/original/stream'); expect(init.method).toBe('GET'); expect(init.body).toBeUndefined();
});
test('disconnect without done is an error', async () => { vi.stubGlobal('fetch', vi.fn(async () => response('event: meta\ndata: {"run_id":"r"}\n\n'))); await expect(streamReply({ conversationId: 'c', key: 'k', message: 'm' }, () => {}, new AbortController().signal)).rejects.toThrow('DISCONNECT'); });
test('error event and HTTP permission rejection remain visible failures', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => response('event: error\ndata: {"code":"REPLY_UNAVAILABLE"}\n\n')));
  await expect(streamReply({ conversationId: 'c', key: 'k', message: 'm' }, () => {}, new AbortController().signal)).rejects.toBeInstanceOf(ApiError);
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"PERMISSION_DENIED"}', { status: 403 })));
  await expect(streamReply({ conversationId: 'c', key: 'k', message: 'm' }, () => {}, new AbortController().signal)).rejects.toThrow('权限');
});
