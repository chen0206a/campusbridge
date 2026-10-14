import type { CSSProperties } from 'react';
const paths: Record<string, string> = {
  plus: 'M12 5v14M5 12h14', send: 'm4 4 17 8-17 8 3-8-3-8Zm3 8h14', chat: 'M20 15a3 3 0 0 1-3 3H9l-5 3V6a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3Z',
  book: 'M12 5C8 2 4 3 3 4v15c3-2 6-2 9 0m0-14c4-3 8-2 9-1v15c-3-2-6-2-9 0V5Z',
  leaf: 'M5 20c0-8 2-14 14-16 1 10-2 17-9 15M5 20l10-11', shield: 'M12 3 3 7v5c0 5 5 8 9 10 4-2 9-5 9-10V7l-9-4Zm-4 9 3 3 5-6',
  logout: 'M9 4H4v16h5m5-16v16m0-8h7m-3-3 3 3-3 3', arrow: 'M5 12h14m-5-5 5 5-5 5', close: 'm6 6 12 12M6 18 18 6',
  check: 'm5 12 4 4L19 6', chevron: 'm9 5 7 7-7 7', clock: 'M12 8v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
  refresh: 'M20 8a8 8 0 1 0 1 7m-1-12v5h-5', cases: 'M8 5V3h8v2M3 6h18v15H3V6Zm0 6h18m-11 0v3h4v-3',
  user: 'M16 8a4 4 0 1 1-8 0 4 4 0 0 1 8 0ZM4 21v-2a8 8 0 0 1 16 0v2', menu: 'M4 6h16M4 12h16M4 18h16',
  alert: 'M12 3 2 21h20L12 3Zm0 6v5m0 3v1', pin: 'M12 22s8-7 8-13a8 8 0 0 0-16 0c0 6 8 13 8 13Zm3-13a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',
};
export function Icon({ name, size = 20 }: { name: string; size?: number }) { return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name] || paths.chat}/></svg>; }
export function Brand({ compact = false }: { compact?: boolean }) { return <div className="brand"><span className="brand-mark"><svg viewBox="0 0 48 48" aria-hidden="true"><path d="M10 32V19m28 13V19M10 27c0-17 28-17 28 0M7 32h34M18 21v11m12-11v11" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round"/></svg></span>{!compact && <span>CampusBridge<small>让支持，更近一步</small></span>}</div>; }
export function Avatar({ name, style }: { name: string; style?: CSSProperties }) { return <span className="avatar" style={style}>{name.slice(-1)}</span>; }
