import { EventArchiveHeader } from "@/components/events/EventArchiveHeader";
import { NoticeCardSkeleton } from "@/components/notices/NoticeCard";
import { SkeletonGroup } from "@/components/ui/Skeleton";
import "@/styles/notices.css";

export default function NoticesLoading() {
  return (
    <main className="notices-page">
      <EventArchiveHeader active="announcements" />
      <SkeletonGroup className="notices-grid" label="Loading announcements">
        {Array.from({ length: 6 }, (_, index) => (
          <NoticeCardSkeleton key={index} />
        ))}
      </SkeletonGroup>
    </main>
  );
}
