import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import {
  BookOpen,
  Building2,
  ChevronDown,
  Inbox,
  LayoutDashboard,
  LogOut,
  Menu,
  Moon,
  PanelLeft,
  PanelLeftClose,
  Plug,
  Settings,
  Sparkles,
  Sun,
  Users,
  X,
} from 'lucide-react';
import { useEffect, useState, type ComponentType } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { api } from '../lib/api';
import { useActiveTenant, useMe } from '../lib/hooks';
import { fetchReviews } from '../lib/reviewsApi';
import { setTenantId } from '../lib/tenant';
import { useTheme } from '../lib/themeContext';
import TenantSwitcher from './TenantSwitcher';
import { Badge, RoleBadge, Tooltip } from './ui';

interface NavItem {
  label: string;
  to: string;
  icon: ComponentType<{ className?: string }>;
  badge?: number;
  adminOnly?: boolean;
}

function SidebarLogo({ collapsed }: { collapsed: boolean }) {
  return (
    <div
      className={`flex h-14 items-center gap-2.5 border-b border-[var(--sidebar-border)] ${
        collapsed ? 'justify-center px-2' : 'px-4'
      }`}
    >
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius-lg)] bg-gradient-to-br from-[var(--primary)] to-violet-500 text-white shadow-sm shadow-[var(--primary-glow)]">
        <Sparkles className="h-4 w-4" />
      </div>
      {!collapsed && (
        <div className="min-w-0 flex-1">
          <span className="font-display block text-sm font-bold tracking-tight text-[var(--text)]">
            OpsMind
          </span>
          <span className="block text-[10px] font-medium text-[var(--text-subtle)]">
            Automation Studio
          </span>
        </div>
      )}
    </div>
  );
}

function NavItemEl({
  item,
  collapsed,
  activeRoute,
  onClick,
}: {
  item: NavItem;
  collapsed: boolean;
  activeRoute: boolean;
  onClick?: () => void;
}) {
  const Icon = item.icon;

  const link = (
    <NavLink
      to={item.to}
      onClick={onClick}
      className={`group relative flex items-center gap-2.5 rounded-[var(--radius-lg)] py-2 text-[13px] font-medium transition-all duration-150 ${
        collapsed ? 'justify-center px-2' : 'px-3'
      } ${
        activeRoute
          ? 'bg-[var(--primary-soft)] text-[var(--primary)] font-semibold shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--primary)_22%,transparent)]'
          : 'text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)]'
      }`}
    >
      <Icon
        className={`h-4 w-4 shrink-0 transition-transform duration-150 ${
          activeRoute ? 'text-[var(--primary)]' : 'group-hover:scale-105'
        }`}
      />
      {!collapsed && <span className="flex-1 truncate">{item.label}</span>}
      {item.badge !== undefined && item.badge > 0 && (
        <Badge
          variant="warning"
          size="xs"
          className={collapsed ? 'absolute -right-1 -top-1' : ''}
        >
          {item.badge}
        </Badge>
      )}
    </NavLink>
  );

  if (collapsed) {
    return (
      <li>
        <Tooltip content={item.label}>{link}</Tooltip>
      </li>
    );
  }

  return <li>{link}</li>;
}

export default function AppShell() {
  const { data: me } = useMe();
  const { active } = useActiveTenant();
  const { theme, toggleTheme } = useTheme();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();

  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  useEffect(() => {
    if (!mobileMenuOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMobileMenuOpen(false);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [mobileMenuOpen]);

  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    return window.localStorage.getItem('opsmind_sidebar_collapsed') === 'true';
  });

  const toggleSidebar = () => {
    setSidebarCollapsed((prev) => {
      const next = !prev;
      window.localStorage.setItem('opsmind_sidebar_collapsed', String(next));
      return next;
    });
  };

  const { data: openReviews } = useQuery({
    queryKey: ['reviews', 'open', active?.id],
    queryFn: () => fetchReviews('open'),
    enabled: Boolean(active?.id),
    refetchInterval: 15000,
  });

  const pendingCount = openReviews?.length ?? 0;
  const isAdminOrOwner = active?.role === 'owner' || active?.role === 'admin';

  const navItems: NavItem[] = [
    { label: 'Overview', to: '/', icon: LayoutDashboard },
    { label: 'Departments', to: '/departments', icon: Building2 },
    { label: 'Review Inbox', to: '/reviews', icon: Inbox, badge: pendingCount },
    { label: 'Knowledge Base', to: '/knowledge', icon: BookOpen },
    { label: 'Connectors', to: '/connectors', icon: Plug },
    { label: 'Members', to: '/members', icon: Users },
    { label: 'Settings', to: '/settings', icon: Settings, adminOnly: true },
  ];

  const visibleNav = navItems.filter((item) => !item.adminOnly || isAdminOrOwner);

  const logout = useMutation({
    mutationFn: () => api('/auth/logout', { method: 'POST' }),
    onSettled: () => {
      setTenantId(null);
      queryClient.clear();
      navigate('/login', { replace: true });
    },
  });

  const userInitials = me?.full_name
    ? me.full_name
        .split(' ')
        .map((part) => part[0])
        .join('')
        .toUpperCase()
        .slice(0, 2)
    : (me?.email?.[0] ?? 'U').toUpperCase();

  const isRouteActive = (to: string) =>
    to === '/' ? location.pathname === '/' : location.pathname.startsWith(to);

  // Full-bleed studio layout for workflow canvas pages
  const isCanvasRoute =
    /^\/departments\/[^/]+\/workflows\/[^/]+/.test(location.pathname) ||
    /^\/workflows\/[^/]+/.test(location.pathname);

  return (
    <div className="flex min-h-screen w-full overflow-x-clip bg-[var(--bg)] text-[var(--text)]">
      {/* ── Desktop Sidebar ─────────────────────────────────────────── */}
      <aside
        aria-label="Sidebar navigation"
        className={`hidden md:flex shrink-0 flex-col border-r border-[var(--sidebar-border)] bg-[var(--sidebar-bg)] transition-[width] duration-200 sticky top-0 h-screen ${
          sidebarCollapsed ? 'w-16' : 'w-60'
        }`}
      >
        <SidebarLogo collapsed={sidebarCollapsed} />

        <div className="flex flex-1 flex-col justify-between overflow-y-auto px-2.5 py-3">
          <nav aria-label="Primary navigation">
            {!sidebarCollapsed && (
              <div className="px-2.5 pb-2 pt-1 text-[10px] font-bold uppercase tracking-[0.12em] text-[var(--text-subtle)]">
                Platform
              </div>
            )}
            <ul className="space-y-1">
              {visibleNav.map((item) => (
                <NavItemEl
                  key={item.to}
                  item={item}
                  collapsed={sidebarCollapsed}
                  activeRoute={isRouteActive(item.to)}
                />
              ))}
            </ul>
          </nav>

          {!sidebarCollapsed && active && (
            <div className="mt-4 rounded-[var(--radius-xl)] border border-[var(--border)] bg-[var(--surface-muted)]/60 p-3">
              <div className="flex items-center justify-between text-[11px] text-[var(--text-muted)]">
                <span className="font-medium">Workspace</span>
                <span className="rounded bg-[var(--primary-soft)] px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase text-[var(--primary)]">
                  {active.region}
                </span>
              </div>
              <div className="mt-1.5 truncate text-xs font-semibold text-[var(--text)]">
                {active.name}
              </div>
            </div>
          )}

          {sidebarCollapsed && active && (
            <div className="mt-4 flex justify-center">
              <Tooltip content={active.name}>
                <div className="flex h-8 w-8 items-center justify-center rounded-[var(--radius-md)] bg-[var(--primary-soft)] text-xs font-bold text-[var(--primary)] cursor-default">
                  {active.name?.[0]?.toUpperCase() ?? 'W'}
                </div>
              </Tooltip>
            </div>
          )}
        </div>
      </aside>

      {/* ── Right Column (Header + Main) ───────────────────────────── */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 glass border-b border-[var(--border)]">
          <div className="flex h-14 items-center justify-between gap-2 px-3 sm:px-5">
            {/* Left side */}
            <div className="flex min-w-0 flex-1 items-center gap-2">
              {/* Mobile menu toggle */}
              <button
                type="button"
                onClick={() => setMobileMenuOpen((prev) => !prev)}
                aria-label="Toggle navigation menu"
                aria-expanded={mobileMenuOpen}
                className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius-md)] text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)] transition-colors md:hidden cursor-pointer"
              >
                {mobileMenuOpen ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
              </button>

              {/* Desktop sidebar toggle */}
              <Tooltip content={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
                <button
                  type="button"
                  onClick={toggleSidebar}
                  aria-label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
                  className="hidden h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius-md)] text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)] transition-colors md:inline-flex cursor-pointer"
                >
                  {sidebarCollapsed ? (
                    <PanelLeft className="h-4 w-4" />
                  ) : (
                    <PanelLeftClose className="h-4 w-4" />
                  )}
                </button>
              </Tooltip>

              {/* Mobile-only logo */}
              <NavLink to="/" className="flex shrink-0 items-center gap-2 md:hidden">
                <div className="flex h-7 w-7 items-center justify-center rounded-[var(--radius-md)] bg-gradient-to-br from-[var(--primary)] to-violet-500 text-white shadow-xs">
                  <Sparkles className="h-3.5 w-3.5" />
                </div>
                <span className="hidden xs:inline font-display text-sm font-bold tracking-tight text-[var(--text)]">
                  OpsMind
                </span>
              </NavLink>

              <div className="mx-0.5 hidden h-4 w-px bg-[var(--border)] sm:block" />
              <div className="min-w-0 max-w-[160px] sm:max-w-xs">
                <TenantSwitcher />
              </div>
            </div>

            {/* Right side */}
            <div className="flex shrink-0 items-center gap-1.5 sm:gap-2">
              {active && (
                <div className="hidden sm:block">
                  <RoleBadge role={active.role} />
                </div>
              )}

              <Tooltip content={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}>
                <button
                  type="button"
                  onClick={toggleTheme}
                  aria-label="Toggle theme"
                  className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)] transition-colors cursor-pointer"
                >
                  {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
                </button>
              </Tooltip>

              <DropdownMenu.Root>
                <DropdownMenu.Trigger asChild>
                  <button
                    type="button"
                    aria-label="User menu"
                    className="flex max-w-[calc(100vw-1rem)] lg:max-w-[200px] items-center gap-2 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] px-1.5 sm:px-2 py-1 text-xs font-medium text-[var(--text)] hover:bg-[var(--surface-muted)] transition-colors cursor-pointer overflow-hidden"
                  >
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[var(--primary-soft)] text-[11px] font-semibold text-[var(--primary)]">
                      {userInitials}
                    </span>
                    <span className="hidden truncate text-[var(--text-muted)] lg:inline">
                      {me?.email}
                    </span>
                    <ChevronDown className="h-3.5 w-3.5 shrink-0 text-[var(--text-subtle)]" />
                  </button>
                </DropdownMenu.Trigger>

                <DropdownMenu.Portal>
                  <DropdownMenu.Content
                    align="end"
                    sideOffset={6}
                    className="z-50 w-56 rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-1.5 shadow-[var(--shadow-md)]"
                  >
                    <div className="mb-1 border-b border-[var(--border)] px-2.5 py-2">
                      <p className="truncate text-xs font-semibold text-[var(--text)]">
                        {me?.full_name || 'Workspace User'}
                      </p>
                      <p className="truncate text-[11px] text-[var(--text-muted)]">{me?.email}</p>
                    </div>

                    {isAdminOrOwner && (
                      <DropdownMenu.Item
                        onSelect={() => navigate('/settings')}
                        className="flex items-center gap-2 rounded-[var(--radius-md)] px-2.5 py-1.5 text-xs text-[var(--text)] hover:bg-[var(--surface-muted)] focus:bg-[var(--surface-muted)] focus:outline-none cursor-pointer"
                      >
                        <Settings className="h-3.5 w-3.5 text-[var(--text-muted)]" />
                        Settings
                      </DropdownMenu.Item>
                    )}

                    <DropdownMenu.Item
                      onSelect={toggleTheme}
                      className="flex items-center gap-2 rounded-[var(--radius-md)] px-2.5 py-1.5 text-xs text-[var(--text)] hover:bg-[var(--surface-muted)] focus:bg-[var(--surface-muted)] focus:outline-none cursor-pointer"
                    >
                      {theme === 'dark' ? (
                        <Sun className="h-3.5 w-3.5 text-[var(--text-muted)]" />
                      ) : (
                        <Moon className="h-3.5 w-3.5 text-[var(--text-muted)]" />
                      )}
                      {theme === 'dark' ? 'Light mode' : 'Dark mode'}
                    </DropdownMenu.Item>

                    <DropdownMenu.Separator className="my-1 h-px bg-[var(--border)]" />

                    <DropdownMenu.Item
                      onSelect={() => logout.mutate()}
                      className="flex items-center gap-2 rounded-[var(--radius-md)] px-2.5 py-1.5 text-xs text-[var(--danger)] hover:bg-[var(--danger-soft)] focus:bg-[var(--danger-soft)] focus:outline-none cursor-pointer"
                    >
                      <LogOut className="h-3.5 w-3.5" />
                      Log out
                    </DropdownMenu.Item>
                  </DropdownMenu.Content>
                </DropdownMenu.Portal>
              </DropdownMenu.Root>
            </div>
          </div>
        </header>

        {/* ── Mobile Drawer ───────────────────────────────────────────── */}
        {mobileMenuOpen && (
          <div className="fixed inset-0 z-40 md:hidden">
            <div
              className="fixed inset-0 bg-black/60 backdrop-blur-sm"
              onClick={() => setMobileMenuOpen(false)}
            />

            <div
              role="dialog"
              aria-modal="true"
              aria-label="Mobile navigation"
              className="fixed inset-y-0 left-0 z-50 flex w-[min(20rem,88vw)] flex-col justify-between border-r border-[var(--sidebar-border)] bg-[var(--sidebar-bg)] shadow-[var(--shadow-lg)]"
            >
              <div className="flex min-h-0 flex-1 flex-col">
                <div className="flex h-14 items-center justify-between border-b border-[var(--sidebar-border)] px-4">
                  <div className="flex items-center gap-2.5">
                    <div className="flex h-7 w-7 items-center justify-center rounded-[var(--radius-md)] bg-gradient-to-br from-[var(--primary)] to-violet-500 text-white">
                      <Sparkles className="h-3.5 w-3.5" />
                    </div>
                    <span className="font-display text-sm font-bold text-[var(--text)]">OpsMind</span>
                  </div>
                  <button
                    type="button"
                    aria-label="Close navigation menu"
                    onClick={() => setMobileMenuOpen(false)}
                    className="rounded-[var(--radius-md)] p-1.5 text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)] cursor-pointer"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>

                <div className="flex-1 overflow-y-auto px-3 pt-3">
                  <div className="px-2.5 pb-2 text-[10px] font-bold uppercase tracking-[0.12em] text-[var(--text-subtle)]">
                    Platform
                  </div>
                  <nav aria-label="Mobile primary navigation">
                    <ul className="space-y-1">
                      {visibleNav.map((item) => (
                        <NavItemEl
                          key={item.to}
                          item={item}
                          collapsed={false}
                          activeRoute={isRouteActive(item.to)}
                          onClick={() => setMobileMenuOpen(false)}
                        />
                      ))}
                    </ul>
                  </nav>
                </div>
              </div>

              <div className="space-y-3 border-t border-[var(--sidebar-border)] px-4 py-4">
                <div className="flex items-center gap-2.5">
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[var(--primary-soft)] text-xs font-semibold text-[var(--primary)]">
                    {userInitials}
                  </span>
                  <div className="min-w-0">
                    <p className="truncate text-xs font-semibold text-[var(--text)]">
                      {me?.email}
                    </p>
                    {active && <RoleBadge role={active.role} />}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => logout.mutate()}
                  className="flex w-full items-center justify-center gap-2 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface-muted)] px-3 py-2 text-xs font-medium text-[var(--text)] hover:bg-[var(--border)] cursor-pointer"
                >
                  <LogOut className="h-3.5 w-3.5" />
                  Log out
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ── Main content ────────────────────────────────────────────── */}
        <main
          className={
            isCanvasRoute
              ? 'w-full min-w-0 flex-1 flex flex-col'
              : 'mx-auto w-full max-w-7xl min-w-0 flex-1 p-4 sm:p-6 lg:p-8 animate-fade-in'
          }
        >
          <Outlet />
        </main>
      </div>
    </div>
  );
}
