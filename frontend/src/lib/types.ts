export type Role = 'owner' | 'admin' | 'builder' | 'reviewer' | 'viewer';
export type DepartmentRole = 'admin' | 'builder' | 'reviewer' | 'viewer';
export type Region = 'eu' | 'us';
export type Plan = 'free' | 'starter' | 'pro';

export interface Tenant {
  id: string;
  name: string;
  slug: string;
  region: Region;
  plan: Plan;
  role: Role;
}

export interface Me {
  id: string;
  email: string | null;
  full_name: string | null;
  tenants: Tenant[];
}

export interface SignupResult {
  message: string;
  confirmation_required: boolean;
  user: { id: string; email: string; full_name: string | null };
}

export interface MessageResult {
  message: string;
}

export const REGION_LABELS: Record<Region, string> = {
  eu: 'Europe (EU)',
  us: 'United States (US)',
};

export const MAX_COMPANIES = 1;
export const MIN_PASSWORD_LENGTH = 12;

export interface MemberDepartmentSummary {
  id: string;
  name: string;
  code: string;
  status: string;
  role?: DepartmentRole | null;
  effective_role?: Role | null;
}

export interface Member {
  user_id: string;
  email: string;
  full_name: string | null;
  role: Role;
  joined_at: string;
  departments?: MemberDepartmentSummary[];
  assignment_status?: 'assigned' | 'unassigned';
}

export interface Invitation {
  id: string;
  email: string;
  role: Role;
  expires_at: string;
  created_at: string;
  expired: boolean;
}

export interface InvitationCreated extends Invitation {
  token: string;
  email_sent?: boolean;
  email_status?: string | null;
}

export interface InvitationInfo {
  id: string;
  email: string;
  role: Role;
  tenant_name: string;
  expires_at: string;
  expired: boolean;
  is_valid: boolean;
}

