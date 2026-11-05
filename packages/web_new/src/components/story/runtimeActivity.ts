type Activity = {
  title?: string | null;
  condition_type?: string;
  transition?: { kind: string };
  flow_states?: unknown[];
  interactions?: unknown[];
  encounter?: unknown;
};

export const genericActivityTitle = (title: Activity['title']) =>
  !title || ['Story activity', 'Continue the story'].includes(title);

export const isSourceOnlyActivity = (block: Activity) =>
  genericActivityTitle(block.title) && block.transition?.kind !== 'dungeon_exit' &&
  !block.flow_states?.length && !block.interactions?.length && !block.encounter;

export const sidebarActivityTitle = (block: Activity) =>
  genericActivityTitle(block.title) && block.condition_type === 'PlayFlow' &&
  block.transition?.kind !== 'dungeon_exit' && !block.encounter
    ? 'Conversation' : block.title;
