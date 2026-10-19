import Link from 'next/link';
import '@/styles/event-archive.css';

export function EventArchiveHeader({ active }: { active: 'calendar' | 'announcements' }) {
  return (
    <>
      <header className="event-archive-heading">
        <span className="events-kicker">SOLARIS / EVENT ARCHIVE</span>
        <h1>{active === 'calendar' ? 'Events' : 'Announcements'}</h1>
        <p>Event calendars, official announcements and every recorded run.</p>
      </header>
      <nav className="event-archive-nav" aria-label="Events and announcements">
        <Link href="/events" aria-current={active === 'calendar' ? 'page' : undefined}>
          Calendar
        </Link>
        <Link href="/notices" aria-current={active === 'announcements' ? 'page' : undefined}>
          Announcements
        </Link>
      </nav>
    </>
  );
}
