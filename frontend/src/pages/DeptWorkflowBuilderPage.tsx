import { useCallback, useMemo, useRef, useState, type DragEvent } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  BackgroundVariant,
  ConnectionLineType,
  Controls,
  MiniMap,
  ReactFlow,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
  type ReactFlowInstance,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  AlertTriangle,
  ArrowLeft,
  Bot,
  CheckCircle2,
  Clock,
  Database,
  FileSearch,
  FlaskConical,
  GitBranch,
  History,
  Layers,
  Pause,
  Play,
  RotateCcw,
  Save,
  Send,
  ShieldAlert,
  SlidersHorizontal,
  Sparkles,
  Tag,
  Trash2,
  X,
  Zap,
} from 'lucide-react';
import { CustomWorkflowNode } from '../components/CustomWorkflowNode';
import { ScheduleEditor } from '../components/ScheduleEditor';
import {
  Alert,
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  Dialog,
  ErrorState,
  Input,
  Select,
  Skeleton,
  useToast,
} from '../components/ui';
import { getErrorMessage } from '../lib/api';
import { useConnections } from '../lib/connectionsApi';
import { useDepartmentDashboard } from '../lib/departmentsApi';
import {
  useDeptRunDetail,
  useDeptWorkflow,
  useDeptWorkflowLifecycle,
  useDeptWorkflowRuns,
  useEvaluateDeptWorkflow,
  useReplayDeptRun,
  useTestDeptAi,
  useUpdateDeptWorkflow,
  type AiTestResponse,
} from '../lib/deptWorkflowsApi';
import { useActiveTenant } from '../lib/hooks';
import {
  validateNodeConfig,
  type DeptTriggerConfig,
  type DeptWorkflowDefinition,
  type DeptWorkflowEdge,
  type DeptWorkflowNode,
} from '../lib/scheduleUtils';
import { useTheme } from '../lib/themeContext';

const NODE_TYPES = { custom: CustomWorkflowNode };

const STATUS_BADGE: Record<string, 'neutral' | 'warning' | 'success'> = {
  draft: 'neutral',
  in_review: 'warning',
  active: 'success',
  paused: 'warning',
};

const PALETTE_ITEMS: Array<{
  type: DeptWorkflowNode['type'];
  label: string;
  description: string;
  icon: typeof Sparkles;
  tone: string;
}> = [
  {
    type: 'classify',
    label: 'Classify',
    description: 'Categorize input into labels with confidence',
    icon: Tag,
    tone: 'bg-violet-500/15 text-violet-400 border-violet-500/30',
  },
  {
    type: 'retrieve',
    label: 'Retrieve',
    description: 'Search Knowledge Hub or aggregate ops data',
    icon: Database,
    tone: 'bg-cyan-500/15 text-cyan-400 border-cyan-500/30',
  },
  {
    type: 'extract',
    label: 'Extract',
    description: 'Extract structured JSON fields from text',
    icon: FileSearch,
    tone: 'bg-indigo-500/15 text-indigo-400 border-indigo-500/30',
  },
  {
    type: 'agent',
    label: 'Agent',
    description: 'AI reasoning, drafting, or grounded Q&A',
    icon: Bot,
    tone: 'bg-purple-500/15 text-purple-400 border-purple-500/30',
  },
  {
    type: 'condition',
    label: 'Condition',
    description: 'Branch flow on true / false expression',
    icon: GitBranch,
    tone: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
  },
  {
    type: 'approval',
    label: 'Approval',
    description: 'Human-in-the-loop review gate',
    icon: ShieldAlert,
    tone: 'bg-rose-500/15 text-rose-400 border-rose-500/30',
  },
  {
    type: 'action',
    label: 'Action',
    description: 'Gmail, Slack, HubSpot, or Zoho Invoice',
    icon: Send,
    tone: 'bg-sky-500/15 text-sky-400 border-sky-500/30',
  },
  {
    type: 'wait',
    label: 'Wait',
    description: 'Delay or pause until condition is met',
    icon: Clock,
    tone: 'bg-slate-500/15 text-slate-400 border-slate-500/30',
  },
];

function toFlowNodes(nodes: DeptWorkflowNode[]): Node[] {
  return nodes.map((n, idx) => ({
    id: n.id,
    type: 'custom',
    position: { x: 100 + (idx % 3) * 300, y: 100 + Math.floor(idx / 3) * 160 },
    data: { ...n, label: n.label || n.id },
  }));
}

function toFlowEdges(edges: DeptWorkflowEdge[]): Edge[] {
  return edges.map((e, idx) => ({
    id: `e_${idx}_${e.source}_${e.target}`,
    source: e.source,
    target: e.target,
    sourceHandle: e.label ?? undefined,
    label: e.label ?? undefined,
    type: 'smoothstep',
    animated: true,
    style: {
      stroke:
        e.label === 'true'
          ? '#10b981'
          : e.label === 'false'
            ? '#ef4444'
            : 'var(--primary)',
      strokeWidth: 2,
    },
  }));
}

export default function DeptWorkflowBuilderPage() {
  const { id: deptId = '', wfId = '' } = useParams<{ id: string; wfId: string }>();
  const navigate = useNavigate();
  const { theme } = useTheme();
  const { toast } = useToast();
  const { active } = useActiveTenant();

  const dashQuery = useDepartmentDashboard(deptId);
  const wfQuery = useDeptWorkflow(deptId, wfId);
  const connsQuery = useConnections(deptId);
  const runsQuery = useDeptWorkflowRuns(deptId, wfId);
  const updateWf = useUpdateDeptWorkflow(deptId, wfId);
  const lifecycle = useDeptWorkflowLifecycle(deptId, wfId);
  const evalWf = useEvaluateDeptWorkflow(deptId, wfId);
  const replayRun = useReplayDeptRun(deptId, wfId);
  const testAi = useTestDeptAi(deptId);

  const rfInstanceRef = useRef<ReactFlowInstance | null>(null);
  const nodeSeqRef = useRef(0);
  const [syncedKey, setSyncedKey] = useState('');
  const [trigger, setTrigger] = useState<DeptTriggerConfig>({ type: 'manual' });
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [inspectorMode, setInspectorMode] = useState<'trigger' | 'node'>('trigger');
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [reviewComment, setReviewComment] = useState('');
  const [aiSampleInput, setAiSampleInput] = useState('Customer asks for invoice #4029 refund.');
  const [aiTestResult, setAiTestResult] = useState<AiTestResponse | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [runsOpen, setRunsOpen] = useState(false);
  const [mobilePaletteOpen, setMobilePaletteOpen] = useState(false);
  const [mobileInspectorOpen, setMobileInspectorOpen] = useState(false);

  const runDetailQuery = useDeptRunDetail(deptId, selectedRunId);

  const wf = wfQuery.data;
  const syncToken = wf ? `${wf.id}:${wf.version}:${wf.updated_at}` : '';
  if (wf && syncToken !== syncedKey) {
    setSyncedKey(syncToken);
    setTrigger(wf.definition?.trigger ?? { type: 'manual' });
    setNodes(toFlowNodes(wf.definition?.nodes ?? []));
    setEdges(toFlowEdges(wf.definition?.edges ?? []));
  }

  const dept = dashQuery.data?.department;
  const deptTimezone = (dept as { timezone?: string } | undefined)?.timezone ?? 'Asia/Karachi';
  const isCompanyAdmin = active?.role === 'owner' || active?.role === 'admin';
  const effRole = isCompanyAdmin ? 'admin' : dept?.my_effective_role ?? active?.role ?? 'viewer';
  const canEdit = isCompanyAdmin || effRole === 'admin' || effRole === 'builder';
  const canReview = isCompanyAdmin || effRole === 'admin' || effRole === 'reviewer';
  const canAdmin = isCompanyAdmin || effRole === 'admin';

  const connectedProviders = useMemo(() => {
    const set = new Set<string>();
    for (const c of connsQuery.data ?? []) {
      if (c.status === 'CONNECTED') set.add(c.provider.toLowerCase());
    }
    return set;
  }, [connsQuery.data]);

  const availableEventSources = useMemo(() => {
    const out: { value: string; label: string }[] = [];
    if (connectedProviders.has('gmail')) out.push({ value: 'gmail.new_email', label: 'Gmail: New Email' });
    if (connectedProviders.has('slack')) out.push({ value: 'slack.message', label: 'Slack: New Message' });
    out.push({ value: 'webhook.received', label: 'Webhook: Received' });
    return out;
  }, [connectedProviders]);

  const availableActions = useMemo(() => {
    const out: { value: string; label: string }[] = [];
    if (connectedProviders.has('gmail')) {
      out.push({ value: 'gmail.send_reply', label: 'Gmail: Send Reply' });
      out.push({ value: 'gmail.send_email', label: 'Gmail: Send Email' });
    }
    if (connectedProviders.has('slack')) {
      out.push({ value: 'slack.post_message', label: 'Slack: Post Message' });
    }
    if (connectedProviders.has('hubspot')) {
      out.push({ value: 'hubspot.create_ticket', label: 'HubSpot: Create Ticket' });
      out.push({ value: 'hubspot.update_ticket', label: 'HubSpot: Update Ticket' });
      out.push({ value: 'hubspot.create_contact', label: 'HubSpot: Create Contact' });
    }
    if (connectedProviders.has('zoho_books')) {
      out.push({ value: 'zoho_books.create_bill', label: 'Zoho Books: Create Vendor Bill' });
    }
    if (connectedProviders.has('google_calendar')) {
      out.push({ value: 'google_calendar.create_event', label: 'Google Calendar: Schedule Meeting' });
    }
    if (connectedProviders.has('google_drive')) {
      out.push({ value: 'google_drive.upload_file', label: 'Google Drive: Upload File' });
    }
    if (connectedProviders.has('google_sheets')) {
      out.push({ value: 'sheets.append_row', label: 'Google Sheets: Append Row (Read-Only Export)' });
    }
    if (connectedProviders.has('stripe')) {
      out.push({ value: 'stripe.create_refund', label: 'Stripe: Create Refund (Approval Required)' });
    }
    return out;
  }, [connectedProviders]);

  const definitionNodes: DeptWorkflowNode[] = useMemo(
    () =>
      nodes.map((n) => {
        const d = n.data as Record<string, unknown>;
        return {
          id: n.id,
          type: (d.type as DeptWorkflowNode['type']) ?? 'agent',
          label: (d.label as string) ?? n.id,
          prompt: (d.prompt as string | null) ?? null,
          system_prompt: (d.system_prompt as string | null) ?? null,
          labels: (d.labels as string[] | null) ?? null,
          output_schema: (d.output_schema as Record<string, unknown> | null) ?? null,
          confidence_threshold:
            typeof d.confidence_threshold === 'number' ? d.confidence_threshold : 0,
          provider: (d.provider as DeptWorkflowNode['provider']) ?? 'auto',
          model: (d.model as string | null) ?? null,
          query_template: d.query_template as string | undefined,
          top_k: typeof d.top_k === 'number' ? d.top_k : undefined,
          include_company_shared:
            typeof d.include_company_shared === 'boolean' ? d.include_company_shared : undefined,
          mode: d.mode as DeptWorkflowNode['mode'],
          require_sources: typeof d.require_sources === 'boolean' ? d.require_sources : undefined,
          expression: d.expression as string | undefined,
          role: d.role as DeptWorkflowNode['role'],
          timeout_hours: (d.timeout_hours as number | null) ?? null,
          action: d.action as string | undefined,
          params: (d.params as Record<string, unknown>) ?? {},
          wait_seconds: typeof d.wait_seconds === 'number' ? d.wait_seconds : undefined,
          until_expression: (d.until_expression as string | null) ?? null,
        };
      }),
    [nodes],
  );

  const definitionEdges: DeptWorkflowEdge[] = useMemo(
    () =>
      edges.map((e) => ({
        source: e.source,
        target: e.target,
        label:
          e.sourceHandle === 'true' || e.label === 'true'
            ? 'true'
            : e.sourceHandle === 'false' || e.label === 'false'
              ? 'false'
              : null,
      })),
    [edges],
  );

  const nodeValidationErrors = useMemo(() => {
    const map: Record<string, string[]> = {};
    for (const dn of definitionNodes) {
      const errs = validateNodeConfig(dn, definitionEdges);
      if (errs.length > 0) map[dn.id] = errs;
    }
    return map;
  }, [definitionNodes, definitionEdges]);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((nds) => applyNodeChanges(changes, nds));
  }, []);

  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    setEdges((eds) => applyEdgeChanges(changes, eds));
  }, []);

  const onConnect = useCallback((params: Connection) => {
    const lbl = params.sourceHandle === 'true' || params.sourceHandle === 'false' ? params.sourceHandle : undefined;
    setEdges((eds) =>
      addEdge(
        {
          ...params,
          label: lbl,
          type: 'smoothstep',
          animated: true,
          style: {
            stroke:
              lbl === 'true'
                ? '#10b981'
                : lbl === 'false'
                  ? '#ef4444'
                  : 'var(--primary)',
            strokeWidth: 2,
          },
        },
        eds,
      ),
    );
  }, []);

  const selectedNode = definitionNodes.find((n) => n.id === selectedNodeId) ?? null;

  function patchSelectedNode(patch: Partial<DeptWorkflowNode>) {
    if (!selectedNodeId) return;
    setNodes((nds) =>
      nds.map((n) => (n.id === selectedNodeId ? { ...n, data: { ...n.data, ...patch } } : n)),
    );
  }

  function addNode(type: DeptWorkflowNode['type'], position?: { x: number; y: number }) {
    nodeSeqRef.current += 1;
    const nid = `${type}_${nodes.length + 1}_${nodeSeqRef.current}`;
    const defaults: Partial<DeptWorkflowNode> =
      type === 'classify'
        ? { labels: ['billing', 'query'], confidence_threshold: 0, provider: 'auto' }
        : type === 'retrieve'
          ? { query_template: '{{trigger.text}}', top_k: 5, include_company_shared: true, mode: 'knowledge' }
          : type === 'extract'
            ? { prompt: 'Extract key fields', provider: 'auto' }
            : type === 'agent'
              ? { prompt: 'Analyze input and respond', require_sources: false, provider: 'auto' }
              : type === 'condition'
                ? { expression: 'classify_1.confidence >= 0.8' }
                : type === 'approval'
                  ? { role: 'reviewer', timeout_hours: 24 }
                  : type === 'wait'
                    ? { wait_seconds: 60 }
                    : { action: availableActions[0]?.value ?? 'gmail.send_reply', params: { body: '' } };

    const newNode: Node = {
      id: nid,
      type: 'custom',
      position: position ?? { x: 160 + (nodes.length % 4) * 60, y: 120 + (nodes.length % 4) * 50 },
      data: { id: nid, type, label: nid, ...defaults },
    };
    setNodes((nds) => [...nds, newNode]);
    setSelectedNodeId(nid);
    setInspectorMode('node');
    setMobilePaletteOpen(false);
    setMobileInspectorOpen(true);
  }

  function onDragOver(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    if (!canEdit || wf?.status === 'in_review') return;
    const type = event.dataTransfer.getData('application/opsmind-node') as DeptWorkflowNode['type'];
    if (!type) return;
    const pos = rfInstanceRef.current?.screenToFlowPosition({
      x: event.clientX,
      y: event.clientY,
    });
    addNode(type, pos);
  }

  function buildPayload(): DeptWorkflowDefinition {
    return { trigger, nodes: definitionNodes, edges: definitionEdges };
  }

  async function handleSave() {
    try {
      await updateWf.mutateAsync({ definition: buildPayload() });
      toast({ variant: 'success', title: 'Workflow saved' });
    } catch (err) {
      toast({ variant: 'danger', title: 'Save failed', description: getErrorMessage(err) });
    }
  }

  async function handleLifecycle(fn: () => Promise<unknown>, successTitle: string) {
    try {
      await fn();
      toast({ variant: 'success', title: successTitle });
    } catch (err) {
      toast({ variant: 'danger', title: 'Action failed', description: getErrorMessage(err) });
    }
  }

  async function handleTestAiNode() {
    if (
      !selectedNode ||
      (selectedNode.type !== 'classify' &&
        selectedNode.type !== 'extract' &&
        selectedNode.type !== 'agent')
    ) {
      return;
    }
    setAiTestResult(null);
    try {
      const res = await testAi.mutateAsync({
        task: selectedNode.type,
        input: aiSampleInput,
        prompt: selectedNode.prompt,
        labels: selectedNode.labels,
        confidence_threshold: selectedNode.confidence_threshold ?? 0,
        provider: selectedNode.provider ?? 'auto',
        model: selectedNode.model,
      });
      setAiTestResult(res);
    } catch (err) {
      toast({ variant: 'danger', title: 'AI test failed', description: getErrorMessage(err) });
    }
  }

  if (wfQuery.isLoading || dashQuery.isLoading) {
    return (
      <div className="space-y-4 p-4 sm:p-6">
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-[72vh] w-full" />
      </div>
    );
  }

  if (wfQuery.isError || !wf) {
    return (
      <div className="p-4 sm:p-6">
        <ErrorState
          title="Could not load workflow"
          message={getErrorMessage(wfQuery.error)}
          onRetry={() => void wfQuery.refetch()}
        />
      </div>
    );
  }

  const totalValidationErrors = Object.values(nodeValidationErrors).flat().length;
  const previousNodeIds = definitionNodes.filter((n) => n.id !== selectedNodeId).map((n) => n.id);

  const inspectorContent = (
    <>
      {inspectorMode === 'trigger' || !selectedNode ? (
        <div className="space-y-4">
          <div className="flex items-center justify-between border-b border-[var(--border)] pb-3">
            <div className="flex items-center gap-2">
              <div className="flex h-7 w-7 items-center justify-center rounded-[var(--radius-md)] bg-emerald-500/15 text-emerald-500 border border-emerald-500/30">
                <Zap className="h-3.5 w-3.5" />
              </div>
              <div>
                <h2 className="text-xs font-bold uppercase tracking-wider text-[var(--text)]">
                  Trigger Configuration
                </h2>
                <p className="text-[11px] text-[var(--text-subtle)]">How this flow starts</p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => setMobileInspectorOpen(false)}
              className="rounded p-1 text-[var(--text-muted)] hover:bg-[var(--surface-muted)] lg:hidden cursor-pointer"
              aria-label="Close inspector"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <Select
            label="Trigger Type"
            disabled={!canEdit}
            value={trigger.type}
            onChange={(e) => {
              const t = e.target.value as DeptTriggerConfig['type'];
              if (t === 'schedule') {
                setTrigger({ type: 'schedule', mode: 'daily', time: '09:00' });
              } else if (t === 'event') {
                setTrigger({
                  type: 'event',
                  source: availableEventSources[0]?.value ?? 'webhook.received',
                });
              } else {
                setTrigger({ type: 'manual' });
              }
            }}
          >
            <option value="schedule">Schedule</option>
            <option value="event">Event</option>
            <option value="manual">Manual</option>
          </Select>

          {trigger.type === 'schedule' && (
            <ScheduleEditor
              value={trigger}
              timezone={deptTimezone}
              disabled={!canEdit}
              onChange={setTrigger}
            />
          )}

          {trigger.type === 'event' && (
            <Select
              label="Event Source (Connected Connectors)"
              disabled={!canEdit}
              value={trigger.source}
              onChange={(e) => setTrigger({ type: 'event', source: e.target.value })}
            >
              {availableEventSources.map((src) => (
                <option key={src.value} value={src.value}>
                  {src.label}
                </option>
              ))}
            </Select>
          )}

          {trigger.type === 'manual' && (
            <div className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface-muted)]/60 p-3 text-xs text-[var(--text-muted)]">
              Runs on demand when triggered manually by a builder or admin via <strong>Run Now</strong>.
            </div>
          )}

          {canReview && wf.status === 'in_review' && (
            <div className="border-t border-[var(--border)] pt-3">
              <Input
                label="Reviewer Comment"
                value={reviewComment}
                onChange={(e) => setReviewComment(e.target.value)}
                placeholder="Optional feedback..."
              />
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-4">
          <div className="flex items-center justify-between border-b border-[var(--border)] pb-3">
            <div className="min-w-0">
              <span className="font-mono text-[11px] font-bold uppercase tracking-wider text-[var(--primary)]">
                {selectedNode.type} Node
              </span>
              <p className="truncate font-mono text-[11px] text-[var(--text-subtle)]">
                {selectedNode.id}
              </p>
            </div>
            <div className="flex items-center gap-1">
              {canEdit && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label="Remove node"
                  onClick={() => {
                    setNodes((nds) => nds.filter((n) => n.id !== selectedNode.id));
                    setEdges((eds) =>
                      eds.filter(
                        (e) => e.source !== selectedNode.id && e.target !== selectedNode.id,
                      ),
                    );
                    setSelectedNodeId(null);
                    setInspectorMode('trigger');
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5 text-[var(--danger)]" />
                </Button>
              )}
              <button
                type="button"
                onClick={() => setMobileInspectorOpen(false)}
                className="rounded p-1 text-[var(--text-muted)] hover:bg-[var(--surface-muted)] lg:hidden cursor-pointer"
                aria-label="Close inspector"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>

          {(nodeValidationErrors[selectedNode.id] ?? []).map((err, i) => (
            <Alert key={i} kind="error">
              {err}
            </Alert>
          ))}

          <Input
            label="Label"
            disabled={!canEdit}
            value={selectedNode.label ?? ''}
            onChange={(e) => patchSelectedNode({ label: e.target.value })}
          />

          {(selectedNode.type === 'classify' ||
            selectedNode.type === 'extract' ||
            selectedNode.type === 'agent') && (
            <div className="space-y-3">
              <Input
                label="Prompt Guidance"
                disabled={!canEdit}
                value={selectedNode.prompt ?? ''}
                onChange={(e) => patchSelectedNode({ prompt: e.target.value })}
              />
              {selectedNode.type === 'classify' && (
                <Input
                  label="Labels (comma-separated)"
                  disabled={!canEdit}
                  value={(selectedNode.labels ?? []).join(', ')}
                  onChange={(e) =>
                    patchSelectedNode({
                      labels: e.target.value
                        .split(',')
                        .map((s) => s.trim())
                        .filter(Boolean),
                    })
                  }
                />
              )}
              {selectedNode.type === 'agent' && (
                <label className="flex items-center gap-2 text-xs text-[var(--text)] cursor-pointer">
                  <input
                    type="checkbox"
                    disabled={!canEdit}
                    checked={Boolean(selectedNode.require_sources)}
                    onChange={(e) => patchSelectedNode({ require_sources: e.target.checked })}
                  />
                  Require grounded Knowledge Hub sources (Policy Q&amp;A)
                </label>
              )}
              <Input
                label="Confidence Threshold (0.0 – 1.0)"
                type="number"
                step="0.05"
                min={0}
                max={1}
                disabled={!canEdit}
                value={selectedNode.confidence_threshold ?? 0}
                onChange={(e) =>
                  patchSelectedNode({ confidence_threshold: Number(e.target.value) })
                }
              />
              <Select
                label="Provider"
                disabled={!canEdit}
                value={selectedNode.provider ?? 'auto'}
                onChange={(e) =>
                  patchSelectedNode({
                    provider: e.target.value as DeptWorkflowNode['provider'],
                  })
                }
              >
                <option value="auto">Auto (Groq → Gemini)</option>
                <option value="groq">Groq</option>
                <option value="gemini">Gemini</option>
              </Select>
              <Input
                label="Model Override (optional)"
                disabled={!canEdit}
                value={selectedNode.model ?? ''}
                onChange={(e) => patchSelectedNode({ model: e.target.value || null })}
              />

              <div className="space-y-2 border-t border-[var(--border)] pt-3">
                <Input
                  label="Test Sample Input"
                  value={aiSampleInput}
                  onChange={(e) => setAiSampleInput(e.target.value)}
                />
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  className="w-full"
                  loading={testAi.isPending}
                  onClick={() => void handleTestAiNode()}
                >
                  <Sparkles className="h-3.5 w-3.5 text-violet-400" />
                  Test {selectedNode.type} node
                </Button>
                {aiTestResult && (
                  <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface-muted)] p-2.5 text-xs space-y-1">
                    <div className="flex items-center justify-between">
                      <Badge variant="primary">{aiTestResult.provider}</Badge>
                      <span className="font-mono text-[var(--text-muted)]">
                        {aiTestResult.latency_ms}ms
                      </span>
                    </div>
                    <pre className="overflow-x-auto font-mono text-[11px] text-[var(--text)]">
                      {JSON.stringify(aiTestResult.output, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            </div>
          )}

          {selectedNode.type === 'retrieve' && (
            <div className="space-y-3">
              <Select
                label="Retrieve Mode"
                disabled={!canEdit}
                value={selectedNode.mode ?? 'knowledge'}
                onChange={(e) =>
                  patchSelectedNode({ mode: e.target.value as 'knowledge' | 'aggregate_ops' })
                }
              >
                <option value="knowledge">Knowledge Hub (Semantic / Lexical)</option>
                <option value="aggregate_ops">Aggregate Ops Metrics (Weekly Report)</option>
              </Select>
              <Input
                label="Query Template"
                disabled={!canEdit}
                value={selectedNode.query_template ?? ''}
                placeholder="{{trigger.text}}"
                onChange={(e) => patchSelectedNode({ query_template: e.target.value })}
              />
              <Input
                label="Top K Results"
                type="number"
                min={1}
                max={20}
                disabled={!canEdit}
                value={selectedNode.top_k ?? 5}
                onChange={(e) => patchSelectedNode({ top_k: Number(e.target.value) })}
              />
              <label className="flex items-center gap-2 text-xs text-[var(--text)] cursor-pointer">
                <input
                  type="checkbox"
                  disabled={!canEdit}
                  checked={selectedNode.include_company_shared ?? true}
                  onChange={(e) => patchSelectedNode({ include_company_shared: e.target.checked })}
                />
                Include Company-Shared Knowledge
              </label>
            </div>
          )}

          {selectedNode.type === 'wait' && (
            <div className="space-y-3">
              <Input
                label="Wait Seconds"
                type="number"
                min={0}
                max={86400}
                disabled={!canEdit}
                value={selectedNode.wait_seconds ?? 60}
                onChange={(e) => patchSelectedNode({ wait_seconds: Number(e.target.value) })}
              />
              <Input
                label="Until Expression (optional)"
                disabled={!canEdit}
                value={selectedNode.until_expression ?? ''}
                placeholder="trigger.ready == true"
                onChange={(e) => patchSelectedNode({ until_expression: e.target.value || null })}
              />
            </div>
          )}

          {selectedNode.type === 'condition' && (
            <Input
              label="Expression"
              disabled={!canEdit}
              value={selectedNode.expression ?? ''}
              placeholder="ai_1.score >= 70"
              onChange={(e) => patchSelectedNode({ expression: e.target.value })}
            />
          )}

          {selectedNode.type === 'approval' && (
            <div className="space-y-3">
              <Select
                label="Required Role"
                disabled={!canEdit}
                value={selectedNode.role ?? 'reviewer'}
                onChange={(e) =>
                  patchSelectedNode({ role: e.target.value as 'reviewer' | 'admin' })
                }
              >
                <option value="reviewer">Reviewer</option>
                <option value="admin">Admin</option>
              </Select>
              <Input
                label="Timeout Hours (1–720, optional)"
                type="number"
                min={1}
                max={720}
                disabled={!canEdit}
                value={selectedNode.timeout_hours ?? ''}
                onChange={(e) =>
                  patchSelectedNode({
                    timeout_hours: e.target.value ? Number(e.target.value) : null,
                  })
                }
              />
            </div>
          )}

          {selectedNode.type === 'action' && (
            <div className="space-y-3">
              <Select
                label="Action (Connected Connectors)"
                disabled={!canEdit}
                value={selectedNode.action ?? ''}
                onChange={(e) => patchSelectedNode({ action: e.target.value })}
              >
                {availableActions.length === 0 ? (
                  <option value={selectedNode.action ?? ''}>
                    {selectedNode.action || 'No connected action providers'}
                  </option>
                ) : (
                  availableActions.map((act) => (
                    <option key={act.value} value={act.value}>
                      {act.label}
                    </option>
                  ))
                )}
              </Select>
              <Input
                label="Body / Message Param"
                disabled={!canEdit}
                value={String(selectedNode.params?.body ?? selectedNode.params?.message ?? '')}
                onChange={(e) =>
                  patchSelectedNode({
                    params: { ...(selectedNode.params ?? {}), body: e.target.value },
                  })
                }
              />
              {previousNodeIds.length > 0 && (
                <div className="space-y-1.5">
                  <span className="block text-[11px] font-medium text-[var(--text-muted)]">
                    Insert variable from step:
                  </span>
                  <div className="flex flex-wrap gap-1.5">
                    {previousNodeIds.map((pid) => (
                      <button
                        key={pid}
                        type="button"
                        disabled={!canEdit}
                        onClick={() => {
                          const curr = String(selectedNode.params?.body ?? '');
                          patchSelectedNode({
                            params: {
                              ...(selectedNode.params ?? {}),
                              body: `${curr}{{${pid}.text}}`,
                            },
                          });
                        }}
                        className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-muted)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--text)] hover:border-[var(--primary)] cursor-pointer"
                      >
                        {`{{${pid}.text}}`}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </>
  );

  return (
    <div className="flex h-[calc(100vh-3.5rem)] flex-col overflow-hidden bg-[var(--bg)]">
      {/* ── n8n Studio Top Bar ──────────────────────────────────────── */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border)] bg-[var(--surface)] px-3 py-2.5 sm:px-5">
        <div className="flex min-w-0 items-center gap-2.5">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-label="Back to department"
            onClick={() => navigate(`/departments/${deptId}`)}
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="font-display truncate text-sm font-bold text-[var(--text)] sm:text-base">
                {wf.name}
              </h1>
              <Badge variant={STATUS_BADGE[wf.status] ?? 'neutral'} size="xs">
                {wf.status}
              </Badge>
              <Badge variant="neutral" size="xs">
                v{wf.version}
              </Badge>
              {wf.active_version && (
                <Badge variant="success" size="xs">
                  Active: v{wf.active_version}
                </Badge>
              )}
            </div>
            {wf.review_comment && (
              <p className="mt-0.5 truncate text-xs text-[var(--warning)]">
                Review note: {wf.review_comment}
              </p>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-1.5 sm:gap-2">
          {canEdit && wf.status !== 'in_review' && (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="md:hidden"
              onClick={() => setMobilePaletteOpen(true)}
            >
              <Layers className="h-3.5 w-3.5" />
              Nodes
            </Button>
          )}
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="lg:hidden"
            onClick={() => setMobileInspectorOpen(true)}
          >
            <SlidersHorizontal className="h-3.5 w-3.5" />
            Inspector
          </Button>

          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={() => {
              setInspectorMode('trigger');
              setSelectedNodeId(null);
              setMobileInspectorOpen(true);
            }}
          >
            <Zap className="h-3.5 w-3.5 text-emerald-500" />
            Trigger
          </Button>
          <Button type="button" variant="secondary" size="sm" onClick={() => setRunsOpen(true)}>
            <History className="h-3.5 w-3.5" />
            Runs ({runsQuery.data?.length ?? 0})
          </Button>

          {canEdit && wf.status !== 'in_review' && (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={updateWf.isPending}
              onClick={() => void handleSave()}
            >
              <Save className="h-3.5 w-3.5" />
              Save Draft
            </Button>
          )}

          {canEdit && (wf.status === 'draft' || wf.status === 'paused') && (
            <Button
              type="button"
              size="sm"
              loading={lifecycle.submitReview.isPending}
              onClick={() =>
                void handleLifecycle(
                  () => lifecycle.submitReview.mutateAsync(),
                  'Submitted for review',
                )
              }
            >
              <Send className="h-3.5 w-3.5" />
              Submit for Review
            </Button>
          )}

          {canReview && wf.status === 'in_review' && (
            <>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                loading={lifecycle.decideReview.isPending}
                onClick={() =>
                  void handleLifecycle(
                    () =>
                      lifecycle.decideReview.mutateAsync({
                        decision: 'send_back',
                        comment: reviewComment.trim() || 'Changes requested',
                      }),
                    'Sent back to draft',
                  )
                }
              >
                Send Back
              </Button>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                loading={lifecycle.decideReview.isPending}
                onClick={() =>
                  void handleLifecycle(
                    () =>
                      lifecycle.decideReview.mutateAsync({
                        decision: 'approve',
                        comment: reviewComment.trim() || 'Approved',
                      }),
                    'Review approved',
                  )
                }
              >
                <CheckCircle2 className="h-3.5 w-3.5 text-[var(--success)]" />
                Approve Review
              </Button>
            </>
          )}

          {canAdmin && wf.status === 'in_review' && (
            <>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                loading={evalWf.isPending}
                onClick={() =>
                  void handleLifecycle(async () => {
                    const res = await evalWf.mutateAsync();
                    if (!res.passed) {
                      throw new Error(
                        `Eval score ${res.score.toFixed(2)} below threshold ${res.pass_threshold.toFixed(2)}`,
                      );
                    }
                    return res;
                  }, 'Evaluation passed')
                }
              >
                <FlaskConical className="h-3.5 w-3.5" />
                Run Eval
              </Button>
              <Button
                type="button"
                size="sm"
                loading={lifecycle.activate.isPending}
                onClick={() =>
                  void handleLifecycle(() => lifecycle.activate.mutateAsync(), 'Workflow activated')
                }
              >
                Activate
              </Button>
            </>
          )}

          {canAdmin && wf.status === 'active' && (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={lifecycle.pause.isPending}
              onClick={() =>
                void handleLifecycle(() => lifecycle.pause.mutateAsync(), 'Workflow paused')
              }
            >
              <Pause className="h-3.5 w-3.5" />
              Pause
            </Button>
          )}

          {canAdmin && wf.status === 'paused' && (
            <Button
              type="button"
              size="sm"
              loading={lifecycle.resume.isPending}
              onClick={() =>
                void handleLifecycle(() => lifecycle.resume.mutateAsync(), 'Workflow resumed')
              }
            >
              <Play className="h-3.5 w-3.5" />
              Resume
            </Button>
          )}

          {canEdit && wf.status === 'active' && (
            <Button
              type="button"
              size="sm"
              loading={lifecycle.runNow.isPending}
              onClick={() =>
                void handleLifecycle(() => lifecycle.runNow.mutateAsync({}), 'Manual run queued')
              }
            >
              <Play className="h-3.5 w-3.5" />
              Run Now
            </Button>
          )}
        </div>
      </div>

      {totalValidationErrors > 0 && (
        <div className="border-b border-[var(--border)] bg-[var(--warning-soft)] px-4 py-2">
          <div className="flex items-center gap-2 text-xs font-medium text-[var(--warning)]">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span>
              {totalValidationErrors} node configuration issue(s) detected. Select highlighted nodes
              to fix before activating.
            </span>
          </div>
        </div>
      )}

      {/* ── Main Studio Workspace ───────────────────────────────────── */}
      <div className="relative flex flex-1 min-h-0 overflow-hidden">
        {/* Left Node Palette (Desktop docked + Mobile drawer) */}
        {canEdit && wf.status !== 'in_review' && (
          <>
            <aside className="hidden w-60 shrink-0 flex-col border-r border-[var(--border)] bg-[var(--surface)] p-3.5 overflow-y-auto md:flex">
              <div className="mb-3 flex items-center justify-between">
                <span className="text-[11px] font-bold uppercase tracking-wider text-[var(--text-muted)]">
                  Node Palette
                </span>
                <span className="text-[10px] text-[var(--text-subtle)]">Drag or click</span>
              </div>

              <div className="space-y-2">
                {PALETTE_ITEMS.map((item) => {
                  const Icon = item.icon;
                  return (
                    <button
                      key={item.type}
                      type="button"
                      draggable
                      onDragStart={(e) => {
                        e.dataTransfer.setData('application/opsmind-node', item.type);
                        e.dataTransfer.effectAllowed = 'move';
                      }}
                      onClick={() => addNode(item.type)}
                      className="group flex w-full items-start gap-3 rounded-[var(--radius-xl)] border border-[var(--border)] bg-[var(--bg)]/60 p-2.5 text-left transition-all hover:border-[var(--primary)]/50 hover:bg-[var(--surface-muted)] hover:shadow-xs cursor-grab active:cursor-grabbing"
                    >
                      <div
                        className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius-md)] border ${item.tone}`}
                      >
                        <Icon className="h-4 w-4" />
                      </div>
                      <div className="min-w-0">
                        <div className="text-xs font-semibold text-[var(--text)]">{item.label}</div>
                        <div className="mt-0.5 text-[11px] leading-snug text-[var(--text-subtle)]">
                          {item.description}
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            </aside>

            {mobilePaletteOpen && (
              <div className="fixed inset-0 z-40 md:hidden">
                <div
                  className="fixed inset-0 bg-black/50 backdrop-blur-xs"
                  onClick={() => setMobilePaletteOpen(false)}
                />
                <div className="fixed inset-y-0 left-0 z-50 w-72 border-r border-[var(--border)] bg-[var(--surface)] p-4 shadow-[var(--shadow-lg)] overflow-y-auto">
                  <div className="mb-4 flex items-center justify-between">
                    <span className="text-xs font-bold uppercase tracking-wider text-[var(--text)]">
                      Add Node
                    </span>
                    <button
                      type="button"
                      onClick={() => setMobilePaletteOpen(false)}
                      className="rounded p-1 text-[var(--text-muted)]"
                    >
                      <X className="h-4 w-4" />
                    </button>
                  </div>
                  <div className="space-y-2">
                    {PALETTE_ITEMS.map((item) => {
                      const Icon = item.icon;
                      return (
                        <button
                          key={item.type}
                          type="button"
                          onClick={() => addNode(item.type)}
                          className="flex w-full items-start gap-3 rounded-[var(--radius-xl)] border border-[var(--border)] bg-[var(--bg)] p-3 text-left"
                        >
                          <div
                            className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius-md)] border ${item.tone}`}
                          >
                            <Icon className="h-4 w-4" />
                          </div>
                          <div>
                            <div className="text-xs font-semibold text-[var(--text)]">{item.label}</div>
                            <div className="text-[11px] text-[var(--text-subtle)]">
                              {item.description}
                            </div>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>
              </div>
            )}
          </>
        )}

        {/* Center n8n Canvas */}
        <div
          className="relative flex-1 min-w-0 bg-[var(--canvas-bg)]"
          onDragOver={onDragOver}
          onDrop={onDrop}
        >
          {nodes.length === 0 && (
            <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center p-6">
              <div className="max-w-sm rounded-[var(--radius-2xl)] border border-dashed border-[var(--border-hover)] bg-[var(--surface)]/90 p-6 text-center shadow-[var(--shadow-md)] backdrop-blur-md">
                <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-[var(--radius-xl)] bg-[var(--primary-soft)] text-[var(--primary)]">
                  <Sparkles className="h-5 w-5" />
                </div>
                <p className="font-display text-sm font-semibold text-[var(--text)]">
                  Start building your workflow
                </p>
                <p className="mt-1 text-xs text-[var(--text-muted)]">
                  Drag or click a node from the left palette to place your first step on the canvas.
                </p>
              </div>
            </div>
          )}

          <ReactFlow
            nodes={nodes}
            edges={edges}
            onInit={(instance) => {
              rfInstanceRef.current = instance;
              instance.fitView({ padding: 0.25 });
            }}
            onNodesChange={canEdit ? onNodesChange : undefined}
            onEdgesChange={canEdit ? onEdgesChange : undefined}
            onConnect={canEdit ? onConnect : undefined}
            nodesDraggable={canEdit}
            nodesConnectable={canEdit}
            connectionLineType={ConnectionLineType.SmoothStep}
            nodeTypes={NODE_TYPES}
            onNodeClick={(_, n) => {
              setSelectedNodeId(n.id);
              setInspectorMode('node');
              setAiTestResult(null);
              setMobileInspectorOpen(true);
            }}
            colorMode={theme}
            fitView
          >
            <Background
              variant={BackgroundVariant.Dots}
              gap={20}
              size={1.25}
              color="var(--canvas-dots)"
            />
            <Controls />
            <MiniMap
              className="!hidden sm:!block"
              nodeColor="var(--primary)"
              maskColor={theme === 'dark' ? 'rgba(8,9,14,0.7)' : 'rgba(241,245,249,0.7)'}
            />
          </ReactFlow>
        </div>

        {/* Right Inspector Panel (Desktop docked) */}
        <aside className="hidden w-80 xl:w-96 shrink-0 overflow-y-auto border-l border-[var(--border)] bg-[var(--surface)] p-4 lg:block">
          {inspectorContent}
        </aside>

        {/* Mobile/Tablet Inspector Drawer */}
        {mobileInspectorOpen && (
          <div className="fixed inset-0 z-40 lg:hidden">
            <div
              className="fixed inset-0 bg-black/50 backdrop-blur-xs"
              onClick={() => setMobileInspectorOpen(false)}
            />
            <div className="fixed inset-y-0 right-0 z-50 w-[min(22rem,92vw)] overflow-y-auto border-l border-[var(--border)] bg-[var(--surface)] p-4 shadow-[var(--shadow-lg)]">
              {inspectorContent}
            </div>
          </div>
        )}
      </div>

      {/* Runs Dialog */}
      <Dialog
        open={runsOpen}
        onOpenChange={setRunsOpen}
        title="Workflow Runs"
        description="Inspect execution history and per-step redacted outputs."
        maxWidth="max-w-2xl"
      >
        <div className="space-y-4">
          {(runsQuery.data ?? []).length === 0 ? (
            <p className="text-xs text-[var(--text-muted)]">No runs recorded yet.</p>
          ) : (
            <div className="max-h-48 space-y-2 overflow-y-auto">
              {(runsQuery.data ?? []).map((r) => (
                <button
                  key={r.id}
                  type="button"
                  onClick={() => setSelectedRunId(r.id)}
                  className={`flex w-full items-center justify-between rounded-[var(--radius-md)] border p-2.5 text-left text-xs cursor-pointer ${
                    selectedRunId === r.id
                      ? 'border-[var(--primary)] bg-[var(--primary-soft)]'
                      : 'border-[var(--border)] hover:bg-[var(--surface-muted)]'
                  }`}
                >
                  <span className="font-mono">
                    {r.id.slice(0, 8)} • {r.trigger_type}
                  </span>
                  <Badge
                    variant={
                      r.status === 'succeeded'
                        ? 'success'
                        : r.status === 'failed'
                          ? 'danger'
                          : 'warning'
                    }
                  >
                    {r.status}
                  </Badge>
                </button>
              ))}
            </div>
          )}

          {runDetailQuery.data && (
            <Card>
              <CardHeader className="pb-2">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="text-xs font-semibold text-[var(--text)]">
                    Run Steps ({runDetailQuery.data.steps.length})
                  </h3>
                  {canEdit && selectedRunId && (
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      loading={replayRun.isPending}
                      onClick={() =>
                        void handleLifecycle(async () => {
                          const replayed = await replayRun.mutateAsync(selectedRunId);
                          setSelectedRunId(replayed.id);
                          return replayed;
                        }, 'Run replayed')
                      }
                    >
                      <RotateCcw className="h-3.5 w-3.5" />
                      Replay
                    </Button>
                  )}
                </div>
              </CardHeader>
              <CardContent className="space-y-2">
                {runDetailQuery.data.steps.map((s) => (
                  <div
                    key={s.id}
                    className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface-muted)] p-2 text-xs"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-semibold">
                        {s.node_id} ({s.node_type})
                      </span>
                      <Badge variant={s.status === 'completed' ? 'success' : 'danger'}>
                        {s.status}
                      </Badge>
                    </div>
                    {s.output && (
                      <pre className="mt-1 overflow-x-auto font-mono text-[11px] text-[var(--text-muted)]">
                        {JSON.stringify(s.output)}
                      </pre>
                    )}
                    {s.error && <p className="mt-1 text-[var(--danger)]">{s.error}</p>}
                  </div>
                ))}
              </CardContent>
            </Card>
          )}
        </div>
      </Dialog>
    </div>
  );
}
