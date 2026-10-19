import { ArrowRight, BookOpen, MapPin, Users } from 'lucide-react';
import { Suspense } from 'react';
import type { Metadata } from 'next';

import { serverApi } from '@/api/serverApi';
import { PublicChat } from '@/components/chat/PublicChat';
import { SearchBox } from '@/components/search/SearchBox';
import { categories } from '@/data/entities';
import { Link } from '@/lib/router';

export const metadata: Metadata = {
  title: 'Solaris Atlas — Story Archive for Solaris-3',
  description:
    'Explore the people, places and story connections woven across Solaris-3. Source-linked Wuthering Waves narrative archive.',
  openGraph: {
    title: 'Solaris Atlas — Story Archive for Solaris-3',
    description:
      'Explore the people, places and story connections woven across Solaris-3. Source-linked Wuthering Waves narrative archive.',
    url: 'https://solarisatlas.fun/',
    type: 'website',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Solaris Atlas — Story Archive for Solaris-3',
    description: 'Explore the people, places and story connections woven across Solaris-3.',
  },
};

export default async function HomePage() {
  const categoriesData = await serverApi<{
    browse_categories: { key: string; count: number }[];
  }>('/categories');

  const counts: Record<string, number> = {};
  if (categoriesData?.browse_categories) {
    for (const c of categoriesData.browse_categories) {
      counts[c.key] = c.count;
    }
  }

  return (
    <>
      <section className="hero">
        <div className="hero-art">
          <div className="orbit orbit-one" />
          <div className="orbit orbit-two" />
          <div className="hero-star">✳</div>
        </div>
        <div className="hero-copy">
          <div className="eyebrow">
            <span className="eyebrow-line" /> THE STORY ATLAS <span className="eyebrow-line" />
          </div>
          <h1>
            Every story leaves
            <br />
            <em>a resonance.</em>
          </h1>
          <p>Explore the people, places and story connections woven across Solaris-3.</p>
          <Suspense fallback={<div className="search-box" style={{ minHeight: '44px' }} />}>
            <SearchBox />
          </Suspense>
          <div className="hero-foot">
            <span>

              <span className="live-dot" /> SOURCE-LINKED GAME ARCHIVE
            </span>
            <span>GAME DATA · EN</span>
          </div>
        </div>
        <div className="hero-index">
          SOLARIS-3 <span>·</span> ARCHIVE 01
        </div>
      </section>

      <section className="section-wrap" style={{ marginTop: '40px' }}>
        <Suspense
          fallback={
            <div className="public-chat-card" style={{ minHeight: '140px', opacity: 0.6 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '16px' }}>
                <span className="live-dot" />
                <span>Loading Solaris Atlas lore assistant…</span>
              </div>
            </div>
          }
        >
          <PublicChat />
        </Suspense>
      </section>

      <section className="section-wrap category-section">
        <div className="section-head">
          <div>
            <span className="eyebrow left">EXPLORE THE ARCHIVE</span>
            <h2>Where will you begin?</h2>
          </div>
          <span className="section-note">A world told through its people and places</span>
        </div>
        <div className="category-grid">
          {categories.map((c) => (
            <Link to={`/catalog/${c.key}`} className={`category-card card-${c.key}`} key={c.key}>
              <div className="card-top">
                <span className="category-icon">
                  <c.icon size={19} />
                </span>
                <span className="card-count">
                  {(counts[c.key] ?? 0).toLocaleString()} <span>entries</span>
                </span>
              </div>
              <div>
                <h3>{c.key === 'quest' ? 'Story' : c.label}</h3>
                <p>{c.description}</p>
              </div>
              <div className="card-bottom">
                <span>Explore collection</span>
                <ArrowRight className="arrow-diagonal" />
              </div>
            </Link>
          ))}
        </div>
      </section>

      <section className="quote-band">
        <div className="quote-icon">✧</div>
        <p>“The echoes of the past are never truly silent.”</p>
        <span>— A world still unfolding</span>
      </section>

      <section className="section-wrap home-explore">
        <div className="explore-copy">
          <span className="eyebrow left">FOLLOW THE THREAD</span>
          <h2>
            One discovery
            <br />
            leads to another.
          </h2>
          <p>
            Open a character to see their story appearances and connections. Step into a story to
            read every scene and line, with choices preserved in context.
          </p>
          <Link className="text-link" to="/story-map">
            Follow the story map <ArrowRight size={16} />
          </Link>
        </div>
        <div className="path-visual">
          <span className="path-caption">A connected story archive</span>
          <div className="path-node main-node">
            <Users size={17} />
            <span>Character</span>
          </div>
          <div className="path-link">
            <span />
            <small>appears in</small>
          </div>
          <div className="path-node">
            <BookOpen size={16} />
            <span>Story scene</span>
          </div>
          <div className="path-link">
            <span />
            <small>takes place at</small>
          </div>
          <div className="path-node">
            <MapPin size={16} />
            <span>Location</span>
          </div>
          <span className="path-footnote">
            Illustrative path · links follow game source records
          </span>
        </div>
      </section>
    </>
  );
}
