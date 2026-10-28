import { ArrowUpRight, Mail, Sparkles } from 'lucide-react';
import { Link } from '@/lib/router';
import { APP_SETTINGS } from '@/config/settings';
import { CookieSettingsButton } from './CookieConsent';

const apiDocsUrl = process.env.NEXT_PUBLIC_API_DOCS_URL ?? APP_SETTINGS.api.defaultDocsUrl;

export function Footer() {
  return (
    <footer className="site-footer">
      <div className="site-footer-main">
        <div className="site-footer-about">
          <Link to="/" className="brand">
            <span className="brand-mark">
              <Sparkles size={17} />
            </span>
            <span>
              SOLARIS<span className="brand-light"> ATLAS</span>
            </span>
          </Link>
          <p>
            A connected story archive for Solaris-3.
            <br />
            Made for curious Rovers.
          </p>
          <a className="site-footer-contact" href="mailto:admin@solarisatlas.fun">
            <Mail size={15} />
            admin@solarisatlas.fun
          </a>
        </div>
        <nav aria-label="Footer archive links">
          <h2>Explore</h2>
          <Link to="/map">Interactive map</Link>
          <Link to="/story-map">Story map</Link>
          <Link to="/events">Events</Link>
          <Link to="/catalog/quest">Story archive</Link>
          <Link to="/cutscenes">Cutscenes</Link>
        </nav>
        <nav aria-label="Project resources">
          <h2>Project</h2>
          <Link to="/mcp-docs">MCP connection</Link>
          <a href={apiDocsUrl} target="_blank" rel="noreferrer">
            API documentation <ArrowUpRight size={13} />
          </a>
          <a href="https://github.com/drhspfn/solaris-atlas" target="_blank" rel="noreferrer">
            GitHub <ArrowUpRight size={13} />
          </a>
          <Link to="/legal">Legal & privacy</Link>
        </nav>
      </div>
      <div className="site-footer-bottom">
        <p>
          Unofficial fan project. Not affiliated with or endorsed by KURO GAMES.
          <br />
          Wuthering Waves and its game content belong to their respective rights holders.
        </p>
        <div>
          <Link to="/legal#privacy">Privacy</Link>
          <CookieSettingsButton />
          <Link to="/legal#copyright">Copyright requests</Link>
          <span>solarisatlas.fun</span>
        </div>
      </div>
    </footer>
  );
}
