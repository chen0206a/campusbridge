import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { api, errorText } from './api';
import { Icon } from './icons';
import type { Citation, Source } from './types';

export function Notice({ children, retry }: { children: ReactNode; retry?: () => void }) { return <div className="notice" role="alert"><Icon name="alert"/><span>{children}</span>{retry && <button className="text-button" onClick={retry}>重试</button>}</div>; }
export function Loading({ text = '正在加载…' }: { text?: string }) { return <div className="loading" role="status"><span className="spinner"/>{text}</div>; }
export function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCloseRef.current();
      if (event.key === 'Tab') {
        const items = ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],textarea,input,[tabindex="0"]');
        if (items?.length) {
          const first = items[0], last = items[items.length - 1];
          if (event.shiftKey && (document.activeElement === first || document.activeElement === ref.current)) { event.preventDefault(); last.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
      }
    };
    document.addEventListener('keydown', handler);
    return () => { document.removeEventListener('keydown', handler); previous?.focus(); };
  }, []);
  return <div className="modal-overlay" onClick={event => { if (event.target === event.currentTarget) onClose(); }}><div className="modal" role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={ref}><header><h2>{title}</h2><button className="icon-button" aria-label="关闭详情" onClick={onClose}><Icon name="close"/></button></header>{children}</div></div>;
}
export function SourceDialog({ citation, onClose }: { citation: Citation; onClose: () => void }) {
  const [source, setSource] = useState<Source | null>(null); const [error, setError] = useState(''); const [attempt, setAttempt] = useState(0);
  useEffect(() => { const controller = new AbortController(); setError(''); setSource(null);
    api<Source>('/knowledge/sources/' + encodeURIComponent(citation.source_id), { signal: controller.signal }).then(setSource).catch(e => { if (!controller.signal.aborted) setError(errorText(e)); });
    return () => controller.abort();
  }, [citation.source_id, attempt]);
  return <Modal title="资料来源" onClose={onClose}><div className="source-body"><span className="eyebrow">CAMPUS KNOWLEDGE</span><h3>{citation.title}</h3><div className="source-meta"><span>版本 {citation.revision}</span><span>校园支持资料</span></div>{error ? <Notice retry={() => setAttempt(attempt + 1)}>{error}</Notice> : !source ? <Loading/> : <><div className="source-text">{source.text}</div><div className="source-credit"><Icon name="book"/><span>{source.author} · {source.license}{!source.active_revision && ' · 历史版本'}</span></div></>}</div></Modal>;
}
export function MessageText({ text, citations = [], onSource }: { text: string; citations?: Citation[]; onSource: (citation: Citation) => void }) {
  // React escapes user/model text. Only grounded IDs from the committed response become clickable citations.
  const visible = text.replace(/\[source:[^\]]+\]/g, '');
  return <><div className="message-text">{visible}</div>{citations.length > 0 && <div className="citations"><span className="citations-label">参考资料</span>{citations.map((citation, index) => <button key={citation.source_id} className="citation" onClick={() => onSource(citation)}><Icon name="book" size={15}/><span>{index + 1}. {citation.title.replace(/\s*\/\s*[A-Za-z ].*$/, '')}</span><Icon name="chevron" size={13}/></button>)}</div>}</>;
}
