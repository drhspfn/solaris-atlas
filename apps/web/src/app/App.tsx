import { useState } from "react";
import { Link, NavLink, Route, Routes } from "react-router-dom";
import { ExternalLink, Menu, Sparkles, X } from "lucide-react";
import { categories } from "../data/entities";
import { LocaleSwitcher } from "../components/layout/LocaleSwitcher";
import { Footer } from "../components/layout/Footer";
import { Home } from "../pages/HomePage";
import { Catalog } from "../pages/CatalogPage";
import { Profile } from "../pages/EntityProfilePage";
import { QuestPage } from "../pages/QuestTranscriptPage";
import { SearchPage } from "../pages/SearchPage";
import { NotFound } from "../pages/NotFoundPage";
import { NodeExplorerPage } from "../pages/NodeExplorerPage";
import { AccountPage, GoogleCompletePage, GoogleExistingLinkPage, GoogleSuccessPage, LoginPage, RegisterPage } from "../pages/AuthPages";
import { useAuth } from "../auth/AuthProvider";

const apiDocsUrl = import.meta.env.VITE_API_DOCS_URL ?? "http://localhost:8000/docs";

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
        <nav className={mobileOpen ? "nav open" : "nav"}>
          {categories.map((c) => (
            <NavLink
              key={c.key}
              onClick={() => setMobileOpen(false)}
              to={`/catalog/${c.key}`}
              className={({ isActive }) =>
                isActive ? "nav-link active" : "nav-link"
              }
            >
              {c.key === "quest" ? "Story" : c.label}
            </NavLink>
          ))}
          <a
            className="nav-link nav-about"
            href={apiDocsUrl}
            target="_blank"
            rel="noreferrer"
          >
            API <ExternalLink size={12} />
          </a>
        </nav>
        <div className="top-actions">
          {user ? <details className="auth-menu"><summary>{user.nickname}</summary><div className="auth-menu-popover"><Link to="/account">Account</Link><button onClick={() => void logout()}>Sign out</button></div></details> : <Link className="nav-link auth-nav" to="/login">Sign in</Link>}
          <LocaleSwitcher />
          <button
            className="mobile-menu"
            onClick={() => setMobileOpen(!mobileOpen)}
            aria-label="Toggle menu"
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
            path="/characters/:key"
            element={<Profile kind="character" />}
          />
          <Route path="/items/:key" element={<Profile kind="item" />} />
          <Route path="/locations/:key" element={<Profile kind="location" />} />
          <Route path="/quests/:key" element={<QuestPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/nodes/:key" element={<NodeExplorerPage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />
          <Route path="/auth/google/complete" element={<GoogleCompletePage />} />
          <Route path="/auth/google/link-existing" element={<GoogleExistingLinkPage />} />
          <Route path="/auth/google/success" element={<GoogleSuccessPage />} />
          <Route path="/account" element={<AccountPage />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
      <Footer />
    </div>
  );
}
