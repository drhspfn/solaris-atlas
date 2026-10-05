import { ArrowUpRight } from 'lucide-react';
import { Link } from 'react-router-dom';

import type { StoryExplanation } from '../../data/explanations';
import { ExplanationSources } from './ExplanationSources';
import { StoryProse } from './StoryProse';

const assertionLabels = {
  confirmed: 'Confirmed',
  observed_anomaly: 'Observed anomaly',
  inferred: 'Inferred',
  unresolved: 'Unresolved',
  suggested: 'Suggested',
  character_speculation: 'Character speculation',
  theory: 'Theory',
};
const worldLabels = {
  before_quest: 'Before this quest',
  during_quest: 'During this quest',
  after_quest: 'After this quest',
  unknown: 'Time unknown',
};

export function ExplanationBlocks({ explanation }: { explanation: StoryExplanation }) {
  const sources = new Map(explanation.nodes.map((node) => [node.id, node]));
  return (
    <div className="explanation-blocks">
      {explanation.blocks.map((block, index) => (
        <article className="explanation-block" key={`${index}-${block.title}`}>
          <h3>{block.title}</h3>
          <StoryProse text={block.text} explanation={explanation} />
          <details className="explanation-evidence">
            <summary>Sources and reasoning</summary>
            {block.scene_importance && (
              <span className="explanation-badge">Scene importance · {block.scene_importance}</span>
            )}
            <ExplanationSources citations={block.citations} nodes={explanation.nodes} />
            {block.assertions?.map((assertion, assertionIndex) => (
              <section
                className="explanation-assertion"
                key={assertionIndex}
                aria-label={assertionLabels[assertion.status]}
              >
                <span className={`assertion-status assertion-status-${assertion.status}`}>
                  {assertionLabels[assertion.status]}
                </span>
                <p className="assertion-text">{assertion.text}</p>
                {assertion.occurrence &&
                  assertion.occurrence !== 'mandatory' &&
                  assertion.occurrence !== 'unknown' && (
                    <p className="assertion-unresolved">
                      {assertion.occurrence.replaceAll('_', ' ')}
                      {assertion.condition ? ` · ${assertion.condition}` : ''}
                    </p>
                  )}
                <dl className="assertion-chronology">
                  <div>
                    <dt>Encountered in quest</dt>
                    <dd>
                      {sources.get(assertion.chronology_in_quest.anchor_node_id) ? (
                        <Link to={sources.get(assertion.chronology_in_quest.anchor_node_id)!.href}>
                          {assertion.chronology_in_quest.label}
                        </Link>
                      ) : (
                        assertion.chronology_in_quest.label
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt>World chronology · {worldLabels[assertion.world_chronology.placement]}</dt>
                    <dd>{assertion.world_chronology.explanation}</dd>
                  </div>
                </dl>
                <div className="assertion-knowledge">
                  <span>Known at this point</span>
                  <p>{assertion.knowledge_state}</p>
                </div>
                <ExplanationSources citations={assertion.citations} nodes={explanation.nodes} />
                {assertion.later_resolution.length ? (
                  <details className="assertion-resolution">
                    <summary>Later revelations · spoilers</summary>
                    {assertion.later_resolution.map((resolution, resolutionIndex) => (
                      <div key={resolutionIndex}>
                        <span>
                          {resolution.status === 'partial'
                            ? 'Partially explained'
                            : resolution.status === 'contradicted'
                              ? 'Contradicted later'
                              : resolution.status === 'suggested'
                                ? 'Possible connection'
                                : 'Explained later'}
                        </span>
                        <p>{resolution.text}</p>
                        <ExplanationSources
                          citations={resolution.citations}
                          nodes={explanation.nodes}
                        />
                      </div>
                    ))}
                  </details>
                ) : (
                  <span className="assertion-unresolved">
                    No later explanation established in the loaded corpus.
                  </span>
                )}
              </section>
            ))}
            {block.related_records?.length || block.related_node_ids?.length ? (
              <div className="explanation-related">
                <span>Related records</span>
                {[
                  ...(block.related_records || []),
                  ...(block.related_node_ids || [])
                    .filter((id) => !block.related_records?.some((record) => record.node_id === id))
                    .map((id) => ({ node_id: id, label: sources.get(id)?.label || '' })),
                ].map((record) => {
                  const node = sources.get(record.node_id);
                  return node ? (
                    <Link key={record.node_id} to={node.href}>
                      {record.label || node.label} <ArrowUpRight size={13} aria-hidden="true" />
                    </Link>
                  ) : null;
                })}
              </div>
            ) : null}
          </details>
        </article>
      ))}
    </div>
  );
}
