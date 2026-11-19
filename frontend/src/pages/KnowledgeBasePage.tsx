import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BookOpen, CheckCircle2, FileText, Plus, Trash2 } from 'lucide-react';
import {
  createKnowledgeDocument,
  deleteKnowledgeDocument,
  fetchKnowledgeDocuments,
  type KnowledgeDocument,
} from '../lib/knowledgeApi';
import { getErrorMessage } from '../lib/api';
import { useDepartments } from '../lib/departmentsApi';
import { useActiveTenant } from '../lib/hooks';
import {
  Alert,
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  ConfirmDialog,
  Dialog,
  EmptyState,
  ErrorState,
  Input,
  PageHeader,
  Select,
  Skeleton,
  useToast,
} from '../components/ui';

export default function KnowledgeBasePage() {
  const { active } = useActiveTenant();
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const canEdit =
    active?.role === 'owner' || active?.role === 'admin' || active?.role === 'builder';

  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [scope, setScope] = useState<'company_shared' | 'department_private'>('company_shared');
  const [departmentId, setDepartmentId] = useState('');
  const [formOpen, setFormOpen] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<KnowledgeDocument | null>(null);

  const { data: departments = [] } = useDepartments();

  const {
    data: docs = [],
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: ['knowledge', active?.id],
    queryFn: () => fetchKnowledgeDocuments(),
    enabled: Boolean(active?.id),
  });

  const createMut = useMutation({
    mutationFn: createKnowledgeDocument,
    onSuccess: () => {
      setTitle('');
      setContent('');
      setSourceUrl('');
      setFormOpen(false);
      setFormError(null);
      void queryClient.invalidateQueries({ queryKey: ['knowledge', active?.id] });
      toast({
        variant: 'success',
        title: 'Document indexed',
        description: 'Knowledge chunks are ready for workflow Retrieve nodes.',
      });
    },
    onError: (err: unknown) => {
      setFormError(getErrorMessage(err));
    },
  });

  const deleteMut = useMutation({
    mutationFn: deleteKnowledgeDocument,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['knowledge', active?.id] });
      toast({
        variant: 'success',
        title: 'Document deleted',
      });
      setConfirmDelete(null);
    },
    onError: (err: unknown) => {
      toast({
        variant: 'danger',
        title: 'Could not delete document',
        description: getErrorMessage(err),
      });
    },
  });

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!title.trim() || content.trim().length < 10) {
      setFormError('Please provide a title and at least 10 characters of content.');
      return;
    }
    createMut.mutate({
      title: title.trim(),
      content: content.trim(),
      source_url: sourceUrl.trim() || undefined,
      scope,
      department_id: scope === 'department_private' && departmentId ? departmentId : null,
    });
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Knowledge Base"
        description="Upload operational playbooks and SOPs used by workflow Retrieve and AI Agent steps."
        actions={
          <div className="flex items-center gap-2">
            <Badge variant="success">
              <BookOpen className="h-3 w-3" />
              RAG Context Store
            </Badge>
            {canEdit && (
              <Button
                size="sm"
                onClick={() => {
                  setFormError(null);
                  setFormOpen(true);
                }}
              >
                <Plus className="h-4 w-4" />
                Add Document
              </Button>
            )}
          </div>
        }
      />

      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : isError ? (
        <ErrorState
          title="Could not load knowledge base"
          message={getErrorMessage(error)}
          onRetry={() => void refetch()}
        />
      ) : docs.length === 0 ? (
        <EmptyState
          icon={FileText}
          title="No Knowledge Documents Indexed"
          description="Add your company SOPs and policies so workflow Retrieve nodes can ground AI responses in verified facts."
          action={
            canEdit ? (
              <Button size="sm" onClick={() => setFormOpen(true)}>
                <Plus className="h-4 w-4" />
                Add Document
              </Button>
            ) : undefined
          }
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {docs.map((doc) => (
            <Card key={doc.id} className="flex flex-col justify-between gap-4">
              <div className="space-y-2">
                <CardHeader className="pb-1">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-2">
                      <FileText className="h-4 w-4 shrink-0 text-[var(--primary)]" />
                      <h3 className="truncate text-sm font-semibold text-[var(--text)]">
                        {doc.title}
                      </h3>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <Badge variant={doc.scope === 'department_private' ? 'warning' : 'neutral'}>
                        {doc.scope === 'department_private' ? 'Dept Private' : 'Company Shared'}
                      </Badge>
                      <Badge variant="success">
                        <CheckCircle2 className="h-3 w-3" />
                        {doc.chunk_count} {doc.chunk_count === 1 ? 'chunk' : 'chunks'}
                      </Badge>
                    </div>
                  </div>
                </CardHeader>
                <CardContent>
                  <p className="line-clamp-3 text-xs leading-relaxed text-[var(--text-muted)]">
                    {doc.preview || 'No preview available'}
                  </p>
                </CardContent>
              </div>

              <div className="flex items-center justify-between border-t border-[var(--border)] pt-3 text-xs text-[var(--text-subtle)]">
                <span>Indexed {new Date(doc.created_at).toLocaleDateString()}</span>
                {canEdit && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => setConfirmDelete(doc)}
                  >
                    <Trash2 className="h-3.5 w-3.5 text-[var(--danger)]" />
                    Delete
                  </Button>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}

      <Dialog
        open={formOpen}
        onOpenChange={setFormOpen}
        title="Index New SOP / Knowledge Document"
        description="Content is chunked and embedded for RAG retrieval inside workflows."
      >
        <form onSubmit={handleSubmit} className="space-y-4">
          {formError && <Alert kind="error">{formError}</Alert>}
          <Input
            label="Document Title"
            required
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g., Billing Refund & Escalation Policy"
          />
          <Input
            label="Reference URL (Optional)"
            type="url"
            value={sourceUrl}
            onChange={(e) => setSourceUrl(e.target.value)}
            placeholder="https://internal.company.com/sop/billing"
          />
          <Select
            label="Visibility Scope"
            value={scope}
            onChange={(e) => setScope(e.target.value as 'company_shared' | 'department_private')}
          >
            <option value="company_shared">Company Shared (all departments)</option>
            <option value="department_private">Department Private</option>
          </Select>
          {scope === 'department_private' && (
            <Select
              label="Department"
              required
              value={departmentId}
              onChange={(e) => setDepartmentId(e.target.value)}
            >
              <option value="">Select department...</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </Select>
          )}
          <div className="space-y-1.5">
            <label
              htmlFor="kb-sop-content"
              className="block text-xs font-medium text-[var(--text-muted)]"
            >
              SOP Content / Guidelines
            </label>
            <textarea
              id="kb-sop-content"
              rows={5}
              required
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="Paste standard operating procedures, SLA rules, or refund policies here..."
              className="block w-full rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text)] placeholder:text-[var(--text-subtle)] shadow-[var(--shadow-xs)] focus:border-[var(--primary)] focus:ring-2 focus:ring-[var(--primary)]/20 focus:outline-none"
            />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" size="sm" onClick={() => setFormOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" size="sm" loading={createMut.isPending}>
              Save &amp; Index Chunks
            </Button>
          </div>
        </form>
      </Dialog>

      <ConfirmDialog
        open={Boolean(confirmDelete)}
        onOpenChange={(open) => {
          if (!open) setConfirmDelete(null);
        }}
        title="Delete knowledge document"
        description={
          confirmDelete
            ? `Delete "${confirmDelete.title}"? Workflows using Retrieve will no longer see these chunks.`
            : ''
        }
        confirmLabel="Delete"
        variant="danger"
        loading={deleteMut.isPending}
        onConfirm={() => {
          if (confirmDelete) deleteMut.mutate(confirmDelete.id);
        }}
      />
    </div>
  );
}
