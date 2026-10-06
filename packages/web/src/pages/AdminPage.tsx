import '../styles/story-agent.css';

import { Bell, BookOpen, ChevronRight, Coins, Database, Film, ShieldCheck } from 'lucide-react';
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom';

import { useAuth } from '../auth/AuthProvider';
import { PageLoader } from '../components/ui/Feedback';

export function AdminLayout() {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <PageLoader />;
  if (!user || user.role !== 'admin')
    return (
      <div className="page-container agent-access">
        <ShieldCheck size={28} aria-hidden="true" />
        <h1>Administrator access required</h1>
        <p>
          {user
            ? 'Your account cannot manage the archive.'
            : 'Sign in with an administrator account to open this panel.'}
        </p>
        <Link
          className="agent-button"
          to={user ? '/account' : '/login'}
          state={{ from: location.pathname + location.search }}
        >
          {user ? 'Open account' : 'Sign in'}
        </Link>
      </div>
    );
  return (
    <div className="page-container admin-page">
      <div className="breadcrumbs">
        <Link to="/">Archive</Link>
        <ChevronRight size={13} />
        <span>Administration</span>
      </div>
      <header className="page-heading">
        <div>
          <span className="eyebrow left">ARCHIVE OPERATIONS</span>
          <h1>
            Admin panel<span className="heading-period">.</span>
          </h1>
        </div>
      </header>
      <div className="admin-layout">
        <aside className="admin-sidebar">
          <nav aria-label="Administration">
            <span>Content</span>
            <NavLink to="/admin/story-agent">
              <BookOpen size={16} />
              Story agent
            </NavLink>
            <NavLink to="/admin/cutscene-analysis">
              <Film size={16} />
              Cutscene analysis
            </NavLink>
            <NavLink to="/admin/data-operations">
              <Database size={16} />
              Game data
            </NavLink>
            <span>Operations</span>
            <NavLink to="/admin/alerts">
              <Bell size={16} />
              Alerts
            </NavLink>
            <NavLink to="/admin/usage">
              <Coins size={16} />
              Usage
            </NavLink>
          </nav>
        </aside>
        <div className="admin-content">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
