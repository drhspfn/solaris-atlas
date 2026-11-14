import Link from "next/link";
import { Breadcrumbs } from "@/components/ui/Breadcrumbs";
import { PageHeader } from "@/components/ui/PageHeader";
import "@/styles/event-archive.css";

export function EventArchiveHeader({
  active,
}: {
  active: "calendar" | "announcements";
}) {
  const title = active === "calendar" ? "Events" : "Announcements";
  return (
    <>
      <Breadcrumbs
        items={[{ label: "Archive", href: "/" }, { label: title }]}
      />
      <PageHeader
        title={title}
        description="Event calendars, official announcements and every recorded run."
      />
      <nav className="event-archive-nav" aria-label="Events and announcements">
        <Link
          href="/events"
          aria-current={active === "calendar" ? "page" : undefined}
        >
          Calendar
        </Link>
        <Link
          href="/notices"
          aria-current={active === "announcements" ? "page" : undefined}
        >
          Announcements
        </Link>
      </nav>
    </>
  );
}
