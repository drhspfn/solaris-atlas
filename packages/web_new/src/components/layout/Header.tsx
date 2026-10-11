'use client';

import { ExternalLink, Menu, Settings, Sparkles, X } from 'lucide-react';
import { useState } from 'react';
import { Link, NavLink } from '@/lib/router';
import { useAuth } from '@/auth/AuthProvider';
import { LocaleSwitcher } from './LocaleSwitcher';
import { APP_SETTINGS } from '@/config/settings';
import { categories } from '@/data/entities';

const apiDocsUrl = process.env.NEXT_PUBLIC_API_DOCS_URL ?? APP_SETTINGS.api.defaultDocsUrl;

export function Header() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const { user, logout } = useAuth();

  return (
    <header className="topbar">
      <Link to="/" className="brand">
        <span className="brand-mark">
          <Sparkles size={18} />
        </span>
        <span>
          SOLARIS<span className="brand-light"> ATLAS</span>
        </span>
      </Link>
      <nav
        id="primary-navigation"
        aria-label="Main navigation"
        className={mobileOpen ? 'nav open' : 'nav'}
      >
        <NavLink
          onClick={() => setMobileOpen(false)}
          to="/map"
          className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
        >
          Interactive map
        </NavLink>
        <NavLink
          onClick={() => setMobileOpen(false)}
          to="/story-map"
          className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
        >
          Story map
        </NavLink>
        <NavLink
          onClick={() => setMobileOpen(false)}
          to="/events"
          className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
        >
          Events
        </NavLink>
        {categories.map((c) => (
          <NavLink
            key={c.key}
            onClick={() => setMobileOpen(false)}
            to={`/catalog/${c.key}`}
            className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
          >
            {c.key === 'quest' ? 'Story' : c.label}
          </NavLink>
        ))}
        <NavLink
          onClick={() => setMobileOpen(false)}
          to="/settings"
          className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
          aria-label="Settings"
          title="Settings"
        >
          <Settings size={16} />
        </NavLink>
        <NavLink
          onClick={() => setMobileOpen(false)}
          to="/mcp-docs"
          className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
        >
          MCP
        </NavLink>
        <a className="nav-link nav-about" href={apiDocsUrl} target="_blank" rel="noreferrer">
          API <ExternalLink size={12} />
        </a>
        <a
          className="nav-link nav-about"
          href="https://github.com/drhspfn/solaris-atlas"
          target="_blank"
          rel="noreferrer"
        >
          GitHub <ExternalLink size={12} />
        </a>
      </nav>
      <div className="top-actions">
        {user ? (
          <details className="auth-menu">
            <summary>{user.nickname}</summary>
            <div className="auth-menu-popover">
              <Link to="/account">Account</Link>
              {user.role === 'admin' && <Link to="/admin">Admin panel</Link>}
              <button onClick={() => void logout()}>Sign out</button>
            </div>
          </details>
        ) : (
          <Link className="nav-link auth-nav" to="/login">
            Sign in
          </Link>
        )}
        <LocaleSwitcher />
        <button
          className="mobile-menu"
          onClick={() => setMobileOpen(!mobileOpen)}
          aria-label="Toggle menu"
          aria-expanded={mobileOpen}
          aria-controls="primary-navigation"
        >
          {mobileOpen ? <X /> : <Menu />}
        </button>
      </div>
    </header>
  );
}
