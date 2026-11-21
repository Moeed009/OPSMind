import { api } from './api';

export interface KnowledgeDocument {
  id: string;
  tenant_id: string;
  department_id?: string | null;
  scope?: 'company_shared' | 'department_private' | string;
  title: string;
  source: string;
  status: string;
  source_url: string | null;
  size_bytes: number | null;
  chunk_count: number;
  preview: string;
  indexed_at: string | null;
  created_at: string;
}

export function fetchKnowledgeDocuments(departmentId?: string | null): Promise<KnowledgeDocument[]> {
  const qs = departmentId ? `?department_id=${encodeURIComponent(departmentId)}` : '';
  return api<KnowledgeDocument[]>(`/knowledge${qs}`);
}

export function createKnowledgeDocument(payload: {
  title: string;
  content: string;
  source_url?: string;
  department_id?: string | null;
  scope?: 'company_shared' | 'department_private';
}): Promise<KnowledgeDocument> {
  return api<KnowledgeDocument>('/knowledge', {
    method: 'POST',
    body: payload,
  });
}

export function deleteKnowledgeDocument(id: string): Promise<void> {
  return api<void>(`/knowledge/${id}`, {
    method: 'DELETE',
  });
}
