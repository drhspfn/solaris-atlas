import { Skeleton, SkeletonGroup, TextSkeleton } from '@/components/ui/Skeleton';
import '@/styles/notices.css';

export default function NoticeLoading() {
  return <main className="notices-page notice-detail">
    <SkeletonGroup label="Loading announcement">
      <header className="notices-heading"><div style={{ width: '100%' }}>
        <Skeleton style={{ width: '20%' }} />
        <h1><Skeleton style={{ width: '65%', height: 36 }} /></h1>
      </div></header>
      <article className="notice-body"><TextSkeleton lines={8} /></article>
    </SkeletonGroup>
  </main>;
}
