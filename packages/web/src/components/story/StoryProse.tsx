import Markdown from 'react-markdown';
import { Link } from 'react-router-dom';

import type { StoryExplanation } from '../../data/explanations';
import { ConnectionLink } from './ConnectionLink';

export function StoryProse({ text, explanation }: { text: string; explanation: StoryExplanation }) {
  return (
    <div className="story-prose">
      <Markdown
        skipHtml
        allowedElements={[
          'p',
          'h1',
          'h2',
          'h3',
          'h4',
          'ul',
          'ol',
          'li',
          'strong',
          'em',
          'a',
          'blockquote',
          'br',
          'code',
        ]}
        unwrapDisallowed
        urlTransform={(url) => (/^(connection|record|event):\d+$/.test(url) ? url : '')}
        components={{
          h1: ({ children }) => <h4>{children}</h4>,
          h2: ({ children }) => <h4>{children}</h4>,
          h3: ({ children }) => <h4>{children}</h4>,
          a: ({ href, children }) => {
            const [kind, value] = (href || '').split(':');
            const index = Number(value);
            if (kind === 'connection') {
              const link = explanation.links[index];
              return link ? (
                <ConnectionLink link={link}>{children}</ConnectionLink>
              ) : (
                <span>{children}</span>
              );
            }
            if (kind === 'record') {
              const node = explanation.nodes.find((entry) => entry.id === index);
              return node ? <Link to={node.href}>{children}</Link> : <span>{children}</span>;
            }
            if (kind === 'event') {
              const event = explanation.events[index];
              return event ? (
                <Link
                  to={`/story-analysis/events/${event.node_id}?${new URLSearchParams({ game_version: explanation.game_version, locale: explanation.locale || 'en' })}`}
                >
                  {children}
                </Link>
              ) : (
                <span>{children}</span>
              );
            }
            return <span>{children}</span>;
          },
        }}
      >
        {text}
      </Markdown>
    </div>
  );
}
