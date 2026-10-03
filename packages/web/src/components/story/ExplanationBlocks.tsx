import { ArrowUpRight, BookOpen } from 'lucide-react';
import { Link } from 'react-router-dom';

import type { StoryExplanation } from '../../data/explanations';

export function ExplanationBlocks({ explanation }: { explanation: StoryExplanation }) {
  const sources = new Map(explanation.nodes.map((node) => [node.id, node]));
  return (
    <div className="explanation-blocks">
      {explanation.blocks.map((block, index) => (
        <article className="explanation-block" key={`${index}-${block.title}`}>
          <h3>{block.title}</h3>
          <p>{block.text}</p>
          <div className="explanation-sources">
            {block.citations.map((citation, citationIndex) => {
              const source = sources.get(citation.node_id);
              return (
                <details key={`${citation.node_id}-${citationIndex}`}>
                  <summary>
                    <BookOpen size={13} aria-hidden="true" /> Source {citationIndex + 1}
                  </summary>
                  <blockquote>{citation.quote}</blockquote>
                  {source && (
                    <Link to={source.href}>
                      Read source <ArrowUpRight size={13} aria-hidden="true" />
                    </Link>
                  )}
                </details>
              );
            })}
          </div>
          {block.related_node_ids?.length > 0 && (
            <div className="explanation-related">
              <span>Related records</span>
              {block.related_node_ids.map((id) => {
                const node = sources.get(id);
                return node ? (
                  <Link key={id} to={node.href}>
                    {node.label} <ArrowUpRight size={13} aria-hidden="true" />
                  </Link>
                ) : null;
              })}
            </div>
          )}
        </article>
      ))}
    </div>
  );
}
