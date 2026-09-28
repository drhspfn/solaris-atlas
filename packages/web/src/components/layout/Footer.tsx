import { Link } from "react-router-dom";
import { Sparkles } from "lucide-react";

export function Footer() {
  return (
    <footer className="footer">
      <Link to="/" className="brand footer-brand">
        <span className="brand-mark">
          <Sparkles size={15} />
        </span>
        <span>
          SOLARIS<span className="brand-light"> ATLAS</span>
        </span>
      </Link>
      <div className="footer-copy">
        <span>A connected story archive for Solaris-3.</span>
        <span>Unofficial Wuthering Waves companion archive.</span>
      </div>
      <a className="footer-domain" href="https://solarisatlas.fun">solarisatlas.fun</a>
    </footer>
  );
}
