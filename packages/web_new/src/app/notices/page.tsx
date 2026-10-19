import type { Metadata } from 'next';
import Link from 'next/link';
import { connection } from 'next/server';
import { serverApi } from '@/api/serverApi';
import { noticeCategories, type Notice } from '@/lib/notices';
import '@/styles/notices.css';

export const metadata: Metadata = {
  title: 'Announcements — Solaris Atlas',
  description:
    'Official Wuthering Waves in-game announcements, event rules, news and update history.',
};

export default async function NoticesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  await connection();
  const params = await searchParams;
  const category =
    typeof params.category === 'string' && Object.hasOwn(noticeCategories, params.category)
      ? params.category
      : '';
  const q = typeof params.q === 'string' ? params.q.slice(0, 160) : '';
  const requestedOffset = Number(params.offset);
  const offset =
    Number.isSafeInteger(requestedOffset) && requestedOffset >= 0
      ? Math.min(requestedOffset, 100000)
      : 0;
  const query = new URLSearchParams({ q, offset: String(offset), limit: '24' });
  if (category) query.set('category', category);
  if (typeof params.event_id === 'string' && /^\d+$/.test(params.event_id))
    query.set('event_id', params.event_id);
  const data = await serverApi<{ notices: Notice[]; has_more: boolean }>(`/notices?${query}`, {
    revalidate: 0,
  });
  const href = (changes: Record<string, string>) => {
    const next = new URLSearchParams(query);
    for (const [key, value] of Object.entries(changes))
      value ? next.set(key, value) : next.delete(key);
    return `/notices?${next}`;
  };
  return (
    <main className="notices-page">
      <header className="notices-heading">
        <div>
          <span>SOLARIS / OFFICIAL NOTICE ARCHIVE</span>
          <h1>Announcements</h1>
          <p>Event rules, highlights and updates from the game’s Notice board.</p>
        </div>
        <Link href="/events">Event calendar ↗</Link>
      </header>
      <nav className="notices-tabs" aria-label="Announcement categories">
        <Link
          href={href({ category: '', offset: '0' })}
          aria-current={!category ? 'page' : undefined}
        >
          All announcements
        </Link>
        {Object.entries(noticeCategories).map(([value, label]) => (
          <Link
            key={value}
            href={href({ category: value, offset: '0' })}
            aria-current={category === value ? 'page' : undefined}
          >
            {label}
          </Link>
        ))}
      </nav>
      <form className="notices-search" action="/notices" noValidate>
        <label htmlFor="notice-search">Find an announcement</label>
        <div>
          <input
            id="notice-search"
            type="search"
            name="q"
            defaultValue={q}
            maxLength={160}
            placeholder="Event name, patch notes…"
          />
          {category && <input type="hidden" name="category" value={category} />}
          <button type="submit">Search</button>
        </div>
      </form>
      {!data ? (
        <p role="alert">
          The announcement archive could not be loaded. <Link href={href({})}>Try again</Link>
        </p>
      ) : (
        <>
          {!data.notices.length && (
            <p className="notices-empty">
              {q || category || offset
                ? 'No announcements match these filters. Try another category or search.'
                : 'No in-game announcements have been imported yet.'}{' '}
              <Link href="/notices">Reset filters</Link>
            </p>
          )}
          <div className="notices-grid">
            {data.notices.map((notice) => (
              <Link className="notice-card" href={notice.href || `/notices/${notice.slug || notice.id}`} key={notice.id}>
                <div className="notice-card-image">
                  {notice.banner_url ? (
                    <img src={notice.banner_url} alt="" loading="lazy" />
                  ) : (
                    <span>{noticeCategories[notice.category]}</span>
                  )}
                </div>
                <div>
                  <span>{noticeCategories[notice.category]}</span>
                  <h2>{notice.title}</h2>
                  <small>
                    {notice.related_event_ids.length ? 'Event details · ' : ''}Read announcement ↗
                  </small>
                </div>
              </Link>
            ))}
          </div>
          <nav className="notices-pagination" aria-label="Announcement pages">
            {offset > 0 && (
              <Link href={href({ offset: String(Math.max(0, offset - 24)) })}>← Previous</Link>
            )}
            {data.has_more && <Link href={href({ offset: String(offset + 24) })}>Next →</Link>}
          </nav>
        </>
      )}
      <p className="notices-source-note">
        Archived from the public in-game Notice feed. Availability and content may differ by server
        or language. Older entries remain in the archive after they leave the live board.
      </p>
    </main>
  );
}
