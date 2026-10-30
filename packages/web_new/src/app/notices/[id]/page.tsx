import type { Metadata } from 'next';
import Link from 'next/link';
import { connection } from 'next/server';
import { serverApi } from '@/api/serverApi';
import { canonicalRoute } from '@/lib/canonicalRoute';
import { decodeRouteKey } from '@/lib/routeKey';
import { noticeCategories, type NoticeDetail } from '@/lib/notices';
import '@/styles/notices.css';

type Props = { params: Promise<{ id: string }>; searchParams: Promise<{ revision?: string }> };
async function readNotice(params: Props['params'], searchParams: Props['searchParams']) {
  const { id } = await params;
  const { revision } = await searchParams;
  const query = revision && /^[a-f0-9]{64}$/.test(revision) ? `?revision=${revision}` : '';
  return serverApi<NoticeDetail>(`/notices/${encodeURIComponent(decodeRouteKey(id))}${query}`, { revalidate: 0 });
}
export async function generateMetadata({ params, searchParams }: Props): Promise<Metadata> {
  const notice = await readNotice(params, searchParams);
  if (!notice) return { title: 'Announcement unavailable — Solaris Atlas' };
  const description = notice.blocks
    .filter((block) => block.type === 'text')
    .map((block) => block.text)
    .join(' ')
    .slice(0, 180);
  return {
    title: `${notice.title} — Solaris Atlas`,
    description,
    alternates: { canonical: notice.href || `/notices/${notice.slug || notice.id}` },
    openGraph: {
      title: notice.title,
      description,
      ...(notice.banner_url ? { images: [notice.banner_url] } : {}),
    },
  };
}
export default async function NoticePage({ params, searchParams }: Props) {
  await connection();
  const notice = await readNotice(params, searchParams);
  if (!notice) {
    const { id } = await params;
    const { revision } = await searchParams;
    const retryQuery = revision && /^[a-f0-9]{64}$/.test(revision) ? `?revision=${revision}` : '';
    return (
      <main className="notices-page">
        <h1>Announcement unavailable</h1>
        <p>The archive could not load this announcement.</p>
        <p><a href={`/notices/${encodeURIComponent(id)}${retryQuery}`}>Try again</a></p>
        <Link href="/notices">Return to announcements</Link>
      </main>
    );
  }
  await canonicalRoute("notices", decodeRouteKey((await params).id), notice, searchParams);
  return (
    <main className="notices-page notice-detail">
      <Link href="/notices">← All announcements</Link>
      <header className="notices-heading">
        <div>
          <span>{noticeCategories[notice.category]}</span>
          <h1>{notice.title}</h1>
        </div>
      </header>
      {!!notice.events.length && (
        <nav className="notice-related" aria-label="Related events">
          {notice.events.map((event) => (
            <Link key={event.id} href={event.href || `/events?event=${event.slug || event.id}`}>
              {event.title} · Show history ↗
            </Link>
          ))}
        </nav>
      )}
      <article className="notice-body">
        {notice.blocks.map((block, index) =>
          block.type === 'image' ? (
            <img
              key={index}
              src={block.url}
              alt={`Announcement illustration ${index + 1}`}
              loading="lazy"
            />
          ) : (
            <p key={index}>{block.text}</p>
          ),
        )}
      </article>
      {!!notice.links.length && (
        <section className="notice-links">
          <h2>Links in this announcement</h2>
          {notice.links.map((url) => (
            <a key={url} href={url} target="_blank" rel="noopener noreferrer">
              {new URL(url).hostname} ↗
            </a>
          ))}
        </section>
      )}
      {notice.revisions.length > 1 && (
        <details className="notice-revisions">
          <summary>Archived revisions</summary>
          {notice.revisions.map((revision, index) => (
            <Link
              key={revision}
              href={`${notice.href || `/notices/${notice.slug || notice.id}`}?revision=${revision}`}
              aria-current={revision === notice.revision ? 'page' : undefined}
            >
              {index === 0
                ? 'Latest revision'
                : `Earlier revision ${notice.revisions.length - index}`}
            </Link>
          ))}
        </details>
      )}
      <footer className="notice-detail-footer">
        <a href={notice.source_url} target="_blank" rel="noopener noreferrer">
          Open Kuro’s Notice board ↗
        </a>
        <span>
          Saved {new Date(notice.observed_at).toLocaleDateString('en', { timeZone: 'UTC' })} ·{' '}
          {notice.revisions.length} archived revision{notice.revisions.length === 1 ? '' : 's'}
        </span>
      </footer>
    </main>
  );
}
