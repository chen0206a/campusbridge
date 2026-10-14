import type { Pending, Session } from './types';
const SESSION = 'campusbridge.session';
const PREFIX = 'campusbridge.';
export function readSession(): Session | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(SESSION) || 'null');
    return value && typeof value.token === 'string' && typeof value.expiresAt === 'number' && value.expiresAt > Date.now() ? value : null;
  } catch { return null; }
}
export function saveSession(value: Session) { sessionStorage.setItem(SESSION, JSON.stringify(value)); }
export function clearSession() { Object.keys(sessionStorage).filter(key => key.startsWith(PREFIX)).forEach(key => sessionStorage.removeItem(key)); }
export function activeConversation(account: string, value?: string) {
  const key = PREFIX + account + '.active';
  if (value !== undefined) sessionStorage.setItem(key, value);
  return sessionStorage.getItem(key);
}
export function readPending(account: string): Pending | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(PREFIX + account + '.pending') || 'null');
    return value && typeof value.conversationId === 'string' && typeof value.key === 'string' && typeof value.message === 'string' ? value : null;
  } catch { return null; }
}
export function savePending(account: string, value: Pending | null) {
  const key = PREFIX + account + '.pending';
  if (value) sessionStorage.setItem(key, JSON.stringify(value)); else sessionStorage.removeItem(key);
}
