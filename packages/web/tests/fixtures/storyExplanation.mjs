export const storyExplanation = {
  id: 1,
  quest_id: 139000025,
  game_version: '1.0.0',
  locale: 'en',
  title: 'Illustrative chronology fixture',
  blocks: [
    {
      title: 'Arrival remains an open clue',
      text: 'The appearance of drowning and the observed condition are different claims.',
      kind: 'explanation',
      citations: [{ node_id: 1, quote: 'No visible signs of trauma.' }],
      related_node_ids: [],
      related_records: [{ node_id: 1, label: 'Baizhi notes a clear airway and dry clothing' }],
      assertions: [
        {
          text: 'The cause of Rover’s arrival is unknown at this point.',
          status: 'unresolved',
          citations: [{ node_id: 1, quote: 'No visible signs of trauma.' }],
          chronology_in_quest: { order: 0, anchor_node_id: 1, label: 'Opening examination' },
          world_chronology: {
            placement: 'unknown',
            explanation: 'The encounter gives no date for the preceding events.',
          },
          knowledge_state: 'Rover was found; the origin of the arrival is not established.',
          later_resolution: [
            {
              status: 'partial',
              text: 'Illustrative later context, not a published story fact.',
              revealed_in_node_id: 2,
              citations: [{ node_id: 2, quote: 'Later context.' }],
            },
          ],
        },
        {
          text: 'Dry clothing after apparent drowning is an anomaly.',
          status: 'observed_anomaly',
          citations: [{ node_id: 1, quote: 'No visible signs of trauma.' }],
          chronology_in_quest: { order: 0, anchor_node_id: 1, label: 'Opening examination' },
          world_chronology: {
            placement: 'during_quest',
            explanation: 'The condition is observed during the examination.',
          },
          knowledge_state: 'The observations do not establish that Rover actually drowned.',
          later_resolution: [],
        },
      ],
    },
  ],
  nodes: [
    {
      id: 1,
      canonical_key: 'talk_item:example',
      kind: 'dialogue_line',
      label: 'Baizhi’s examination',
      href: '/quests/139000025?game_version=1.0.0&line=example',
    },
    {
      id: 2,
      canonical_key: 'quest:later',
      kind: 'quest',
      label: 'Later context',
      href: '/quests/139000026?game_version=1.0.0',
    },
    {
      id: 3,
      canonical_key: 'character:rover',
      kind: 'character',
      label: 'Rover',
      href: '/characters/rover',
    },
    {
      id: 4,
      canonical_key: 'character:yangyang',
      kind: 'character',
      label: 'Yangyang',
      href: '/characters/yangyang',
    },
  ],
  links: [
    {
      from_node_id: 3,
      to_node_id: 4,
      relation: 'INVOLVES',
      relation_label: 'was found by',
      explanation: 'Illustrative sourced connection.',
      confidence: 0.9,
      citations: [{ node_id: 1, quote: 'No visible signs of trauma.' }],
    },
  ],
  events: [],
  unresolved_questions: ['Where did Rover arrive from?'],
};

// Deliberately illustrative UI data, never imported into the story database.
export const adaptiveExplanation = {
  ...storyExplanation,
  assessment: {
    narrative_weight: 'main_plot',
    hook_priority: 'high',
    reason: 'Introduces the central mystery.',
  },
  narrative_function: 'Establishes an unexplained arrival without resolving its cause.',
  loaded_versions: ['1.0.0', '1.1.0'],
  corpus_changed: true,
  knowledge_boundary: {
    known: ['An examination takes place.'],
    unknown: ['How the arrival happened.'],
    cannot_conclude: ['Apparent drowning proves actual drowning.'],
  },
  hooks: [
    {
      key: 'arrival-origin',
      question: 'What caused the unexplained arrival?',
      priority: 'high',
      revisit_on_new_versions: true,
      revisit_reason: 'Review explicit references to the arrival in new imported sources.',
      citations: storyExplanation.blocks[0].citations,
    },
  ],
  supplements: [
    {
      ...storyExplanation,
      id: 2,
      title: 'Illustrative follow-up',
      revisited_hooks: [
        {
          hook_key: 'arrival-origin',
          priority: 'high',
          status: 'suggested',
          explanation: 'A candidate needs further evidence; this fixture asserts no new game fact.',
          citations: storyExplanation.blocks[0].citations,
        },
      ],
    },
  ],
};
