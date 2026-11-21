import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './api';
import type { Invitation, InvitationCreated, InvitationInfo, Member, Role } from './types';

export const membersKey = ['members'] as const;
export const invitationsKey = ['invitations'] as const;

export function useMembers() {
  return useQuery({
    queryKey: membersKey,
    queryFn: () => api<Member[]>('/members'),
  });
}

export function useChangeMemberRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: Role }) =>
      api<Member>(`/members/${userId}`, {
        method: 'PATCH',
        body: { role },
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: membersKey });
      qc.invalidateQueries({ queryKey: ['me'] });
    },
  });
}

export function useRemoveMember() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) =>
      api<void>(`/members/${userId}`, {
        method: 'DELETE',
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: membersKey });
      qc.invalidateQueries({ queryKey: ['me'] });
    },
  });
}

export function useInvitations() {
  return useQuery({
    queryKey: invitationsKey,
    queryFn: () => api<Invitation[]>('/invitations'),
  });
}

export function useCreateInvitation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: { email: string; role: 'admin' | 'builder' | 'reviewer' | 'viewer' }) =>
      api<InvitationCreated>('/invitations', {
        method: 'POST',
        body: payload,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: invitationsKey });
    },
  });
}

export function useRevokeInvitation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (invitationId: string) =>
      api<void>(`/invitations/${invitationId}`, {
        method: 'DELETE',
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: invitationsKey });
    },
  });
}

export function useInvitationInfo(token: string | null) {
  return useQuery({
    queryKey: ['invitation', 'info', token],
    queryFn: () => api<InvitationInfo>(`/invitations/info?token=${encodeURIComponent(token || '')}`),
    enabled: Boolean(token),
    retry: false,
  });
}

