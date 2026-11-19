import { useState } from 'react';
import { AlertTriangle, CheckCircle2, Sparkles } from 'lucide-react';
import { getErrorMessage } from '../lib/api';
import {
  useDeptWorkflowTemplates,
  useUseDeptWorkflowTemplate,
  type DeptWorkflowOut,
} from '../lib/deptWorkflowsApi';
import { Badge, Button, Card, CardContent, CardHeader, EmptyState, ErrorState, Skeleton, useToast } from './ui';

export interface DeptTemplateGalleryProps {
  departmentId: string;
  canUse: boolean;
  onUsed: (workflow: DeptWorkflowOut) => void;
}

export function DeptTemplateGallery({ departmentId, canUse, onUsed }: DeptTemplateGalleryProps) {
  const { toast } = useToast();
  const [includeAll, setIncludeAll] = useState(false);
  const templatesQuery = useDeptWorkflowTemplates(departmentId, includeAll);
  const useTemplate = useUseDeptWorkflowTemplate(departmentId);

  if (templatesQuery.isLoading) {
    return (
      <div className="grid gap-4 md:grid-cols-2">
        <Skeleton className="h-36 w-full" />
        <Skeleton className="h-36 w-full" />
      </div>
    );
  }

  if (templatesQuery.isError) {
    return (
      <ErrorState
        title="Could not load workflow templates"
        message={getErrorMessage(templatesQuery.error)}
        onRetry={() => void templatesQuery.refetch()}
      />
    );
  }

  const templates = templatesQuery.data ?? [];
  if (templates.length === 0) {
    return (
      <EmptyState
        icon={Sparkles}
        title="No templates for this department type"
        description="Create a custom workflow from scratch using the New Workflow button."
      />
    );
  }

  async function handleUse(key: string) {
    try {
      const wf = await useTemplate.mutateAsync(key);
      toast({
        variant: 'success',
        title: 'Draft workflow created',
        description: `Created "${wf.name}" from template.`,
      });
      onUsed(wf);
    } catch (err) {
      toast({
        variant: 'danger',
        title: 'Could not use template',
        description: getErrorMessage(err),
      });
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-[var(--text-muted)]">
          Showing {templates.length} template(s){includeAll ? ' across all 6 departments' : ' for this department'}.
        </p>
        <Button
          type="button"
          size="sm"
          variant="secondary"
          onClick={() => setIncludeAll((v) => !v)}
        >
          {includeAll ? 'This department only' : 'Show all departments (12)'}
        </Button>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {templates.map((tpl) => (
          <Card key={tpl.key} className="flex flex-col justify-between gap-3">
            <div className="space-y-2">
              <CardHeader className="pb-1">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h3 className="text-sm font-semibold text-[var(--text)]">{tpl.name}</h3>
                    <Badge variant="neutral">{tpl.department_type}</Badge>
                  </div>
                  <Badge variant={tpl.ready ? 'success' : 'warning'}>
                    {tpl.ready ? 'Ready' : 'Connector needed'}
                  </Badge>
                </div>
              </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-xs leading-relaxed text-[var(--text-muted)]">
                {tpl.description}
              </p>
              <div className="flex flex-wrap gap-1.5">
                {tpl.connector_status.map((cs) => (
                  <Badge key={cs.provider} variant={cs.connected ? 'success' : 'warning'}>
                    {cs.connected ? (
                      <CheckCircle2 className="h-3 w-3" />
                    ) : (
                      <AlertTriangle className="h-3 w-3" />
                    )}
                    {cs.provider}: {cs.connected ? 'connected' : 'not ready'}
                  </Badge>
                ))}
              </div>
            </CardContent>
          </div>
          {canUse && (
            <div className="flex justify-end border-t border-[var(--border)] pt-3">
              <Button
                type="button"
                size="sm"
                loading={useTemplate.isPending}
                onClick={() => void handleUse(tpl.key)}
              >
                Use template
              </Button>
            </div>
          )}
        </Card>
      ))}
      </div>
    </div>
  );
}
