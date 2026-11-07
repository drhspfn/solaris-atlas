"use client";

import { useEffect, useState } from "react";
import { api } from "@/api/client";
import { ErrorPanel } from "@/components/ui/Feedback";
import { Pagination } from "@/components/ui/Pagination";
import { localizedText } from "@/data/localized";
import { useLocale } from "@/hooks/useLocale";

const PAGE_SIZE = 20;
export type NpcInstance = { map_id: number | string; entity_id: number | string; name: unknown; character_key: string };
type InstancePage = { results: NpcInstance[]; total: number };

export function NpcInstances({ characterKey, gameVersion }: { characterKey: string; gameVersion?: string }) {
  const locale = useLocale();
  const [page, setPage] = useState(1);
  const [result, setResult] = useState<InstancePage | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ locale, limit: String(PAGE_SIZE), offset: String((page - 1) * PAGE_SIZE) });
    if (gameVersion) params.set("game_version", gameVersion);
    setResult(null);
    setError("");
    api<InstancePage>(`/npcs/${encodeURIComponent(characterKey)}/instances?${params}`, { signal: controller.signal })
      .then((value) => { if (!controller.signal.aborted) setResult(value); })
      .catch((cause: Error) => { if (!controller.signal.aborted) setError(cause.message); });
    return () => controller.abort();
  }, [characterKey, gameVersion, locale, page, retry]);
  if (result && !result.total) return null;
  return (
    <section className="content-panel full-panel" aria-label="Locations and interactions">
      <h2>Locations and interactions</h2>
      {error ? <ErrorPanel message={error} onRetry={() => setRetry((value) => value + 1)} /> : !result ?
        <p role="status">Loading locations…</p> : <>
          {result.results.map((instance, index) => (
            <div className="plain-row" key={`${instance.character_key}:${instance.map_id}:${instance.entity_id}:${index}`}>
              <span>{localizedText(instance.name, "Unnamed instance")}</span>
              <small>Map {instance.map_id} · Entity {instance.entity_id}</small>
            </div>
          ))}
          {result.total > PAGE_SIZE && <Pagination mode="pages" page={page} pageSize={PAGE_SIZE} total={result.total}
            onPageChange={setPage} ariaLabel="Location pages" />}
        </>}
    </section>
  );
}
