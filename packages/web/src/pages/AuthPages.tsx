import { ArrowRight, ShieldCheck, Sparkles } from 'lucide-react';
import { type FormEvent, useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';

import { api, ApiError, apiUrl } from '../api/client';
import { type CurrentUser, useAuth } from '../auth/AuthProvider';

function AuthFrame({
  eyebrow,
  title,
  children,
}: {
  eyebrow: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="auth-page page-container">
      <div className="breadcrumbs">
        <Link to="/">Archive</Link>
        <span>›</span>
        <span>Account</span>
      </div>
      <section className="auth-card">
        <div className="auth-emblem">
          <Sparkles size={25} />
        </div>
        <p className="eyebrow left">{eyebrow}</p>
        <h1>{title}</h1>
        {children}
      </section>
    </div>
  );
}
function GoogleButton({ label = 'Continue with Google' }: { label?: string }) {
  return (
    <a className="auth-google" href={apiUrl('/auth/google')}>
      <span className="google-g">G</span>
      {label}
      <ArrowRight size={16} />
    </a>
  );
}
function ErrorMessage({ message }: { message: string }) {
  return message ? (
    <div className="auth-error" role="alert">
      {message}
    </div>
  ) : null;
}
function friendly(error: unknown) {
  if (!(error instanceof ApiError)) return 'Something went wrong. Please try again.';
  const messages: Record<string, string> = {
    EMAIL_ALREADY_EXISTS: 'An account with this email already exists.',
    NICKNAME_ALREADY_EXISTS: 'That nickname is already in use.',
    INVALID_CREDENTIALS: 'Invalid email or password.',
    INVALID_NICKNAME: 'Use 3–32 English letters, numbers, dots or underscores.',
    RESERVED_NICKNAME: 'That nickname is reserved.',
    WEAK_PASSWORD: 'Password must be between 8 and 128 characters.',
    ACCOUNT_DISABLED: 'This account is disabled.',
    GOOGLE_IDENTITY_ALREADY_LINKED: 'This Google account is connected to another account.',
    PENDING_REGISTRATION_EXPIRED: 'Registration expired. Start the Google sign-in again.',
    PENDING_LINK_EXPIRED: 'Connection expired. Start the Google sign-in again.',
    CSRF_INVALID: 'The form expired. Reload and try again.',
  };
  return messages[error.code ?? ''] ?? error.message;
}

export function LoginPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const { refresh } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const returnTo = (location.state as { from?: string } | null)?.from ?? '/';
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api<CurrentUser>('/auth/login', { method: 'POST', body: { email, password } });
      await refresh();
      navigate(returnTo, { replace: true });
    } catch (e) {
      setError(friendly(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <AuthFrame eyebrow="RETURN TO THE ARCHIVE" title="Welcome back.">
      <p className="auth-copy">Sign in to keep your story research and account connected.</p>
      <form className="auth-form" onSubmit={submit}>
        <label>
          Email
          <input
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <ErrorMessage message={error} />
        <button className="auth-submit" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
          <ArrowRight size={16} />
        </button>
      </form>
      <div className="auth-divider">
        <span>or</span>
      </div>
      <GoogleButton />
      <p className="auth-switch">
        New to Solaris Atlas? <Link to="/register">Create an account</Link>
      </p>
    </AuthFrame>
  );
}

export function RegisterPage() {
  const [email, setEmail] = useState('');
  const [nickname, setNickname] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const { refresh } = useAuth();
  const navigate = useNavigate();
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError('');
    if (password !== confirm) {
      setError('Passwords do not match.');
      return;
    }
    setBusy(true);
    try {
      await api('/auth/register', { method: 'POST', body: { email, nickname, password } });
      await refresh();
      navigate('/', { replace: true });
    } catch (e) {
      setError(friendly(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <AuthFrame eyebrow="CREATE YOUR ARCHIVE ACCOUNT" title="Make it yours.">
      <p className="auth-copy">
        A personal account for exploring Solaris-3 and keeping your place.
      </p>
      <form className="auth-form" onSubmit={submit}>
        <label>
          Email
          <input
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label>
          Nickname
          <input
            autoComplete="username"
            required
            minLength={3}
            maxLength={32}
            value={nickname}
            onChange={(e) => setNickname(e.target.value)}
          />
          <small>3–32 characters. English letters, numbers, dots and underscores.</small>
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            maxLength={128}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <label>
          Confirm password
          <input
            type="password"
            autoComplete="new-password"
            required
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </label>
        <ErrorMessage message={error} />
        <button className="auth-submit" disabled={busy}>
          {busy ? 'Creating account…' : 'Create account'}
          <ArrowRight size={16} />
        </button>
      </form>
      <div className="auth-divider">
        <span>or</span>
      </div>
      <GoogleButton />
      <p className="auth-switch">
        Already registered? <Link to="/login">Sign in</Link>
      </p>
    </AuthFrame>
  );
}

export function GoogleCompletePage() {
  const [pending, setPending] = useState<{ email: string; nickname: string } | null>(null);
  const [nickname, setNickname] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const { refresh } = useAuth();
  const navigate = useNavigate();
  useEffect(() => {
    void api<{ email: string; nickname: string }>('/auth/google/pending')
      .then((value) => {
        setPending(value);
        setNickname(value.nickname);
      })
      .catch((e) => setError(friendly(e)));
  }, []);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (password !== confirm) {
      setError('Passwords do not match.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await api('/auth/google/complete', { method: 'POST', body: { nickname, password } });
      await refresh();
      navigate('/', { replace: true });
    } catch (e) {
      setError(friendly(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <AuthFrame eyebrow="GOOGLE ACCOUNT CONNECTED" title="Complete your account.">
      <p className="auth-copy">
        Your verified Google email is locked to this registration. Choose a nickname and password.
      </p>
      {pending && (
        <form className="auth-form" onSubmit={submit}>
          <label>
            Email
            <input type="email" readOnly value={pending.email} />
          </label>
          <label>
            Nickname
            <input
              required
              minLength={3}
              maxLength={32}
              value={nickname}
              onChange={(e) => setNickname(e.target.value)}
            />
          </label>
          <label>
            Password
            <input
              type="password"
              autoComplete="new-password"
              required
              minLength={8}
              maxLength={128}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <label>
            Confirm password
            <input
              type="password"
              autoComplete="new-password"
              required
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
            />
          </label>
          <ErrorMessage message={error} />
          <button className="auth-submit" disabled={busy}>
            {busy ? 'Creating account…' : 'Create Solaris Atlas account'}
            <ArrowRight size={16} />
          </button>
        </form>
      )}
      {!pending && error && <ErrorMessage message={error} />}
    </AuthFrame>
  );
}

export function GoogleExistingLinkPage() {
  const [pending, setPending] = useState<{ email: string; nickname: string } | null>(null);
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const { refresh } = useAuth();
  const navigate = useNavigate();
  useEffect(() => {
    void api<{ email: string; nickname: string }>('/auth/google/link-existing')
      .then(setPending)
      .catch((e) => setError(friendly(e)));
  }, []);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api('/auth/google/link-existing', { method: 'POST', body: { password } });
      await refresh();
      navigate('/account', { replace: true });
    } catch (e) {
      setError(friendly(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <AuthFrame eyebrow="LINK YOUR GOOGLE ACCOUNT" title="Confirm it’s you.">
      <p className="auth-copy">
        An account already uses this email. Sign in with its password to connect Google safely.
      </p>
      {pending && (
        <form className="auth-form" onSubmit={submit}>
          <label>
            Email
            <input readOnly value={pending.email} />
          </label>
          <label>
            Password
            <input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <ErrorMessage message={error} />
          <button className="auth-submit" disabled={busy}>
            {busy ? 'Connecting…' : 'Verify and connect'}
            <ArrowRight size={16} />
          </button>
        </form>
      )}
      {!pending && error && <ErrorMessage message={error} />}
      <p className="auth-switch">
        <Link to="/login">Return to sign in</Link>
      </p>
    </AuthFrame>
  );
}

export function AccountPage() {
  const { user, loading, refresh, logout } = useAuth();
  const [nickname, setNickname] = useState(user?.nickname ?? '');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const navigate = useNavigate();
  const location = useLocation();
  useEffect(() => {
    if (user) setNickname(user.nickname);
  }, [user]);
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    if (params.has('google')) setMessage('Google account connected.');
    if (params.has('auth_error'))
      setError(
        params.get('auth_error') === 'google_already_linked'
          ? 'This Google account is already connected elsewhere.'
          : 'The Google connection expired. Please try again.',
      );
  }, [location.search]);
  async function save(event: FormEvent) {
    event.preventDefault();
    setError('');
    setMessage('');
    try {
      await api('/account', { method: 'PATCH', body: { nickname } });
      await refresh();
      setMessage('Nickname updated.');
    } catch (e) {
      setError(friendly(e));
    }
  }
  async function unlink() {
    setError('');
    setMessage('');
    try {
      await api('/auth/identities/google', { method: 'DELETE' });
      await refresh();
      setMessage('Google account disconnected.');
    } catch (e) {
      setError(friendly(e));
    }
  }
  if (loading)
    return (
      <div className="page-container">
        <p className="auth-copy">Loading account…</p>
      </div>
    );
  if (!user)
    return (
      <AuthFrame eyebrow="ACCOUNT" title="Sign in required.">
        <Link className="auth-submit" to="/login">
          Sign in
          <ArrowRight size={16} />
        </Link>
      </AuthFrame>
    );
  return (
    <div className="page-container account-page">
      <div className="breadcrumbs">
        <Link to="/">Archive</Link>
        <span>›</span>
        <span>Account</span>
      </div>
      <div className="page-heading">
        <div>
          <p className="eyebrow left">YOUR SOLARIS ATLAS</p>
          <h1>
            Account<span className="heading-period">.</span>
          </h1>
          <p>Manage the credentials connected to your archive account.</p>
        </div>
        <span className="account-role">
          <ShieldCheck size={14} />
          {user.role}
        </span>
      </div>
      <div className="account-grid">
        <section className="content-panel">
          <h2>Profile</h2>
          <p className="account-email">{user.email}</p>
          <form className="auth-form" onSubmit={save}>
            <label>
              Nickname
              <input
                minLength={3}
                maxLength={32}
                required
                value={nickname}
                onChange={(e) => setNickname(e.target.value)}
              />
            </label>
            <button className="auth-submit">
              Save nickname
              <ArrowRight size={16} />
            </button>
          </form>
        </section>
        <section className="content-panel">
          <h2>Connected accounts</h2>
          <div className="connected-row">
            <span className="google-g">G</span>
            <div>
              <strong>Google</strong>
              <small>{user.google_connected ? 'Connected' : 'Not connected'}</small>
            </div>
            {user.google_connected ? (
              <button className="auth-secondary" onClick={() => void unlink()}>
                Disconnect
              </button>
            ) : (
              <a className="auth-secondary" href={apiUrl('/auth/google?mode=link')}>
                Connect
              </a>
            )}
          </div>
          <p className="auth-copy">
            Your Google account is used only for sign-in. Provider access tokens are not retained.
          </p>
        </section>
      </div>
      <ErrorMessage message={error} />
      {message && <div className="auth-success">{message}</div>}
      <button
        className="auth-logout"
        onClick={async () => {
          await logout();
          navigate('/', { replace: true });
        }}
      >
        Sign out
      </button>
    </div>
  );
}

export function GoogleSuccessPage() {
  const { refresh } = useAuth();
  const navigate = useNavigate();
  useEffect(() => {
    void refresh().then(() => navigate('/', { replace: true }));
  }, [refresh, navigate]);
  return (
    <div className="page-container">
      <p className="auth-copy">Finishing sign in…</p>
    </div>
  );
}
