export type Notice = {
  id: number;
  slug?: string | null;
  href?: string;
  source_id: string;
  title: string;
  category: 'recommend' | 'game' | 'activity';
  banner_url: string | null;
  available_from: string | null;
  observed_at: string;
  source_url: string;
  revision: string;
  related_event_ids: number[];
};
export type NoticeDetail = Notice & {
  blocks: ({ type: 'text'; text: string } | { type: 'image'; url: string })[];
  links: string[];
  revisions: string[];
  events: { id: number; title: string; slug?: string; href?: string }[];
};
export const noticeCategories = { recommend: 'Highlights', game: 'Info', activity: 'News' };
