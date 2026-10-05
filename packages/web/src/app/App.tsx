import { ExternalLink, Menu, Settings, Sparkles, X } from 'lucide-react';
import { lazy, Suspense, useState } from 'react';
import { Link, Navigate, NavLink, Route, Routes } from 'react-router-dom';

import { useAuth } from '../auth/AuthProvider';
import { Footer } from '../components/layout/Footer';
import { LocaleSwitcher } from '../components/layout/LocaleSwitcher';
import { PageLoader } from '../components/ui/Feedback';
import { APP_SETTINGS } from '../config/settings';
import { categories } from '../data/entities';
import { AdminLayout } from '../pages/AdminPage';
import {
  AccountPage,
  GoogleCompletePage,
  GoogleExistingLinkPage,
  GoogleSuccessPage,
  LoginPage,
  RegisterPage,
} from '../pages/AuthPages';
import { Catalog } from '../pages/CatalogPage';
import { Profile } from '../pages/EntityProfilePage';
import { Home } from '../pages/HomePage';
import { NodeExplorerPage } from '../pages/NodeExplorerPage';
import { NotFound } from '../pages/NotFoundPage';
import { QuestPage } from '../pages/QuestTranscriptPage';
import { SearchPage } from '../pages/SearchPage';
import { SettingsPage } from '../pages/SettingsPage';
import { StoryConnectionPage } from '../pages/StoryConnectionPage';
import { StoryEventPage } from '../pages/StoryEventPage';
import { StoryMapPage } from '../pages/StoryMapPage';

const WorldMapPage = lazy(() =>
  import('../pages/WorldMapPage').then((module) => ({ default: module.WorldMapPage })),
);
const StoryAgentPage = lazy(() =>
  import('../pages/StoryAgentPage').then((module) => ({ default: module.StoryAgentPage })),
);
const AgentOperationsPage = lazy(() =>
  import('../pages/AgentOperationsPage').then((module) => ({
    default: module.AgentOperationsPage,
  })),
);

const apiDocsUrl = import.meta.env.VITE_API_DOCS_URL ?? APP_SETTINGS.api.defaultDocsUrl;

export function App() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const { user, logout } = useAuth();
  return (
    <div className="app-shell">
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
          >
            <Settings size={13} /> Settings
          </NavLink>
          <a className="nav-link nav-about" href={apiDocsUrl} target="_blank" rel="noreferrer">
            API <ExternalLink size={12} />
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
      <main>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/catalog/:category" element={<Catalog />} />
          <Route
            path="/map"
            element={
              <Suspense fallback={<PageLoader />}>
                <WorldMapPage />
              </Suspense>
            }
          />
          <Route path="/story-map" element={<StoryMapPage />} />
          <Route path="/characters/:key" element={<Profile kind="character" />} />
          <Route path="/items/:key" element={<Profile kind="item" />} />
          <Route path="/locations/:key" element={<Profile kind="location" />} />
          <Route path="/quests/:key" element={<QuestPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/story-analysis/events/:key" element={<StoryEventPage />} />
          <Route
            path="/story-analysis/connections/:documentId/:index"
            element={<StoryConnectionPage />}
          />
          <Route path="/nodes/:key" element={<NodeExplorerPage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />
          <Route path="/auth/google/complete" element={<GoogleCompletePage />} />
          <Route path="/auth/google/link-existing" element={<GoogleExistingLinkPage />} />
          <Route path="/auth/google/success" element={<GoogleSuccessPage />} />
          <Route path="/account" element={<AccountPage />} />
          <Route path="/admin" element={<AdminLayout />}>
            <Route index element={<Navigate to="story-agent" replace />} />
            <Route
              path="story-agent"
              element={
                <Suspense fallback={<PageLoader />}>
                  <StoryAgentPage />
                </Suspense>
              }
            />
            <Route
              path="alerts"
              element={
                <Suspense fallback={<PageLoader />}>
                  <AgentOperationsPage key="alerts" kind="alerts" />
                </Suspense>
              }
            />
            <Route
              path="usage"
              element={
                <Suspense fallback={<PageLoader />}>
                  <AgentOperationsPage key="usage" kind="usage" />
                </Suspense>
              }
            />
          </Route>
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
      <Footer />
    </div>
  );
}
