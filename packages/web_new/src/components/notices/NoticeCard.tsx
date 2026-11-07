import Link from 'next/link';
import { noticeCategories, type Notice } from '@/lib/notices';
import { Skeleton } from '@/components/ui/Skeleton';

export function NoticeCard({ notice }: { notice: Notice }) {
  return <Link className="notice-card" href={notice.href || `/notices/${notice.slug || notice.id}`}>
    <div className="notice-card-image">
      {notice.banner_url ? <img src={notice.banner_url} alt="" loading="lazy" /> : <span>{noticeCategories[notice.category]}</span>}
    </div>
    <div>
      <span>{noticeCategories[notice.category]}</span>
      <h2>{notice.title}</h2>
      <small>{notice.related_event_ids.length ? 'Event details · ' : ''}Read announcement ↗</small>
    </div>
  </Link>;
}

export function NoticeCardSkeleton() {
  return <div className="notice-card">
    <div className="notice-card-image"><Skeleton style={{ height: '100%' }} /></div>
    <div>
      <Skeleton style={{ width: '32%', height: 11 }} />
      <h2><Skeleton style={{ width: '80%', height: 20 }} /></h2>
      <Skeleton style={{ width: '48%', height: 12 }} />
    </div>
  </div>;
}
