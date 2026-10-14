import { ApiError, authorizationHeaders, checkResponse } from './api';
import type { Pending } from './types';
export interface StreamEvent { event: 'meta' | 'status' | 'token' | 'done' | 'error'; data: Record<string, unknown>; }
export class SSEParser {
  private buffer = '';
  constructor(private receive: (event: StreamEvent) => void) {}
  push(text: string) {
    this.buffer = (this.buffer + text).replace(/\r\n/g, '\n');
    if (this.buffer.length > 1_000_000) throw new Error('SSE_BUFFER_LIMIT');
    let boundary: number;
    while ((boundary = this.buffer.indexOf('\n\n')) !== -1) {
      const block = this.buffer.slice(0, boundary); this.buffer = this.buffer.slice(boundary + 2);
      let name = 'message'; const data: string[] = [];
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) name = line.slice(6).trim();
        if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
      }
      if (!data.length || !['meta', 'status', 'token', 'done', 'error'].includes(name)) continue;
      const value = JSON.parse(data.join('\n'));
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('SSE_INVALID_DATA');
      if (name === 'token' && typeof value.text !== 'string') throw new Error('SSE_INVALID_TOKEN');
      if ((name === 'meta' || name === 'done') && typeof value.run_id !== 'string') throw new Error('SSE_INVALID_RUN');
      this.receive({ event: name as StreamEvent['event'], data: value });
    }
  }
  finish() { if (this.buffer.trim()) throw new Error('SSE_INCOMPLETE_FRAME'); }
}
export async function streamReply(pending: Pending, receive: (event: StreamEvent) => void, signal: AbortSignal) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener('abort', abort, { once: true });
  if (signal.aborted) controller.abort();
  const timeout = setTimeout(abort, 100_000);
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  try {
    const path = pending.runId ? '/api/runs/' + encodeURIComponent(pending.runId) + '/stream'
      : '/api/conversations/' + encodeURIComponent(pending.conversationId) + '/stream';
    const response = await fetch(path, { method: pending.runId ? 'GET' : 'POST', headers: {
      ...authorizationHeaders(), Accept: 'text/event-stream', ...(pending.runId ? {} : { 'Content-Type': 'application/json', 'Idempotency-Key': pending.key }) },
      body: pending.runId ? undefined : JSON.stringify({ message: pending.message }), signal: controller.signal, cache: 'no-store' });
    await checkResponse(response);
    if (!response.body || !response.headers.get('Content-Type')?.includes('text/event-stream')) throw new Error('SSE_UNAVAILABLE');
    let completed = false;
    const parser = new SSEParser(event => {
      if (event.event === 'error') throw new ApiError(503, 'REPLY_UNAVAILABLE');
      if (event.event === 'done') completed = true;
      receive(event);
    });
    reader = response.body.getReader();
    const decoder = new TextDecoder();
    while (true) {
      const next = await reader.read();
      if (next.done) break;
      parser.push(decoder.decode(next.value, { stream: true }));
    }
    parser.push(decoder.decode()); parser.finish();
    if (!completed) throw new Error('SSE_EARLY_DISCONNECT');
  } finally {
    clearTimeout(timeout); signal.removeEventListener('abort', abort);
    if (reader) { await reader.cancel().catch(() => undefined); reader.releaseLock(); }
  }
}
