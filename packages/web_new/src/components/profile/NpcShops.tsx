"use client";

import { useEffect, useState } from "react";
import { Package, Store } from "lucide-react";
import { api } from "@/api/client";
import { ErrorPanel } from "@/components/ui/Feedback";
import { Skeleton, SkeletonGroup } from "@/components/ui/Skeleton";
import { Pagination } from "@/components/ui/Pagination";
import { PanelTitle } from "@/components/ui/PanelTitle";
import {
  PlayerText,
  type PlayerDisplay,
} from "@/components/dialogue/PlayerText";
import { useLocale } from "@/hooks/useLocale";
import { Link } from "@/lib/router";

const PAGE_SIZE = 50;
type Item = {
  id?: number;
  label: string;
  href?: string;
  slug?: string;
  image_url?: string;
};
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

export function NpcShopSkeleton() {
  return (
    <div>
      <Skeleton style={{ width: "45%", height: 16, margin: "8px 0" }} />
    </div>
  );
}

export function NpcOfferSkeleton() {
  return (
    <div className="npc-shop-card skeleton">
      <Skeleton
        style={{ width: 56, height: 56, borderRadius: "var(--radius-sm)" }}
      />
      <div style={{ flex: 1 }}>
        <Skeleton style={{ width: "70%", height: 16, marginBottom: 8 }} />
        <Skeleton style={{ width: "40%", height: 14 }} />
      </div>
    </div>
  );
}

function OfferCard({
  offer,
  display,
}: {
  offer: Offer;
  display: PlayerDisplay;
}) {
  const item = offer.item;
  const count = offer.count ?? 1;
  const isLocked = Boolean(
    (offer.conditions?.DisableCondition &&
      offer.conditions.DisableCondition !== 0) ||
    (offer.conditions?.CondId && offer.conditions.CondId !== 0),
  );

  return (
    <article className="npc-shop-card">
      <div className="npc-shop-card-media">
        {item.image_url ? (
          <img
            src={item.image_url}
            alt={typeof item.label === "string" ? item.label : "Item icon"}
            loading="lazy"
            className="npc-shop-card-img"
          />
        ) : (
          <div className="npc-shop-card-placeholder">
            <Package size={26} aria-hidden="true" />
          </div>
        )}
        {count > 1 && <span className="npc-shop-card-count">{count}×</span>}
      </div>
      <div className="npc-shop-card-body">
        <h4 className="npc-shop-card-title">
          {item.href ? (
            <Link to={item.href}>
              <PlayerText value={item.label} display={display} />
            </Link>
          ) : (
            <PlayerText value={item.label} display={display} />
          )}
        </h4>
        <div className="npc-shop-card-price-row">
          {offer.price.map((price, i) => (
            <span key={i} className="npc-shop-price-item">
              <span className="npc-shop-price-amount">{price.amount}</span>
              {price.item.image_url ? (
                <img
                  src={price.item.image_url}
                  alt={
                    typeof price.item.label === "string"
                      ? price.item.label
                      : "Currency"
                  }
                  className="npc-currency-icon"
                  title={
                    typeof price.item.label === "string"
                      ? price.item.label
                      : undefined
                  }
                />
              ) : (
                <span className="npc-shop-price-currency">
                  <PlayerText value={price.item.label} display={display} />
                </span>
              )}
            </span>
          ))}
          {offer.original_price.length > 0 &&
            offer.original_price[0].amount > (offer.price[0]?.amount ?? 0) && (
              <s className="npc-shop-original-price">
                {offer.original_price[0].amount}
              </s>
            )}
        </div>
        <div className="npc-shop-card-footer">
          {offer.limit != null && offer.limit > 0 ? (
            <span className="npc-shop-badge limit">Limit {offer.limit}</span>
          ) : (
            <span className="npc-shop-badge unlimited">Unlimited</span>
          )}
          {isLocked && <span className="npc-shop-badge locked">Locked</span>}
        </div>
      </div>
    </article>
  );
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
  const [open, setOpen] = useState(true);
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
    <div className="npc-shop-container">
      <header className="npc-shop-header">
        <h3>
          <Store size={18} aria-hidden="true" />
          <PlayerText
            value={shop.name}
            fallback={`Shop ${shop.id}`}
            display={display}
          />
        </h3>
        <span className="npc-shop-stock-badge">
          {shop.offer_count} offers configured
        </span>
      </header>
      {error ? (
        <ErrorPanel
          message={error}
          onRetry={() => setRetry((value) => value + 1)}
        />
      ) : !result ? (
        <SkeletonGroup label="Loading offers">
          <div className="npc-shop-goods-grid">
            {Array.from({ length: 4 }, (_, i) => (
              <NpcOfferSkeleton key={i} />
            ))}
          </div>
        </SkeletonGroup>
      ) : (
        <>
          {!result.results.length && <p>No fixed offers recorded.</p>}
          <div className="npc-shop-goods-grid">
            {result.results.map((offer, index) => (
              <OfferCard
                key={`${offer.id}-${index}`}
                offer={offer}
                display={display}
              />
            ))}
          </div>
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
    </div>
  );
}

export function NpcShops({
  characterKey,
  gameVersion,
  display,
  sectionNumber = "01",
}: {
  characterKey: string;
  gameVersion?: string;
  display: PlayerDisplay;
  sectionNumber?: string;
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
    <section className="content-panel full-panel npc-section npc-shops-section">
      <PanelTitle number={sectionNumber} title="Shops & Merchandise" />
      <p className="npc-section-intro">
        In-game merchant inventory and source purchase records.
      </p>
      {error ? (
        <ErrorPanel
          message={error}
          onRetry={() => setRetry((value) => value + 1)}
        />
      ) : !result ? (
        <SkeletonGroup label="Loading shops">
          {Array.from({ length: 2 }, (_, i) => (
            <NpcShopSkeleton key={i} />
          ))}
        </SkeletonGroup>
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
