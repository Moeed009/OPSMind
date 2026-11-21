# OpsMind Project Documentation

This directory contains professional, phase‑by‑phase documentation for the OpsMind SaaS platform.

- `SRS/` – Updated Software Requirements Specification.
- `architecture/` – System, Database, and Security architecture diagrams and descriptions.
- `phases/` – Detailed markdown files for each implementation phase (PHASE_00_FOUNDATION.md … PHASE_11_DEPLOYMENT_READINESS.md).
- `testing/` – Test strategy, results, security and performance reports.
- `reports/daily/` – Daily status reports generated after every work session.
- `decisions/` – Architecture decision records.
- `IMPLEMENTATION_STATUS.md` – Overall progress tracker.

All files reference the source code in the repository and include real test outputs where available.


## Department Role Model, Permission Matrix & Connection Rules

### 1. Per-Department Role Model (`FR-1.3`, `FR-1.4`)
- `department_members.role` (`DepartmentRole`: `admin`, `builder`, `reviewer`, `viewer`, nullable) allows per-department role overrides without altering the user's company-wide `Membership.role`.
- **Effective Role Resolver (`get_effective_department_role` in `backend/app/services/department.py`):**
  - Company **Owner** / **Admin** always retain full access across all departments.
  - Other members must be assigned to the department (`department_members`); their effective role is `department_members.role` if set, otherwise their tenant role.
  - A user can hold different effective roles across different departments.
- **Last-Admin & Duplicate Guards:**
  - Duplicate assignment to the same department returns `409 Conflict`.
  - Demoting or removing the last admin of a department returns `409 Conflict`.
  - Every assign, role change, and remove writes an `AuditLog` entry (`department_member.assigned`, `department_member.role_changed`, `department_member.removed`).

### 2. Department Permission Matrix
| Action | Tenant Owner / Admin | Dept Admin | Dept Builder | Dept Reviewer | Dept Viewer | Unassigned |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| Create / Archive Department | Yes | No | No | No | No | No |
| Assign / Remove Dept Members | Yes | No | No | No | No | No |
| Update Dept Member Roles (`PATCH`) | Yes (all) | Yes (own dept) | No | No | No | No |
| Connect / Disconnect / Poll Dept Connections | Yes (all) | Yes (own dept) | No | No | No | No |
| Read Dept Connections & Dashboard | Yes (all) | Yes (own dept) | Yes (own dept) | Yes (own dept) | Yes (own dept) | No |
| Write / Publish / Pause / Resume Dept Workflows | Yes (all) | Yes (own dept) | Yes (own dept) | No | No | No |
| Tenant-Wide Settings / Billing / Members | Yes | No | No | No | No | No |

### 3. Connection Ownership & Lifecycle Rules (`FR-6.5`, `FR-6.6`, `FR-3.2`)
- **Required Department Scope:** New connections require `department_id` (`422` if missing). API responses (`ConnectionView`, `DepartmentConnectionItem`) never include credentials or tokens.
- **Cross-Department Uniqueness (`UNIQUE(tenant_id, provider, external_account_id)`):**
  - Connecting an active external account already bound to another department returns `409 Conflict` (`AccountAlreadyBound`) without modifying `department_id`.
  - Reconnecting inside the same department updates credentials in place.
  - Disconnected (`revoked`) accounts can be reconnected to any department.
- **Workflow Pause on Disconnect / Unhealthy (`pause_workflows_for_connection`):**
  - Revoking, deleting, or polling a connection into `NEEDS_REAUTH`/`ERROR`/`REVOKED` automatically sets all referencing `PUBLISHED` `WorkflowVersion`s (via `trigger_connection_id` or node `config.connection_id`) to `PAUSED` with `pause_reason` stored and audited.
  - Reconnecting does **not** auto-resume workflows; a department admin or tenant owner/admin must resume manually via `POST /workflows/{id}/resume` or `publish`.
- **Cross-Department Validation:** Referenced connections are validated to match the workflow's `department_id` at both save/publish (`403`) and runtime (`RunStatus.FAILED`), with `AuditLog` entries (`workflow.cross_department_connection_rejected`).
