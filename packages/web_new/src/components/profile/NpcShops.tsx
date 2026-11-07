"use client";

import { useEffect, useState } from "react";
import { api } from "@/api/client";
import { ErrorPanel } from "@/components/ui/Feedback";
import { Pagination } from "@/components/ui/Pagination";
import { PanelTitle } from "@/components/ui/PanelTitle";
import {
  PlayerText,
  type PlayerDisplay,
} from "@/components/dialogue/PlayerText";
import { useLocale } from "@/hooks/useLocale";
import { Link } from "@/lib/router";

const PAGE_SIZE = 50;
type Item = { label: string; href?: string };
type Price = { item: Item; amount: number };
type Offer = {
  id: number;
  item: Item;
  count: number | null;
  price: Price[];
  original_price: Price[];
  limit: number | null;
  conditions: Record<string, unknown>;
};
type Shop = { id: number; name: unknown; offer_count: number };
type Page<T> = { results: T[]; total: number };

function ItemLabel({ item, display }: { item: Item; display: PlayerDisplay }) {
  const label = <PlayerText value={item.label} display={display} />;
  return item.href ? <Link to={item.href}>{label}</Link> : <span>{label}</span>;
}

function ShopOffers({
  characterKey,
  gameVersion,
  shop,
  display,
}: {
  characterKey: string;
  gameVersion?: string;
  shop: Shop;
  display: PlayerDisplay;
}) {
  const locale = useLocale();
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState(1);
  const [result, setResult] = useState<Page<Offer> | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const params = new URLSearchParams({
      locale,
      limit: String(PAGE_SIZE),
      offset: String((page - 1) * PAGE_SIZE),
    });
    if (gameVersion) params.set("game_version", gameVersion);
    setResult(null);
    setError("");
    api<Page<Offer>>(
      `/npcs/${encodeURIComponent(characterKey)}/shops/${shop.id}/offers?${params}`,
      { signal: controller.signal },
    )
      .then((value) => {
        if (!controller.signal.aborted) setResult(value);
      })
      .catch((cause: Error) => {
        if (!controller.signal.aborted) setError(cause.message);
      });
    return () => controller.abort();
  }, [characterKey, shop.id, gameVersion, locale, page, retry, open]);
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>
        <PlayerText
          value={shop.name}
          fallback={`Shop ${shop.id}`}
          display={display}
        />{" "}
        · {shop.offer_count} configured offers
      </summary>
      <p>Reset: unknown</p>
      {error ? (
        <ErrorPanel
          message={error}
          onRetry={() => setRetry((value) => value + 1)}
        />
      ) : !result ? (
        <p role="status">Loading offers…</p>
      ) : (
        <>
          {!result.results.length && <p>No fixed offers recorded.</p>}
          {result.results.map((offer, index) => (
            <div className="plain-row" key={`${offer.id}-${index}`}>
              <div>
                <ItemLabel item={offer.item} display={display} /> · Quantity{" "}
                {offer.count ?? "unknown"}
                <br />
                <small>
                  Configured conditions:{" "}
                  {Object.entries(offer.conditions)
                    .filter(([, value]) => value != null)
                    .map(([key, value]) => `${key}: ${JSON.stringify(value)}`)
                    .join(" · ") || "none recorded"}
                </small>
              </div>
              <div>
                Price:{" "}
                {offer.price.length
                  ? offer.price.map((price, i) => (
                      <span key={i}>
                        {i > 0 ? ", " : ""}
                        {price.amount} ×{" "}
                        <ItemLabel item={price.item} display={display} />
                      </span>
                    ))
                  : "unknown"}
                {offer.original_price.length > 0 && (
                  <div>
                    Original price:{" "}
                    {offer.original_price.map((price, i) => (
                      <span key={i}>
                        {i > 0 ? ", " : ""}
                        {price.amount} ×{" "}
                        <ItemLabel item={price.item} display={display} />
                      </span>
                    ))}
                  </div>
                )}
                {offer.limit != null && offer.limit >= 0
                  ? ` · Limit ${offer.limit}`
                  : ""}
              </div>
            </div>
          ))}
          {result.total > PAGE_SIZE && (
            <Pagination
              mode="pages"
              page={page}
              pageSize={PAGE_SIZE}
              total={result.total}
              onPageChange={setPage}
              ariaLabel={`Shop ${shop.id} offer pages`}
            />
          )}
        </>
      )}
    </details>
  );
}

export function NpcShops({
  characterKey,
  gameVersion,
  display,
}: {
  characterKey: string;
  gameVersion?: string;
  display: PlayerDisplay;
}) {
  const locale = useLocale();
  const [page, setPage] = useState(1);
  const [result, setResult] = useState<Page<Shop> | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({
      locale,
      limit: String(PAGE_SIZE),
      offset: String((page - 1) * PAGE_SIZE),
    });
    if (gameVersion) params.set("game_version", gameVersion);
    setResult(null);
    setError("");
    api<Page<Shop>>(
      `/npcs/${encodeURIComponent(characterKey)}/shops?${params}`,
      { signal: controller.signal },
    )
      .then((value) => {
        if (!controller.signal.aborted) setResult(value);
      })
      .catch((cause: Error) => {
        if (!controller.signal.aborted) setError(cause.message);
      });
    return () => controller.abort();
  }, [characterKey, gameVersion, locale, page, retry]);
  return (
    <section className="content-panel full-panel">
      <PanelTitle number="03" title="Shops" />
      <p>
        Configured offers and conditions are source records; availability in
        game may differ.
      </p>
      {error ? (
        <ErrorPanel
          message={error}
          onRetry={() => setRetry((value) => value + 1)}
        />
      ) : !result ? (
        <p role="status">Loading shops…</p>
      ) : (
        <>
          {result.results.map((shop) => (
            <ShopOffers
              key={shop.id}
              shop={shop}
              characterKey={characterKey}
              gameVersion={gameVersion}
              display={display}
            />
          ))}
          {result.total > PAGE_SIZE && (
            <Pagination
              mode="pages"
              page={page}
              pageSize={PAGE_SIZE}
              total={result.total}
              onPageChange={setPage}
              ariaLabel="Shop pages"
            />
          )}
        </>
      )}
    </section>
  );
}
