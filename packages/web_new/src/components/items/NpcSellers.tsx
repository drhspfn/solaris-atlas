'use client';

import { useEffect, useState } from 'react';
import { api } from '@/api/client';
import { ErrorPanel } from '@/components/ui/Feedback';
import { Pagination } from '@/components/ui/Pagination';
import { Link } from '@/lib/router';

type Seller = { canonical_key: string; href: string; name: string; offers: { shop_id: number; quantity: number | null }[] };
const PAGE_SIZE = 20;

export function NpcSellers({ itemId, locale }: { itemId: number; locale: string }) {
  const [page, setPage] = useState(1);
  const [result, setResult] = useState<{ results: Seller[]; total: number } | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ locale, limit: String(PAGE_SIZE), offset: String((page - 1) * PAGE_SIZE) });
    setResult(null); setError('');
    api<{ results: Seller[]; total: number }>(`/item-npc-sellers/${itemId}?${params}`, { signal: controller.signal })
      .then((value) => { if (!controller.signal.aborted) setResult(value); })
      .catch((cause: Error) => { if (!controller.signal.aborted) setError(cause.message); });
    return () => controller.abort();
  }, [itemId, locale, page, retry]);
  if (result && result.total === 0) return null;
  return <div className="subsection"><h3>NPC shopkeepers</h3>
    <p>Configured shop offers; current availability is not confirmed.</p>
    {error ? <ErrorPanel message={error} onRetry={() => setRetry((value) => value + 1)} /> : !result ? <p role="status">Loading shopkeepers…</p> : <>
      {result.results.map((seller) => <Link className="connection-row" key={seller.canonical_key} to={seller.href}>
        <strong>{seller.name}</strong><small>{seller.offers.length} configured {seller.offers.length === 1 ? 'offer' : 'offers'}</small>
      </Link>)}
      {result.total > PAGE_SIZE && <Pagination mode="pages" page={page} pageSize={PAGE_SIZE} total={result.total} onPageChange={setPage} ariaLabel="Shopkeeper pages" />}
    </>}
  </div>;
}
