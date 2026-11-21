import { lazy, Suspense } from 'react';
import { Route, Routes } from 'react-router-dom';
import AppShell from './components/AppShell';
import { ProtectedRoute, PublicOnlyRoute, RequireCompany } from './components/routes';
import { Skeleton } from './components/ui';

const AcceptInvitePage = lazy(() => import('./pages/AcceptInvitePage'));
const ConnectionsPage = lazy(() => import('./pages/ConnectionsPage'));
const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const DepartmentDetailPage = lazy(() => import('./pages/DepartmentDetailPage'));
const DeptWorkflowBuilderPage = lazy(() => import('./pages/DeptWorkflowBuilderPage'));
const DepartmentsPage = lazy(() => import('./pages/DepartmentsPage'));
const ForgotPasswordPage = lazy(() => import('./pages/ForgotPasswordPage'));
const KnowledgeBasePage = lazy(() => import('./pages/KnowledgeBasePage'));
const LoginPage = lazy(() => import('./pages/LoginPage'));
const MembersPage = lazy(() => import('./pages/MembersPage'));
const NotFoundPage = lazy(() => import('./pages/NotFoundPage'));
const OnboardingPage = lazy(() => import('./pages/OnboardingPage'));
const ResetPasswordPage = lazy(() => import('./pages/ResetPasswordPage'));
const ReviewInboxPage = lazy(() => import('./pages/ReviewInboxPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
const SignupPage = lazy(() => import('./pages/SignupPage'));

export function RouteSkeleton() {
  return (
    <div role="status" aria-label="Loading page" className="space-y-4 p-6">
      <Skeleton className="h-8 w-56" />
      <Skeleton className="h-4 w-80" />
      <div className="grid grid-cols-1 gap-4 pt-2 sm:grid-cols-3">
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-28 w-full" />
      </div>
    </div>
  );
}

export default function App() {
  return (
    <Suspense fallback={<RouteSkeleton />}>
      <Routes>
        <Route element={<PublicOnlyRoute />}>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/signup" element={<SignupPage />} />
          <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        </Route>

        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/accept-invite" element={<AcceptInvitePage />} />
        <Route path="/accept-invitation" element={<AcceptInvitePage />} />

        <Route element={<ProtectedRoute />}>
          <Route path="/onboarding" element={<OnboardingPage />} />
          <Route element={<RequireCompany />}>
            <Route element={<AppShell />}>
              <Route index element={<DashboardPage />} />
              <Route path="departments" element={<DepartmentsPage />} />
              <Route path="departments/:id" element={<DepartmentDetailPage />} />
              <Route path="departments/:id/workflows/:wfId" element={<DeptWorkflowBuilderPage />} />
              <Route path="connectors" element={<ConnectionsPage />} />
              <Route path="reviews" element={<ReviewInboxPage />} />
              <Route path="knowledge" element={<KnowledgeBasePage />} />
              <Route path="members" element={<MembersPage />} />
              <Route path="settings" element={<SettingsPage />} />
            </Route>
          </Route>
        </Route>

        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </Suspense>
  );
}
