export type Role = 'student' | 'counselor';
export interface Session { token: string; expiresAt: number; }
export interface User { account_id: string; username: string; display_name: string; role: Role; campus_id: string; }
export interface Conversation { conversation_id: string; title: string; created_at: string; updated_at: string; message_count: number; }
export interface Citation { source_id: string; document_id: string | null; title: string; revision: string; quote: string; source_hash: string; }
export interface Source { source_id: string; title: string; text: string; revision: string; license: string; author: string; active_revision: boolean; }
export interface Message { message_id: string; run_id: string; role: 'user' | 'assistant'; text: string; citations?: Citation[]; }
export interface Reply { run_id: string; conversation_id: string; message_id: string; reply: string; citations: Citation[]; state: 'ACCEPTED' | 'SAFE_FALLBACK'; }
export interface Pending { conversationId: string; key: string; message: string; runId?: string; }
export type CaseState = 'OPEN' | 'CLAIMED' | 'IN_PROGRESS' | 'CLOSED';
export interface CaseItem { case_id: string; risk_event_id: string; student_id: string; state: CaseState; owner_id: string | null; version: number; }
export interface CaseEvent { actor_id: string; action: string; from_state: string; to_state: CaseState; note: string; version: number; created_at: string; }
export interface CaseDetail extends CaseItem { run_id: string; events: CaseEvent[]; }
export interface StudentContext { student_display_name: string; student_id: string; request_text: string; created_at: string; }
