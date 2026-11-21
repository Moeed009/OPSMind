import { useState, type FormEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  Building2,
  Check,
  Copy,
  HelpCircle,
  Mail,
  Plus,
  Trash2,
  UserPlus,
  Users,
} from 'lucide-react';
import { useActiveTenant, useMe } from '../lib/hooks';
import {
  useChangeMemberRole,
  useCreateInvitation,
  useInvitations,
  useMembers,
  useRemoveMember,
  useRevokeInvitation,
} from '../lib/membersApi';
import { useDepartments } from '../lib/departmentsApi';
import { api, getErrorMessage } from '../lib/api';
import type { DepartmentRole, Invitation, InvitationCreated, Member, Role } from '../lib/types';
import {
  Alert,
  Badge,
  Button,
  Card,
  CardContent,
  ConfirmDialog,
  Dialog,
  EmptyState,
  ErrorState,
  Input,
  PageHeader,
  RoleBadge,
  Select,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  Tooltip,
  useToast,
} from '../components/ui';

type AssignableRole = 'admin' | 'builder' | 'reviewer' | 'viewer';

const INVITE_ROLES: { value: AssignableRole; label: string }[] = [
  { value: 'viewer', label: 'Viewer' },
  { value: 'reviewer', label: 'Reviewer' },
  { value: 'builder', label: 'Builder' },
  { value: 'admin', label: 'Admin' },
];

const DEPT_ROLES: { value: DepartmentRole; label: string }[] = [
  { value: 'viewer', label: 'Viewer' },
  { value: 'reviewer', label: 'Reviewer' },
  { value: 'builder', label: 'Builder' },
  { value: 'admin', label: 'Admin' },
];

const REVIEWER_TOOLTIP =
  'Reviewer: Human-in-the-loop (HITL) approval of AI actions in the Review Inbox.';

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString();
}

export function MembersPage() {
  const { active } = useActiveTenant();
  const { data: me } = useMe();
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const myRole = active?.role;
  const currentUserId = me?.id;
  const canManage = myRole === 'owner' || myRole === 'admin';
  const isOwner = myRole === 'owner';

  const membersQuery = useMembers();
  const invitationsQuery = useInvitations();
  const departmentsQuery = useDepartments();

  const changeRole = useChangeMemberRole();
  const removeMember = useRemoveMember();
  const createInvitation = useCreateInvitation();
  const revokeInvitation = useRevokeInvitation();

  const [activeTab, setActiveTab] = useState<'members' | 'invitations'>('members');

  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState<AssignableRole>('builder');
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [inviteBanner, setInviteBanner] = useState<InvitationCreated | null>(null);
  const [copiedLink, setCopiedLink] = useState(false);

  const [assignMember, setAssignMember] = useState<Member | null>(null);
  const [assignDeptId, setAssignDeptId] = useState('');
  const [assignDeptRole, setAssignDeptRole] = useState<DepartmentRole>('builder');
  const [assignSaving, setAssignSaving] = useState(false);
  const [assignError, setAssignError] = useState<string | null>(null);

  const [confirmRemoveMember, setConfirmRemoveMember] = useState<Member | null>(null);
  const [confirmRevokeInvite, setConfirmRevokeInvite] = useState<Invitation | null>(null);

  const members = membersQuery.data ?? [];
  const invitations = invitationsQuery.data ?? [];
  const departments = departmentsQuery.data ?? [];

  const assignableTenantRoles: Role[] = isOwner
    ? ['admin', 'builder', 'reviewer', 'viewer']
    : ['builder', 'reviewer', 'viewer'];

  function openAssignDialog(member: Member) {
    setAssignMember(member);
    setAssignDeptId(departments[0]?.id ?? '');
    setAssignDeptRole('builder');
    setAssignError(null);
  }

  async function handleAssignDepartment(e: FormEvent) {
    e.preventDefault();
    if (!assignMember || !assignDeptId) return;
    setAssignSaving(true);
    setAssignError(null);
    try {
      await api(`/departments/${assignDeptId}/members`, {
        method: 'POST',
        body: {
          user_ids: [assignMember.user_id],
          role: assignDeptRole,
        },
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['members'] }),
        queryClient.invalidateQueries({ queryKey: ['departments'] }),
        queryClient.invalidateQueries({ queryKey: ['department-dashboard', assignDeptId] }),
      ]);
      toast({
        variant: 'success',
        title: 'Assigned to department',
        description: `${assignMember.email} assigned as ${assignDeptRole}.`,
      });
      setAssignMember(null);
    } catch (err) {
      setAssignError(getErrorMessage(err));
    } finally {
      setAssignSaving(false);
    }
  }

  async function handleInviteSubmit(e: FormEvent) {
    e.preventDefault();
    setInviteError(null);
    try {
      const created = await createInvitation.mutateAsync({
        email: inviteEmail.trim(),
        role: inviteRole,
      });
      setInviteBanner(created);
      setInviteEmail('');
      setInviteRole('builder');
      setInviteOpen(false);
      setActiveTab('invitations');
      const isFailed = created.email_sent === false && created.email_status === 'failed';
      toast({
        variant: isFailed ? 'warning' : 'success',
        title: created.email_sent ? 'Invitation email sent' : 'Invitation created',
        description: `Invited ${created.email} as ${created.role}.`,
      });
    } catch (err) {
      setInviteError(getErrorMessage(err));
    }
  }

  function buildInviteUrl(token: string): string {
    return `${window.location.origin}/accept-invite?token=${encodeURIComponent(token)}`;
  }

  async function handleCopyInviteLink(url: string) {
    try {
      await navigator.clipboard.writeText(url);
      setCopiedLink(true);
      setTimeout(() => setCopiedLink(false), 2000);
    } catch {
      toast({ variant: 'danger', title: 'Could not copy link' });
    }
  }

  async function handleTenantRoleChange(member: Member, nextRole: Role) {
    try {
      await changeRole.mutateAsync({ userId: member.user_id, role: nextRole });
      toast({
        variant: 'success',
        title: 'Role updated',
        description: `${member.email} is now ${nextRole}.`,
      });
    } catch (err) {
      toast({
        variant: 'danger',
        title: 'Could not update role',
        description: getErrorMessage(err),
      });
    }
  }

  async function handleConfirmRemoveMember() {
    if (!confirmRemoveMember) return;
    try {
      await removeMember.mutateAsync(confirmRemoveMember.user_id);
      toast({
        variant: 'success',
        title: 'Member removed',
        description: `${confirmRemoveMember.email} was removed from the company.`,
      });
      setConfirmRemoveMember(null);
    } catch (err) {
      toast({
        variant: 'danger',
        title: 'Could not remove member',
        description: getErrorMessage(err),
      });
    }
  }

  async function handleConfirmRevokeInvite() {
    if (!confirmRevokeInvite) return;
    try {
      await revokeInvitation.mutateAsync(confirmRevokeInvite.id);
      toast({
        variant: 'success',
        title: 'Invitation revoked',
        description: `Revoked invitation for ${confirmRevokeInvite.email}.`,
      });
      setConfirmRevokeInvite(null);
    } catch (err) {
      toast({
        variant: 'danger',
        title: 'Could not revoke invitation',
        description: getErrorMessage(err),
      });
    }
  }

  const inviteUrl = inviteBanner ? buildInviteUrl(inviteBanner.token) : '';

  return (
    <div className="space-y-6">
      <PageHeader
        title="Members"
        description={
          active
            ? `Manage workspace members, tenant roles, and department assignments for ${active.name}.`
            : 'Manage workspace members and invitations.'
        }
        actions={
          canManage ? (
            <Button
              variant="primary"
              size="sm"
              onClick={() => {
                setInviteError(null);
                setInviteOpen(true);
              }}
            >
              <UserPlus className="h-4 w-4" />
              Invite member
            </Button>
          ) : undefined
        }
      />

      {inviteBanner && (
        <Alert
          kind={
            inviteBanner.email_sent
              ? 'success'
              : inviteBanner.email_status === 'failed'
                ? 'warning'
                : 'info'
          }
          onClose={() => setInviteBanner(null)}
        >
          <p className="font-medium">
            {inviteBanner.email_sent
              ? `Invitation sent to ${inviteBanner.email}`
              : inviteBanner.email_status === 'failed'
                ? `Invitation created for ${inviteBanner.email}, but email delivery failed.`
                : `Invitation created for ${inviteBanner.email} (${inviteBanner.email_status ?? 'simulated mode'}).`}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <code className="max-w-full truncate rounded-[var(--radius-sm)] bg-[var(--surface-muted)] px-2 py-1 text-xs text-[var(--text)]">
              {inviteUrl}
            </code>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => void handleCopyInviteLink(inviteUrl)}
            >
              {copiedLink ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
              {copiedLink ? 'Copied' : 'Copy invite link'}
            </Button>
          </div>
        </Alert>
      )}

      <Tabs
        value={activeTab}
        onValueChange={(v) => setActiveTab(v === 'invitations' ? 'invitations' : 'members')}
      >
        <TabsList>
          <TabsTrigger value="members">Members ({members.length})</TabsTrigger>
          <TabsTrigger value="invitations">Pending Invitations ({invitations.length})</TabsTrigger>
        </TabsList>

        <TabsContent value="members" className="mt-4">
          {membersQuery.isLoading ? (
            <Card>
              <CardContent className="space-y-3 pt-5">
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </CardContent>
            </Card>
          ) : membersQuery.isError ? (
            <ErrorState
              title="Could not load members"
              message={getErrorMessage(membersQuery.error)}
              onRetry={() => void membersQuery.refetch()}
            />
          ) : members.length === 0 ? (
            <EmptyState
              icon={Users}
              title="No members yet"
              description="Invite teammates to collaborate in this company."
              action={
                canManage ? (
                  <Button variant="primary" size="sm" onClick={() => setInviteOpen(true)}>
                    <UserPlus className="h-4 w-4" />
                    Invite member
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <>
              <div className="hidden md:block">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Member</TableHead>
                      <TableHead>
                        <span className="inline-flex items-center gap-1">
                          Tenant role
                          <Tooltip content={REVIEWER_TOOLTIP}>
                            <button
                              type="button"
                              aria-label="About reviewer role"
                              className="text-[var(--text-subtle)] hover:text-[var(--text)]"
                            >
                              <HelpCircle className="h-3.5 w-3.5" />
                            </button>
                          </Tooltip>
                        </span>
                      </TableHead>
                      <TableHead>Departments</TableHead>
                      <TableHead>Joined</TableHead>
                      {canManage && <TableHead className="text-right">Actions</TableHead>}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {members.map((m) => {
                      const isSelf = m.user_id === currentUserId;
                      const isRowOwner = m.role === 'owner';
                      const canEditThisRow =
                        canManage && !isSelf && !isRowOwner && (isOwner || m.role !== 'admin');

                      return (
                        <TableRow key={m.user_id}>
                          <TableCell>
                            <div className="min-w-0">
                              <p className="truncate font-medium text-[var(--text)]">
                                {m.full_name || m.email}
                                {isSelf && (
                                  <span className="ml-2 text-xs text-[var(--text-subtle)]">(you)</span>
                                )}
                              </p>
                              <p className="truncate text-xs text-[var(--text-muted)]">{m.email}</p>
                            </div>
                          </TableCell>
                          <TableCell>
                            {canEditThisRow ? (
                              <select
                                aria-label={`Tenant role for ${m.email}`}
                                value={m.role}
                                onChange={(e) => void handleTenantRoleChange(m, e.target.value as Role)}
                                disabled={changeRole.isPending}
                                className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1 text-xs font-medium text-[var(--text)] focus:border-[var(--primary)] focus:outline-none"
                              >
                                {assignableTenantRoles.map((r) => (
                                  <option key={r} value={r}>
                                    {r}
                                  </option>
                                ))}
                              </select>
                            ) : (
                              <RoleBadge role={m.role} />
                            )}
                          </TableCell>
                          <TableCell>
                            {m.departments && m.departments.length > 0 ? (
                              <div className="flex flex-wrap gap-1.5">
                                {m.departments.map((d) => (
                                  <Badge key={d.id} variant="neutral">
                                    {d.name} : {d.role ?? d.effective_role}
                                  </Badge>
                                ))}
                              </div>
                            ) : (
                              <span className="text-xs text-[var(--text-subtle)]">None</span>
                            )}
                          </TableCell>
                          <TableCell className="text-xs text-[var(--text-muted)]">
                            {formatDate(m.joined_at)}
                          </TableCell>
                          {canManage && (
                            <TableCell className="text-right">
                              <div className="inline-flex items-center gap-1.5">
                                {departments.length > 0 && (
                                  <Button
                                    type="button"
                                    variant="secondary"
                                    size="sm"
                                    onClick={() => openAssignDialog(m)}
                                  >
                                    <Building2 className="h-3.5 w-3.5" />
                                    Assign to department
                                  </Button>
                                )}
                                {canEditThisRow && (
                                  <Button
                                    type="button"
                                    variant="ghost"
                                    size="sm"
                                    aria-label={`Remove ${m.email}`}
                                    onClick={() => setConfirmRemoveMember(m)}
                                  >
                                    <Trash2 className="h-3.5 w-3.5 text-[var(--danger)]" />
                                  </Button>
                                )}
                              </div>
                            </TableCell>
                          )}
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>

              <div className="space-y-3 md:hidden">
                {members.map((m) => {
                  const isSelf = m.user_id === currentUserId;
                  const isRowOwner = m.role === 'owner';
                  const canEditThisRow =
                    canManage && !isSelf && !isRowOwner && (isOwner || m.role !== 'admin');

                  return (
                    <Card key={m.user_id}>
                      <CardContent className="space-y-3 pt-5">
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <p className="truncate text-sm font-semibold text-[var(--text)]">
                              {m.full_name || m.email}
                            </p>
                            <p className="truncate text-xs text-[var(--text-muted)]">{m.email}</p>
                          </div>
                          <RoleBadge role={m.role} />
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                          {m.departments && m.departments.length > 0 ? (
                            m.departments.map((d) => (
                              <Badge key={d.id} variant="neutral">
                                {d.name} : {d.role ?? d.effective_role}
                              </Badge>
                            ))
                          ) : (
                            <span className="text-xs text-[var(--text-subtle)]">
                              No department assignments
                            </span>
                          )}
                        </div>
                        {canManage && (
                          <div className="flex flex-wrap items-center gap-2 pt-1">
                            {canEditThisRow && (
                              <select
                                aria-label={`Tenant role for ${m.email}`}
                                value={m.role}
                                onChange={(e) => void handleTenantRoleChange(m, e.target.value as Role)}
                                disabled={changeRole.isPending}
                                className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-xs font-medium text-[var(--text)]"
                              >
                                {assignableTenantRoles.map((r) => (
                                  <option key={r} value={r}>
                                    {r}
                                  </option>
                                ))}
                              </select>
                            )}
                            {departments.length > 0 && (
                              <Button
                                type="button"
                                variant="secondary"
                                size="sm"
                                onClick={() => openAssignDialog(m)}
                              >
                                <Building2 className="h-3.5 w-3.5" />
                                Assign to department
                              </Button>
                            )}
                            {canEditThisRow && (
                              <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                onClick={() => setConfirmRemoveMember(m)}
                              >
                                <Trash2 className="h-3.5 w-3.5 text-[var(--danger)]" />
                                Remove
                              </Button>
                            )}
                          </div>
                        )}
                      </CardContent>
                    </Card>
                  );
                })}
              </div>
            </>
          )}
        </TabsContent>

        <TabsContent value="invitations" className="mt-4">
          {invitationsQuery.isLoading ? (
            <Card>
              <CardContent className="space-y-3 pt-5">
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </CardContent>
            </Card>
          ) : invitationsQuery.isError ? (
            <ErrorState
              title="Could not load invitations"
              message={getErrorMessage(invitationsQuery.error)}
              onRetry={() => void invitationsQuery.refetch()}
            />
          ) : invitations.length === 0 ? (
            <EmptyState
              icon={Mail}
              title="No pending invitations"
              description="Tenant-level invitations you send will appear here until accepted or revoked."
              action={
                canManage ? (
                  <Button variant="primary" size="sm" onClick={() => setInviteOpen(true)}>
                    <Plus className="h-4 w-4" />
                    Invite member
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Email</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Expires</TableHead>
                  {canManage && <TableHead className="text-right">Actions</TableHead>}
                </TableRow>
              </TableHeader>
              <TableBody>
                {invitations.map((inv) => (
                  <TableRow key={inv.id}>
                    <TableCell className="font-medium text-[var(--text)]">{inv.email}</TableCell>
                    <TableCell>
                      <RoleBadge role={inv.role} />
                    </TableCell>
                    <TableCell className="text-xs text-[var(--text-muted)]">
                      {formatDate(inv.expires_at)}
                    </TableCell>
                    {canManage && (
                      <TableCell className="text-right">
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => setConfirmRevokeInvite(inv)}
                        >
                          Revoke
                        </Button>
                      </TableCell>
                    )}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </TabsContent>
      </Tabs>

      <Dialog
        open={inviteOpen}
        onOpenChange={setInviteOpen}
        title="Invite member"
        description="Invitations grant access at the company level. Assign departments after they join."
      >
        <form onSubmit={(e) => void handleInviteSubmit(e)} className="space-y-4">
          {inviteError && <Alert kind="error">{inviteError}</Alert>}
          <Input
            label="Email address"
            type="email"
            required
            placeholder="teammate@company.com"
            value={inviteEmail}
            onChange={(e) => setInviteEmail(e.target.value)}
          />
          <div>
            <div className="mb-1.5 flex items-center gap-1.5">
              <label
                htmlFor="invite-role-select"
                className="block text-xs font-medium text-[var(--text-muted)]"
              >
                Tenant role
              </label>
              <Tooltip content={REVIEWER_TOOLTIP}>
                <button
                  type="button"
                  aria-label="About reviewer role"
                  className="text-[var(--text-subtle)] hover:text-[var(--text)]"
                >
                  <HelpCircle className="h-3.5 w-3.5" />
                </button>
              </Tooltip>
            </div>
            <Select
              id="invite-role-select"
              value={inviteRole}
              onChange={(e) => setInviteRole(e.target.value as AssignableRole)}
            >
              {INVITE_ROLES.filter((r) => isOwner || r.value !== 'admin').map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </Select>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" size="sm" onClick={() => setInviteOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" size="sm" loading={createInvitation.isPending}>
              Send invitation
            </Button>
          </div>
        </form>
      </Dialog>

      <Dialog
        open={Boolean(assignMember)}
        onOpenChange={(open) => {
          if (!open) setAssignMember(null);
        }}
        title="Assign to department"
        description={
          assignMember
            ? `Assign ${assignMember.email} to a department and set their department role.`
            : undefined
        }
      >
        <form onSubmit={(e) => void handleAssignDepartment(e)} className="space-y-4">
          {assignError && <Alert kind="error">{assignError}</Alert>}
          <Select
            label="Department"
            value={assignDeptId}
            onChange={(e) => setAssignDeptId(e.target.value)}
            required
          >
            {departments.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name} ({d.code})
              </option>
            ))}
          </Select>
          <div>
            <div className="mb-1.5 flex items-center gap-1.5">
              <label
                htmlFor="assign-dept-role-select"
                className="block text-xs font-medium text-[var(--text-muted)]"
              >
                Department role
              </label>
              <Tooltip content={REVIEWER_TOOLTIP}>
                <button
                  type="button"
                  aria-label="About reviewer role"
                  className="text-[var(--text-subtle)] hover:text-[var(--text)]"
                >
                  <HelpCircle className="h-3.5 w-3.5" />
                </button>
              </Tooltip>
            </div>
            <Select
              id="assign-dept-role-select"
              value={assignDeptRole}
              onChange={(e) => setAssignDeptRole(e.target.value as DepartmentRole)}
            >
              {DEPT_ROLES.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </Select>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" size="sm" onClick={() => setAssignMember(null)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" size="sm" loading={assignSaving}>
              Assign
            </Button>
          </div>
        </form>
      </Dialog>

      <ConfirmDialog
        open={Boolean(confirmRemoveMember)}
        onOpenChange={(open) => {
          if (!open) setConfirmRemoveMember(null);
        }}
        title="Remove member"
        description={
          confirmRemoveMember
            ? `Remove ${confirmRemoveMember.email} from this company? They will lose access to all departments.`
            : ''
        }
        confirmLabel="Remove"
        variant="danger"
        loading={removeMember.isPending}
        onConfirm={handleConfirmRemoveMember}
      />

      <ConfirmDialog
        open={Boolean(confirmRevokeInvite)}
        onOpenChange={(open) => {
          if (!open) setConfirmRevokeInvite(null);
        }}
        title="Revoke invitation"
        description={
          confirmRevokeInvite
            ? `Revoke the pending invitation for ${confirmRevokeInvite.email}?`
            : ''
        }
        confirmLabel="Revoke"
        variant="danger"
        loading={revokeInvitation.isPending}
        onConfirm={handleConfirmRevokeInvite}
      />
    </div>
  );
}

export default MembersPage;
