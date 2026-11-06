import { Fragment, type ReactNode } from "react";
import { Film, Swords } from "lucide-react";
import { PlayerText, type PlayerDisplay } from "../dialogue/PlayerText";
import {
  encounterAnchor,
  type Encounter,
  type EncounterCinematic,
  type EncounterFlow,
  type EncounterNode,
} from "./encounterTransitions";
import styles from "./QuestEncounter.module.css";
import { QuestSequenceFragments } from "./QuestSequenceFragments";

export type RenderEncounterFlow = (flow: EncounterFlow) => ReactNode[];

export function EncounterCinematicBoundary({
  media,
  children,
  unplaced = false,
  playbackResolved = false,
  componentCount = 0,
}: {
  componentCount?: number;
  unplaced?: boolean;
  playbackResolved?: boolean;
  media: EncounterCinematic[];
  children?: ReactNode;
}) {
  return (
    <section className={styles.cinematic} aria-label="Scene">
      <div className={styles.marker}>
        <Film size={15} aria-hidden="true" />
        Scene
      </div>
      <p className={styles.note}>
        {componentCount > 0
          ? `In-game scene · ${componentCount} video components available`
          : playbackResolved
            ? "Playback available"
            : "Playback pending"}
        {unplaced ? " · placement unknown" : ""}
      </p>
      {children}
      <div className={styles.marker}>End scene</div>
      <details className={styles.source}>
        <summary>Source</summary>
        <p>
          {media.some((item) => item.kind === "sequence")
            ? "In-game cinematic sequence"
            : "Cinematic scene"}
        </p>
        {unplaced && (
          <p>
            Its position relative to the displayed dialogue is not established.
          </p>
        )}
        <p>
          Action boundaries do not establish playback duration or completion.
        </p>
        {media.map((item, index) => (
          <p key={index}>
            {item.source.file} · {item.raw_path}
          </p>
        ))}
      </details>
    </section>
  );
}

export function QuestEncounter({
  encounter,
  display,
  stateAnchors,
  renderFlow,
}: {
  encounter: Encounter;
  display: PlayerDisplay;
  stateAnchors: Map<string, string>;
  renderFlow?: RenderEncounterFlow;
}) {
  const renderOccurrence = (flow: EncounterFlow) => {
    const content = renderFlow?.(flow) || [];
    const body = content.length ? (
      content
    ) : stateAnchors.has(flow.state) ? (
      <a href={`#${stateAnchors.get(flow.state)}`}>Read this dialogue ↗</a>
    ) : (
      <p className={styles.note}>Dialogue is outside this transcript window.</p>
    );
    const title =
      flow.timing === "during"
        ? "Dialogue during the encounter"
        : flow.cinematics.length
          ? "Cinematic dialogue"
          : "Dialogue";
    const grouped = content.length ? (
      content
    ) : (
      <>
        {flow.cinematics.map((media, index) => (
          <EncounterCinematicBoundary
            key={`${flow.id}-${index}`}
            media={[media]}
          />
        ))}
        {body}
      </>
    );
    return (
      <details
        className={styles.dialogue}
        id={encounterAnchor(flow.id)}
        key={flow.id}
      >
        <summary>
          {title}
          {flow.timing === "unknown" ? " · timing unknown" : ""}
        </summary>
        {grouped}
        {!content.length && (
          <QuestSequenceFragments media={flow.sequence_media} />
        )}
      </details>
    );
  };
  const sourceOnlyNodes = new Set<string>();
  const branchTitle = (node: EncounterNode): string => {
    if (
      node.title &&
      !["Story activity", "Story action", "Continue the story"].includes(
        node.title,
      )
    )
      return node.title;
    const child = node.children.find((child) => !sourceOnlyNodes.has(child.id));
    if (child) return branchTitle(child);
    if (node.flow_states.length) return "Dialogue";
    if (node.media.length) return "Cinematic scene";
    if (node.completions.length) return "Encounter outcome";
    return "Encounter activity";
  };
  const renderNode = (node: EncounterNode): ReactNode => {
    const meaningful =
      !!node.condition?.Type ||
      !!node.flow_states.some(
        (flow) => flow.execution_context !== "save_config",
      ) ||
      !!node.media.some((item) => item.execution_context !== "save_config") ||
      !!node.completions.some(
        (item) => item.execution_context !== "save_config",
      );
    const savedFlows = node.flow_states.filter(
      (flow) =>
        flow.execution_context === "save_config" ||
        flow.phase.startsWith("SaveConfig."),
    );
    const savedMedia = node.media.filter(
      (item) => item.execution_context === "save_config",
    );
    const savedSections =
      node.action_sections?.filter(
        (section) => section.execution_context === "save_config",
      ) || [];
    const hasSavedContext =
      savedFlows.length || savedMedia.length || savedSections.length;
    const phase = (name: string, label: string) => {
      const flows = node.flow_states.filter(
        (flow) =>
          flow.phase === name && flow.execution_context !== "save_config",
      );
      const media = node.media.filter(
        (item) =>
          item.phase === name && item.execution_context !== "save_config",
      );
      if (!flows.length && !media.length) return null;
      return (
        <div className={styles.phase} key={name}>
          <small className={styles.phaseLabel}>{label}</small>
          {media.map((item, index) => (
            <EncounterCinematicBoundary
              key={`${node.id}-${name}-${index}`}
              media={[item]}
            />
          ))}
          {flows.map(renderOccurrence)}
        </div>
      );
    };
    const children = node.children
      .map((node) => ({ node, content: renderNode(node) }))
      .filter((child) => child.content !== null);
    const order = node.children_order;
    const branchLabel =
      order === "parallel"
        ? "Parallel paths"
        : order === "conditional"
          ? "Runtime conditions · order unknown"
          : order === "alternative"
            ? "Alternative paths · order unknown"
            : "Paths with unknown order";
    const childContent = !children.length ? null : order === "sequence" ? (
      <ol className={styles.steps} aria-label="Authored action order">
        {children.map((child) => (
          <li key={child.node.id}>{child.content}</li>
        ))}
      </ol>
    ) : children.length === 1 &&
      (order === "decorator" || order === "unknown") ? (
      children.map((child) => (
        <Fragment key={child.node.id}>{child.content}</Fragment>
      ))
    ) : (
      <div className={styles.branches} role="group" aria-label={branchLabel}>
        <p className={styles.note}>{branchLabel}</p>
        {order === "conditional" && (
          <p className={styles.note}>
            These paths depend on runtime conditions, not player choices.
          </p>
        )}
        {order === "alternative" && (
          <p className={styles.note}>
            How a path is selected is not established; this does not identify a
            player choice.
          </p>
        )}
        {children.map((child) => (
          <details key={child.node.id} className={styles.branch}>
            <summary>
              <PlayerText
                display={display}
                value={branchTitle(child.node)}
                fallback="Encounter activity"
              />
            </summary>
            {child.content}
          </details>
        ))}
      </div>
    );
    const hasTitle =
      !!node.title &&
      !["Story activity", "Story action", "Continue the story"].includes(
        node.title,
      );
    const hasReadableContent =
      (meaningful && hasTitle) ||
      ["EnterActions", "Actions", "Condition", "FinishActions"].some(
        (name) =>
          node.flow_states.some(
            (flow) =>
              flow.phase === name && flow.execution_context !== "save_config",
          ) ||
          node.media.some(
            (item) =>
              item.phase === name && item.execution_context !== "save_config",
          ),
      ) ||
      node.completions.some(
        (item) => item.execution_context !== "save_config",
      ) ||
      hasSavedContext;
    if (!hasReadableContent && !children.length) {
      sourceOnlyNodes.add(node.id);
      return null;
    }
    return (
      <div
        className={
          meaningful || hasSavedContext ? styles.node : styles.structure
        }
        id={encounterAnchor(node.id)}
      >
        {meaningful && (
          <>
            {hasTitle && (
              <h4>
                <PlayerText
                  display={display}
                  value={node.title}
                  fallback="Story action"
                />
              </h4>
            )}
            {phase("EnterActions", "Before this action")}
            {phase("Actions", "During this action")}
            {phase("Condition", "Action condition")}
          </>
        )}
        {childContent}
        {phase("FinishActions", "After this action")}
        {node.completions
          .filter((item) => item.execution_context !== "save_config")
          .map((completion, index) => (
            <p className={styles.marker} key={index}>
              {completion.is_success
                ? "Encounter success condition"
                : "Encounter failure condition"}
            </p>
          ))}
        {!!hasSavedContext && (
          <details className={styles.context}>
            <summary>Additional lifecycle context · order unknown</summary>
            <p className={styles.note}>
              Its timing relative to this encounter is not established.
            </p>
            {savedMedia.map((item, index) => (
              <EncounterCinematicBoundary
                key={`${node.id}-saved-${index}`}
                media={[item]}
              />
            ))}
            {savedFlows.map(renderOccurrence)}
            {savedSections.some(
              (section) => section.status === "unsupported_shape",
            ) && (
              <p className={styles.note}>
                Some lifecycle action details could not be decoded.
              </p>
            )}
          </details>
        )}
      </div>
    );
  };
  const trees = encounter.trees.map((tree) => ({
    tree,
    content: renderNode(tree.root),
  }));
  const renderSource = (node: EncounterNode): ReactNode => (
    <div
      key={node.id}
      id={sourceOnlyNodes.has(node.id) ? encounterAnchor(node.id) : undefined}
    >
      <p>
        {node.node_type} · {node.owner} · {node.source.file} ·{" "}
        {node.source.raw_path}
      </p>
      <p>
        <code>
          {JSON.stringify(
            {
              condition: node.condition,
              branch_condition: node.branch_condition,
              count: node.count,
              children_order: node.children_order,
              source: node.source,
              action_sections: node.action_sections,
              completions: node.completions,
            },
            null,
            2,
          )}
        </code>
      </p>
      {node.children.map(renderSource)}
    </div>
  );
  return (
    <section
      className={styles.encounter}
      aria-label="Encounter activities"
      id={encounterAnchor(encounter.id)}
    >
      <div className={styles.marker}>
        <Swords size={16} aria-hidden="true" />
        Encounter
      </div>
      <h3>
        <PlayerText
          display={display}
          value={encounter.title}
          fallback="Encounter"
        />
      </h3>
      <p className={styles.note}>
        Only ordered steps establish a sequence. Other paths may be conditional
        or have unknown order.
      </p>
      {trees
        .filter(({ content }) => content !== null)
        .map(({ tree, content }) => (
          <details
            key={tree.level_play_id}
            className={styles.context}
            open={encounter.trees.length === 1}
          >
            <summary>Encounter activities</summary>
            {content}
          </details>
        ))}
      {encounter.unresolved_level_plays?.map((tree) => (
        <p className={styles.note} key={tree.level_play_id}>
          An additional encounter context is recorded, but its matching authored
          tree is unavailable.
        </p>
      ))}
      {!encounter.trees.length && !encounter.unresolved_level_plays?.length && (
        <p className={styles.note}>
          Authored encounter activities are not available in this snapshot.
        </p>
      )}
      <div className={styles.marker}>End of encounter context</div>
      <details className={styles.source}>
        <summary>Source &amp; conditions</summary>
        {encounter.trees.length +
          (encounter.unresolved_level_plays?.length || 0) >
          1 && (
          <p>
            The relative order of these encounter contexts is not established.
          </p>
        )}
        <p>
          {encounter.source.file} · {encounter.source.raw_path}
        </p>
        {encounter.trees.map((tree) => (
          <Fragment key={tree.level_play_id}>
            <p>
              {tree.membership.file} · {tree.level_play_id}
            </p>
            {renderSource(tree.root)}
          </Fragment>
        ))}
        {encounter.unresolved_level_plays?.map((tree) => (
          <p key={tree.level_play_id}>
            {tree.level_play_id} · {tree.reason}
          </p>
        ))}
      </details>
    </section>
  );
}
