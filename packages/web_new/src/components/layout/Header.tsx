'use client';

import {
  CalendarDays,
  ChevronDown,
  Layers,
  LogOut,
  Map,
  Menu,
  Settings,
  Shield,
  Sparkles,
  UserRound,
  X,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { usePathname } from 'next/navigation';
import { Link } from '@/lib/router';
import { useAuth } from '@/auth/AuthProvider';
import { LocaleSwitcher } from './LocaleSwitcher';
import { categories } from '@/data/entities';

export function Header() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [logoutError, setLogoutError] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const header = useRef<HTMLElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  const pathname = usePathname();
  const { user, logout } = useAuth();

  function closeMenus() {
    header.current?.querySelectorAll<HTMLDetailsElement>('details[open]').forEach((item) => {
      item.open = false;
    });
    setMobileOpen(false);
  }
  useEffect(() => {
    closeMenus();
  }, [pathname]);
  useEffect(() => {
    function outside(event: PointerEvent) {
      if (event.target instanceof Node && !header.current?.contains(event.target)) closeMenus();
    }
    function escape(event: KeyboardEvent) {
      if (event.key !== 'Escape') return;
      const open = header.current?.querySelector<HTMLDetailsElement>('details[open]');
      if (open) {
        open.open = false;
        open.querySelector('summary')?.focus({ preventScroll: true });
      } else if (mobileOpen) {
        setMobileOpen(false);
        menuButton.current?.focus({ preventScroll: true });
      }
    }
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [mobileOpen]);
  useEffect(() => {
    const wide = window.matchMedia('(min-width: 900px)');
    function resize() {
      closeMenus();
    }
    wide.addEventListener('change', resize);
    return () => wide.removeEventListener('change', resize);
  }, []);
  const active = (path: string) => pathname === path || pathname.startsWith(`${path}/`);
  async function signOut() {
    closeMenus();
    setSigningOut(true);
    setLogoutError(false);
    try {
      await logout();
    } catch {
      setLogoutError(true);
    } finally {
      setSigningOut(false);
    }
  }

  return (
    <header
      className="site-header"
      ref={header}
      onClick={(event) => {
        if ((event.target as Element).closest('a, .site-locale button')) closeMenus();
      }}
    >
      <div className="site-header-inner">
        <Link to="/" className="brand" aria-label="Solaris Atlas home">
          <span className="brand-mark">
            <Sparkles size={19} />
          </span>
          <span>
            SOLARIS<span className="brand-light"> ATLAS</span>
          </span>
        </Link>
        <nav
          id="primary-navigation"
          aria-label="Main navigation"
          className={`site-navigation${mobileOpen ? ' is-open' : ''}`}
        >
          <details className="site-disclosure" name="site-header-menu">
            <summary className={active('/map') || active('/story-map') ? 'is-active' : ''}>
              Maps <ChevronDown size={14} />
            </summary>
            <div className="site-menu-panel">
              <span className="site-menu-caption">Explore Solaris-3</span>
              <Link to="/map" aria-current={active('/map') ? 'page' : undefined}>
                <Map size={18} />
                <span>
                  Interactive map<small>Places, markers & collectibles</small>
                </span>
              </Link>
              <Link to="/story-map" aria-current={active('/story-map') ? 'page' : undefined}>
                <Layers size={18} />
                <span>
                  Story map<small>Follow connected stories</small>
                </span>
              </Link>
            </div>
          </details>
          <details className="site-disclosure" name="site-header-menu">
            <summary
              className={
                active('/catalog') ||
                ['/characters', '/quests', '/locations', '/items'].some(active)
                  ? 'is-active'
                  : ''
              }
            >
              Archive <ChevronDown size={14} />
            </summary>
            <div className="site-menu-panel">
              <span className="site-menu-caption">People, stories & places</span>
              {categories.map((category) => (
                <Link
                  key={category.key}
                  to={`/catalog/${category.key}`}
                  aria-current={active(`/catalog/${category.key}`) ? 'page' : undefined}
                >
                  <category.icon size={18} />
                  <span>
                    {category.key === 'quest' ? 'Story' : category.label}
                    <small>{category.description}</small>
                  </span>
                </Link>
              ))}
            </div>
          </details>
          <Link
            className="site-nav-link"
            to="/events"
            aria-current={active('/events') ? 'page' : undefined}
          >
            <CalendarDays size={17} />
            Events
          </Link>
        </nav>
        <div className="site-header-actions">
          <LocaleSwitcher />
          {user ? (
            <details className="site-disclosure site-account" name="site-header-menu">
              <summary aria-label={`Account: ${user.nickname}`}>
                <UserRound size={17} />
                <span>{user.nickname}</span>
                <ChevronDown size={14} />
              </summary>
              <div className="site-menu-panel">
                <span className="site-menu-caption">Your atlas</span>
                <Link to="/account">
                  <UserRound size={17} />
                  Account
                </Link>
                <Link to="/settings">
                  <Settings size={17} />
                  Settings
                </Link>
                {user.role === 'admin' && (
                  <Link to="/admin">
                    <Shield size={17} />
                    Admin panel
                  </Link>
                )}
                <button onClick={() => void signOut()} disabled={signingOut}>
                  <LogOut size={17} />
                  {signingOut ? 'Signing out…' : 'Sign out'}
                </button>
              </div>
            </details>
          ) : (
            <>
              <Link
                className="site-icon-button"
                to="/settings"
                aria-label="Settings"
                title="Settings"
              >
                <Settings size={19} />
              </Link>
              <Link className="site-sign-in" to="/login">
                Sign in
              </Link>
            </>
          )}
          <button
            ref={menuButton}
            className="site-menu-toggle site-icon-button"
            onClick={() => setMobileOpen(!mobileOpen)}
            aria-label={mobileOpen ? 'Close navigation' : 'Open navigation'}
            aria-expanded={mobileOpen}
            aria-controls="primary-navigation"
          >
            {mobileOpen ? <X size={22} /> : <Menu size={22} />}
          </button>
        </div>
      </div>
      {logoutError && (
        <p className="site-session-error" role="alert">
          Could not sign out.{' '}
          <button onClick={() => void signOut()} disabled={signingOut}>
            Try again
          </button>
        </p>
      )}
    </header>
  );
}
