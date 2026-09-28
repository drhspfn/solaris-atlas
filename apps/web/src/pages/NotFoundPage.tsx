import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";

export function NotFound() {
  return (
    <div className="page-container not-found">
      <span className="eyebrow left">404 / LOST IN THE STATIC</span>
      <h1>This path has faded.</h1>
      <Link className="text-link" to="/">
        Return to the archive <ArrowRight size={16} />
      </Link>
    </div>
  );
}
