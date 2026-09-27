-- Read-only investigation queries for game release 3.6.0.
-- Run with: docker compose exec -T postgres psql -U wuwa -d wuwa_story -f - < queries/denia.sql

-- 1. Deterministic character -> speaker crosswalk and its source evidence.
SELECT character_node.canonical_key AS character,
       speaker_node.canonical_key AS speaker,
       speaker.game_speaker_id,
       link.resolution_type,
       link.metadata ->> 'evidence' AS evidence,
       link.metadata ->> 'source_file' AS source_file,
       link.metadata ->> 'source_raw_path' AS source_raw_path
FROM core.speaker_entity_link AS link
JOIN graph.node AS character_node ON character_node.id = link.entity_node_id
JOIN graph.node AS speaker_node ON speaker_node.id = link.speaker_node_id
JOIN core.speaker AS speaker ON speaker.node_id = link.speaker_node_id
WHERE character_node.canonical_key = 'character:1211'
ORDER BY speaker.game_speaker_id;

-- 2. Quest IDs reachable by the explicit chain:
-- quest -> quest_node -> flow_state -> action -> talk_item -> Denia speaker.
WITH denia_dialogue AS (
    SELECT dialogue.node_id
    FROM core.speaker_entity_link AS link
    JOIN core.dialogue_line AS dialogue ON dialogue.speaker_node_id = link.speaker_node_id
    WHERE link.entity_node_id = 114576
), dialogue_quest AS (
    SELECT DISTINCT quest.game_quest_id, dialogue.node_id AS dialogue_id
    FROM core.quest AS quest
    JOIN graph.edge AS has_node ON has_node.from_node_id = quest.node_id
    JOIN ontology.relation_type AS has_node_type
      ON has_node_type.id = has_node.relation_type_id AND has_node_type.key = 'has_quest_node'
    JOIN graph.edge AS references_state ON references_state.from_node_id = has_node.to_node_id
    JOIN ontology.relation_type AS state_type
      ON state_type.id = references_state.relation_type_id
     AND state_type.key = 'references_flow_state'
    JOIN graph.edge AS contains_action ON contains_action.from_node_id = references_state.to_node_id
    JOIN ontology.relation_type AS action_type
      ON action_type.id = contains_action.relation_type_id AND action_type.key = 'contains_action'
    JOIN graph.edge AS presents_talk ON presents_talk.from_node_id = contains_action.to_node_id
    JOIN ontology.relation_type AS talk_type
      ON talk_type.id = presents_talk.relation_type_id AND talk_type.key = 'presents_talk_item'
    JOIN denia_dialogue AS dialogue ON dialogue.node_id = presents_talk.to_node_id
)
SELECT quest.game_quest_id,
       name.content AS quest_name,
       count(DISTINCT dialogue_quest.dialogue_id) AS denia_dialogue_lines
FROM dialogue_quest
JOIN core.quest AS quest ON quest.game_quest_id = dialogue_quest.game_quest_id
LEFT JOIN i18n.localization_value AS name
  ON name.key_id = quest.name_key_id AND name.release_id = 1 AND name.locale_id = 1
GROUP BY quest.game_quest_id, name.content
ORDER BY denia_dialogue_lines DESC;

-- 3. Ordered scenes and Denia dialogue count in the golden quest 119000000.
WITH quest_nodes AS (
    SELECT edge.to_node_id AS id
    FROM graph.edge AS edge
    JOIN ontology.relation_type AS relation ON relation.id = edge.relation_type_id
    WHERE relation.key = 'has_quest_node'
      AND edge.from_node_id = (SELECT node_id FROM core.quest WHERE game_quest_id = 119000000)
), quest_states AS (
    SELECT DISTINCT edge.to_node_id AS id
    FROM quest_nodes AS qnode
    JOIN graph.edge AS edge ON edge.from_node_id = qnode.id
    JOIN ontology.relation_type AS relation
      ON relation.id = edge.relation_type_id AND relation.key = 'references_flow_state'
), scene_dialogue AS (
    SELECT DISTINCT scene.node_id AS scene_id, dialogue.node_id AS dialogue_id
    FROM story.scene AS scene
    JOIN graph.edge AS scene_state ON scene_state.from_node_id = scene.node_id
    JOIN ontology.relation_type AS scene_state_type
      ON scene_state_type.id = scene_state.relation_type_id
     AND scene_state_type.key = 'references_flow_state'
    JOIN quest_states AS state ON state.id = scene_state.to_node_id
    JOIN graph.edge AS contains_action ON contains_action.from_node_id = state.id
    JOIN ontology.relation_type AS action_type
      ON action_type.id = contains_action.relation_type_id AND action_type.key = 'contains_action'
    JOIN graph.edge AS presents_talk ON presents_talk.from_node_id = contains_action.to_node_id
    JOIN ontology.relation_type AS talk_type
      ON talk_type.id = presents_talk.relation_type_id AND talk_type.key = 'presents_talk_item'
    JOIN core.dialogue_line AS dialogue ON dialogue.node_id = presents_talk.to_node_id
    JOIN core.speaker_entity_link AS link
      ON link.speaker_node_id = dialogue.speaker_node_id AND link.entity_node_id = 114576
)
SELECT node.canonical_key AS scene,
       scene.authored_order,
       count(DISTINCT scene_dialogue.dialogue_id) AS denia_lines
FROM scene_dialogue
JOIN story.scene AS scene ON scene.node_id = scene_dialogue.scene_id
JOIN graph.node AS node ON node.id = scene.node_id
GROUP BY node.canonical_key, scene.authored_order
ORDER BY scene.authored_order, node.canonical_key;

-- 4. Player-choice text available in the main quest's flow states.
WITH quest_nodes AS (
    SELECT edge.to_node_id AS id
    FROM graph.edge AS edge
    JOIN ontology.relation_type AS relation ON relation.id = edge.relation_type_id
    WHERE relation.key = 'has_quest_node'
      AND edge.from_node_id = (SELECT node_id FROM core.quest WHERE game_quest_id = 119000000)
), quest_states AS (
    SELECT DISTINCT edge.to_node_id AS id
    FROM quest_nodes AS qnode
    JOIN graph.edge AS edge ON edge.from_node_id = qnode.id
    JOIN ontology.relation_type AS relation
      ON relation.id = edge.relation_type_id AND relation.key = 'references_flow_state'
), actions AS (
    SELECT DISTINCT edge.to_node_id AS id
    FROM quest_states AS state
    JOIN graph.edge AS edge ON edge.from_node_id = state.id
    JOIN ontology.relation_type AS relation
      ON relation.id = edge.relation_type_id AND relation.key = 'contains_action'
), talk_items AS (
    SELECT DISTINCT edge.to_node_id AS id
    FROM actions AS action
    JOIN graph.edge AS edge ON edge.from_node_id = action.id
    JOIN ontology.relation_type AS relation
      ON relation.id = edge.relation_type_id AND relation.key = 'presents_talk_item'
), choices AS (
    SELECT DISTINCT edge.to_node_id AS id
    FROM graph.edge AS edge
    JOIN ontology.relation_type AS relation ON relation.id = edge.relation_type_id
    WHERE relation.key = 'presents_choice'
      AND edge.from_node_id IN (SELECT id FROM actions UNION SELECT id FROM talk_items)
)
SELECT node.canonical_key AS choice,
       text.content AS choice_text
FROM choices
JOIN core.player_choice AS choice ON choice.node_id = choices.id
JOIN graph.node AS node ON node.id = choice.node_id
LEFT JOIN i18n.localization_value AS text
  ON text.key_id = choice.localization_key_id AND text.release_id = 1 AND text.locale_id = 1
WHERE text.content <> ''
ORDER BY node.canonical_key;
