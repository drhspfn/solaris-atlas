import type { StoryExplanation } from '../../data/explanations';
import { ExplanationBlocks } from './ExplanationBlocks';
import { ExplanationSources } from './ExplanationSources';

export function ExplanationContext({ explanation }: { explanation: StoryExplanation }) {
  const boundary = explanation.knowledge_boundary;
  return (
    <>
      {explanation.assessment && (
        <section className="explanation-context" aria-label="Narrative role">
          <span className="explanation-badge">
            {explanation.assessment.narrative_weight.replaceAll('_', ' ')}
          </span>
          <p>{explanation.narrative_function || explanation.assessment.reason}</p>
        </section>
      )}
      {explanation.loaded_versions?.length ? (
        <details className="explanation-context">
          <summary>
            Sources considered · {explanation.loaded_versions.length} imported versions
          </summary>
          <p>{explanation.loaded_versions.join(' · ')}</p>
          <p>Versions identify source snapshots, not the order of story events.</p>
        </details>
      ) : null}
      {explanation.corpus_changed && (
        <p className="explanation-status">
          More sources have been imported since this analysis. Its cited passages remain valid; new
          findings appear separately below.
        </p>
      )}
      {boundary && (
        <details className="explanation-context">
          <summary>What this quest establishes</summary>
          {(
            [
              ['Known', boundary.known],
              ['Still unknown', boundary.unknown],
              ['Cannot conclude', boundary.cannot_conclude],
            ] as const
          ).map(([label, items]) => (
            <section key={label}>
              <h4>{label}</h4>
              <ul>
                {items.map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
            </section>
          ))}
        </details>
      )}
    </>
  );
}

export function ExplanationFollowUps({ explanation }: { explanation: StoryExplanation }) {
  return (
    <>
      {explanation.hooks?.length ? (
        <details className="explanation-context">
          <summary>Open threads · {explanation.hooks.length}</summary>
          <p>Unresolved in the sources available to this analysis.</p>
          {explanation.hooks.map((hook) => (
            <article className="explanation-edge" key={hook.key}>
              <span className="explanation-badge">{hook.priority} priority</span>
              <h4>{hook.question}</h4>
              <p>{hook.revisit_reason}</p>
              {hook.revisit_on_new_versions && (
                <span className="assertion-unresolved">
                  Flagged for review when new sources arrive
                </span>
              )}
              <ExplanationSources citations={hook.citations} nodes={explanation.nodes} />
            </article>
          ))}
        </details>
      ) : null}
      {explanation.supplements?.map((supplement) => (
        <details className="explanation-context assertion-resolution" key={supplement.id}>
          <summary>New context · spoilers · {supplement.title}</summary>
          <p>This supplement leaves the original analysis and what was known then unchanged.</p>
          {supplement.revisited_hooks?.map((review) => (
            <article className="explanation-edge" key={review.hook_key}>
              <h4>
                {explanation.hooks?.find((hook) => hook.key === review.hook_key)?.question ||
                  'Reviewed thread'}
              </h4>
              <span className="explanation-badge">{review.status.replaceAll('_', ' ')}</span>
              <span className="explanation-badge">{review.priority} priority after review</span>
              <p>{review.explanation}</p>
              <ExplanationSources citations={review.citations} nodes={supplement.nodes} />
            </article>
          ))}
          <ExplanationBlocks explanation={supplement} />
        </details>
      ))}
    </>
  );
}
