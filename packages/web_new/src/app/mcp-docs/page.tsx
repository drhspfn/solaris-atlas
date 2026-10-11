import type { Metadata } from 'next';
import { Suspense } from 'react';

import { McpDocsClient } from '@/components/docs/McpDocsClient';
import { PageLoader } from '@/components/ui/Feedback';

export const metadata: Metadata = {
  title: 'Model Context Protocol (MCP) — Solaris Atlas',
  description:
    'Connect compatible AI clients (Claude Desktop, Cursor, agents) directly to our Wuthering Waves lore database via standard MCP SSE.',
  openGraph: {
    title: 'Model Context Protocol (MCP) — Solaris Atlas',
    description:
      'Connect compatible AI clients directly to our Wuthering Waves lore database via standard MCP SSE.',
    type: 'website',
  },
  twitter: {
    card: 'summary',
    title: 'Model Context Protocol (MCP) — Solaris Atlas',
    description: 'Connect AI clients directly to the Wuthering Waves lore database.',
  },
};

export default function McpDocsPage() {
  return (
    <Suspense fallback={<PageLoader />}>
      <McpDocsClient />
    </Suspense>
  );
}
