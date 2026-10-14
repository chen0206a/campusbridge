import { readSession } from './session';

const messages: Record<string, string> = {
  INVALID_CREDENTIALS: '账号或密码不正确，请重新输入。', INVALID_TOKEN: '登录已过期，请重新登录。',
  AUTHENTICATION_REQUIRED: '请先登录后继续。', PERMISSION_DENIED: '你没有执行此操作的权限。',
  RESOURCE_NOT_FOUND: '该记录不存在，或当前账号无权访问。', STALE_CASE_VERSION: '工单已被更新。已刷新最新状态，请检查后再操作。',
  OPERATION_KEY_REUSED: '该操作标识已用于其他内容，请刷新后确认。', CASE_NOT_OPEN: '工单已被认领，请查看最新状态。',
  INVALID_CASE_TRANSITION: '当前状态不能执行此操作，请先更新工单。', HANDLING_NOTE_REQUIRED: '请填写至少 3 个字的处理记录。',
  INVALID_REQUEST: '提交内容不符合要求，请检查后重试。', REQUEST_UNAVAILABLE: '服务暂时不可用，请稍后重试。',
  REPLY_UNAVAILABLE: '回复暂时无法获取，请重试连接或查询已保存的结果。',
  RUN_FAILED: '这次请求未能完成，可查看已保存的对话后重新发送。',
};
export class ApiError extends Error {
  constructor(public status: number, public code: string) {
    super(messages[code] || (status === 401 ? '登录已过期，请重新登录。' : status === 403 ? '你没有执行此操作的权限。' : status === 409 ? '记录状态已变化，请刷新后重试。' : '服务暂时不可用，请稍后重试。'));
  }
}
export function errorText(error: unknown) {
  if (error instanceof ApiError) return error.message;
  if (error instanceof DOMException && error.name === 'AbortError') return '请求等待超时，请重试。';
  return '网络连接中断，请检查连接后重试。';
}
export function authorizationHeaders(): Record<string, string> { const session = readSession(); return session ? { Authorization: 'Bearer ' + session.token } : {}; }
export async function checkResponse(response: Response) {
  if (response.ok) return;
  let code = 'REQUEST_UNAVAILABLE';
  try { const data = await response.json(); if (data.state === 'FAILED') code = 'RUN_FAILED'; if (typeof data.detail === 'string') code = data.detail; } catch { /* Keep a safe, fixed message. */ }
  if (response.status === 401 && code !== 'INVALID_CREDENTIALS') window.dispatchEvent(new Event('campusbridge:unauthorized'));
  throw new ApiError(response.status, code);
}
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  init.signal?.addEventListener('abort', abort, { once: true });
  if (init.signal?.aborted) controller.abort();
  const timeout = setTimeout(abort, 20_000);
  try {
    const headers = new Headers(authorizationHeaders());
    if (init.body) headers.set('Content-Type', 'application/json');
    new Headers(init.headers).forEach((value, name) => headers.set(name, value));
    const response = await fetch('/api' + path, { ...init, signal: controller.signal,
      headers, cache: 'no-store' });
    await checkResponse(response);
    if (response.status === 204) return undefined as T;
    return await response.json() as T;
  } finally { clearTimeout(timeout); init.signal?.removeEventListener('abort', abort); }
}
