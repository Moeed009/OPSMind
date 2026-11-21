import { Link } from 'react-router-dom';
import { AuthLayout } from '../components/ui';

export default function NotFoundPage() {
  return (
    <AuthLayout
      title="Page not found"
      subtitle="The page you are looking for does not exist."
      footer={
        <Link to="/" className="font-medium text-[var(--primary)] hover:underline">
          Go home
        </Link>
      }
    >
      <p className="text-sm text-[var(--text-muted)]">
        Check the address, or head back to the dashboard.
      </p>
    </AuthLayout>
  );
}
