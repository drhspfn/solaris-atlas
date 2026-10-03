import { ArrowUpRight, BookOpen } from 'lucide-react';
import { Link } from 'react-router-dom';

import type { SourceCitation, StoryExplanation } from '../../data/explanations';

export function ExplanationSources({
  citations,
  nodes,
}: {
  citations: SourceCitation[];
  nodes: StoryExplanation['nodes'];
}) {
  return (
    <div className="explanation-sources">
      {citations.map((citation, index) => {
        const source = nodes.find((node) => node.id === citation.node_id);
        const href = citation.href || source?.href;
        return (
          <details key={`${citation.node_id}-${index}`}>
            <summary>
              <BookOpen size={13} aria-hidden="true" /> Source {index + 1}
            </summary>
            <blockquote>{citation.quote}</blockquote>
            {href && (
              <Link to={href}>
                Read source <ArrowUpRight size={13} aria-hidden="true" />
              </Link>
            )}
          </details>
        );
      })}
    </div>
  );
}
