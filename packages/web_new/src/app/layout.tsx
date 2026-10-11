import type { Metadata, Viewport } from 'next';
import { DM_Sans, Manrope } from 'next/font/google';

import '@/styles/index.css';

import { Footer } from '@/components/layout/Footer';
import { Header } from '@/components/layout/Header';
import { Providers } from '@/components/layout/Providers';

const dmSans = DM_Sans({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-body',
  display: 'swap',
});

const manrope = Manrope({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700', '800'],
  variable: '--font-display',
  display: 'swap',
});

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://solarisatlas.fun';

export const viewport: Viewport = {
  themeColor: '#090E0F',
  width: 'device-width',
  initialScale: 1,
};

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: 'Solaris Atlas — Story Archive for Solaris-3',
    template: '%s | Solaris Atlas',
  },
  description:
    'Solaris Atlas — a connected story archive for Solaris-3. Explore Wuthering Waves characters, stories, factions, locations, lore and items.',
  icons: {
    icon: '/favicon.svg',
  },
  alternates: {
    canonical: '/',
  },
  openGraph: {
    type: 'website',
    locale: 'en_US',
    url: siteUrl,
    siteName: 'Solaris Atlas',
    title: 'Solaris Atlas — Story Archive for Solaris-3',
    description:
      'A connected story archive for Solaris-3. Explore Wuthering Waves characters, stories, factions, locations, lore and items.',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Solaris Atlas — Story Archive for Solaris-3',
    description: 'A connected story archive for Solaris-3.',
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${dmSans.variable} ${manrope.variable}`}>
      <body>
        <Providers>
          <div className="app-shell">
            <Header />
            <main>{children}</main>
            <Footer />
          </div>
        </Providers>
      </body>
    </html>
  );
}
