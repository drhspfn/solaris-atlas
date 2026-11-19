"use client";

import { useEffect, useId, useRef, useState } from "react";
import { api, ApiError } from "@/api/client";
import { useAuth } from "@/auth/AuthProvider";
import { ActionMenu } from "@/components/ui/ActionMenu";
import { ExternalLink, Globe, RefreshCw, X } from "lucide-react";

type SyncAction =
  | "sync_facts"
  | "sync_relations"
  | "import_images"
  | "run_visual_analysis";

type RelationItem = {
  id?: number;
  title?: string;
  description?: string;
  relation_type?: string | null;
  object_node_id?: number | null;
  href?: string | null;
};

type FandomStatusPayload = {
  entity: {
    node_id: number;
    canonical_key: string;
    slug?: string | null;
    node_type: string;
    label: string;
  };
  synced: boolean;
  fandom_fact?: {
    id: number;
    title: string;
    summary: string;
    page_url?: string | null;
    gallery_url?: string | null;
    infobox_template?: string | null;
    updated_at?: string | null;
  } | null;
  relations: RelationItem[];
  visual: {
    has_identity: boolean;
    identity_id?: number | null;
    identity_name?: string | null;
    kind?: string | null;
    references_count: number;
    approved_count: number;
    preview_urls: string[];
    admin_url: string;
  };
};

type Props = {
  nodeId?: number;
  canonicalKey?: string;
  entityKind?:
    | "character"
    | "npc"
    | "speaker"
    | "quest"
    | "item"
    | "location"
    | "event"
    | "cutscene";
  menuLabel?: string;
  standalone?: boolean;
};

export function FandomSyncAction({
  nodeId,
  canonicalKey,
  entityKind,
  menuLabel = "Fandom & Visual",
  standalone = true,
}: Props) {
  const { user, refresh: refreshAuth } = useAuth();
  if (user?.role !== "admin" || (!nodeId && !canonicalKey)) return null;
  return (
    <FandomSyncControls
      nodeId={nodeId}
      canonicalKey={canonicalKey}
      entityKind={entityKind}
      menuLabel={menuLabel}
      standalone={standalone}
      onAuthRefresh={refreshAuth}
    />
  );
}

function FandomSyncControls({
  nodeId,
  canonicalKey,
  entityKind,
  menuLabel = "Fandom & Visual",
  standalone,
  onAuthRefresh,
}: Props & { onAuthRefresh: () => Promise<void> }) {
  const id = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [status, setStatus] = useState<FandomStatusPayload | null>(null);
  const [titleOverride, setTitleOverride] = useState("");
  const [licenseFilter, setLicenseFilter] = useState<
    "ignore" | "cc_by_sa_only"
  >("ignore");
  const [selectedActions, setSelectedActions] = useState<SyncAction[]>([
    "sync_facts",
  ]);

  useEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);

  async function loadStatus() {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams();
      if (nodeId) params.set("node_id", String(nodeId));
      else if (canonicalKey) params.set("canonical_key", canonicalKey);
      const data = await api<FandomStatusPayload>(
        `/admin/fandom/status?${params.toString()}`,
      );
      setStatus(data);
    } catch (e) {
      if (e instanceof ApiError && [401, 403].includes(e.status)) {
        void onAuthRefresh();
      }
      setError(
        e instanceof Error ? e.message : "Failed to load Fandom sync status.",
      );
    } finally {
      setLoading(false);
    }
  }

  function openModal() {
    setNotice("");
    setOpen(true);
    void loadStatus();
  }

  function toggleAction(action: SyncAction) {
    setSelectedActions((prev) =>
      prev.includes(action)
        ? prev.filter((item) => item !== action)
        : [...prev, action],
    );
  }

  async function runSync() {
    if (!selectedActions.length) {
      setError("Select at least one sync action.");
      return;
    }
    setSyncing(true);
    setError("");
    setNotice("");
    try {
      const response = await api<{
        sync_result: {
          page_url: string;
          facts_count: number;
          edges_count: number;
          imported_references: number[];
          queued_jobs?: unknown[];
        };
        status: FandomStatusPayload;
      }>("/admin/fandom/sync", {
        method: "POST",
        body: {
          node_id: nodeId ?? null,
          canonical_key: canonicalKey ?? null,
          entity_kind: entityKind === "cutscene" ? null : (entityKind ?? null),
          page_title_override: titleOverride.trim() || null,
          actions: selectedActions,
          license_filter: licenseFilter,
        },
      });
      setStatus(response.status);
      const r = response.sync_result;
      setNotice(
        `Synced: ${r.facts_count} facts, ${r.edges_count} graph edges, ${r.imported_references.length} visual references imported.`,
      );
    } catch (e) {
      if (e instanceof ApiError && [401, 403].includes(e.status)) {
        void onAuthRefresh();
      }
      setError(e instanceof Error ? e.message : "Fandom sync failed.");
    } finally {
      setSyncing(false);
    }
  }

  const triggerButton = (
    <button type="button" aria-haspopup="dialog" onClick={openModal}>
      <Globe size={15} aria-hidden="true" /> Fandom sync
    </button>
  );

  return (
    <>
      {standalone ? (
        <ActionMenu label={menuLabel}>{triggerButton}</ActionMenu>
      ) : (
        triggerButton
      )}
      <dialog
        ref={dialogRef}
        className="character-model-dialog character-model-import-dialog"
        aria-labelledby={`${id}-title`}
        onCancel={(event) => {
          event.preventDefault();
          setOpen(false);
        }}
        onClose={() => setOpen(false)}
      >
        <header className="character-model-dialog-heading">
          <h2 id={`${id}-title`}>Fandom Sync &amp; Visual Memory</h2>
          <button
            type="button"
            autoFocus
            aria-label="Close Fandom sync"
            onClick={() => setOpen(false)}
          >
            <X size={20} aria-hidden="true" />
          </button>
        </header>

        <div style={{ display: "grid", gap: "0.9rem", paddingTop: "0.5rem" }}>
          {loading && <p>Loading current sync &amp; visual status…</p>}
          {error && <p role="alert">{error}</p>}
          {notice && <p role="status">{notice}</p>}

          {status && (
            <>
              <div
                style={{
                  border: "1px solid var(--border, #2a2f3a)",
                  borderRadius: "8px",
                  padding: "0.75rem",
                  display: "grid",
                  gap: "0.4rem",
                }}
              >
                <strong>
                  {status.entity.label} ({status.entity.node_type})
                </strong>
                {status.synced && status.fandom_fact ? (
                  <>
                    <div
                      style={{
                        display: "flex",
                        gap: "0.75rem",
                        flexWrap: "wrap",
                      }}
                    >
                      {status.fandom_fact.page_url && (
                        <a
                          href={status.fandom_fact.page_url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          Fandom page <ExternalLink size={13} />
                        </a>
                      )}
                      {status.fandom_fact.gallery_url && (
                        <a
                          href={status.fandom_fact.gallery_url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          Gallery <ExternalLink size={13} />
                        </a>
                      )}
                    </div>
                    <p style={{ margin: 0, fontSize: "0.9rem", opacity: 0.9 }}>
                      {status.fandom_fact.summary}
                    </p>
                  </>
                ) : (
                  <p style={{ margin: 0, fontSize: "0.9rem", opacity: 0.8 }}>
                    Not synced with Fandom yet.
                  </p>
                )}
              </div>

              <div
                style={{
                  border: "1px solid var(--border, #2a2f3a)",
                  borderRadius: "8px",
                  padding: "0.75rem",
                  display: "grid",
                  gap: "0.45rem",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                  }}
                >
                  <strong>Visual Memory</strong>
                  <a
                    href={status.visual.admin_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Open Visual Memory
                  </a>
                </div>
                {status.visual.has_identity ? (
                  <>
                    <p style={{ margin: 0, fontSize: "0.9rem" }}>
                      Identity #{status.visual.identity_id} (
                      {status.visual.kind}) · {status.visual.references_count}{" "}
                      refs ({status.visual.approved_count} approved)
                    </p>
                    {status.visual.preview_urls.length > 0 && (
                      <div
                        style={{
                          display: "flex",
                          gap: "0.4rem",
                          flexWrap: "wrap",
                        }}
                      >
                        {status.visual.preview_urls.map((src) => (
                          <img
                            key={src}
                            src={src}
                            alt="Visual reference"
                            style={{
                              width: 56,
                              height: 56,
                              objectFit: "cover",
                              borderRadius: 6,
                            }}
                          />
                        ))}
                      </div>
                    )}
                  </>
                ) : (
                  <p style={{ margin: 0, fontSize: "0.9rem", opacity: 0.8 }}>
                    No Visual Identity linked yet.
                  </p>
                )}
              </div>

              {status.relations.length > 0 && (
                <div
                  style={{
                    border: "1px solid var(--border, #2a2f3a)",
                    borderRadius: "8px",
                    padding: "0.75rem",
                    display: "grid",
                    gap: "0.35rem",
                  }}
                >
                  <strong>
                    Synced Fandom Relations ({status.relations.length})
                  </strong>
                  <ul
                    style={{
                      margin: 0,
                      paddingLeft: "1.2rem",
                      fontSize: "0.9rem",
                    }}
                  >
                    {status.relations.slice(0, 12).map((rel, idx) => (
                      <li key={rel.id ?? idx}>
                        {rel.href ? (
                          <a href={rel.href}>{rel.title}</a>
                        ) : (
                          <span>{rel.title}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}

          <fieldset
            style={{
              border: "1px solid var(--border, #2a2f3a)",
              borderRadius: "8px",
              padding: "0.75rem",
              display: "grid",
              gap: "0.45rem",
            }}
          >
            <legend>Actions to run</legend>
            <label
              style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}
            >
              <input
                type="checkbox"
                checked={selectedActions.includes("sync_facts")}
                onChange={() => toggleAction("sync_facts")}
              />
              Sync Fandom page &amp; facts (default)
            </label>
            <label
              style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}
            >
              <input
                type="checkbox"
                checked={selectedActions.includes("sync_relations")}
                onChange={() => toggleAction("sync_relations")}
              />
              Update graph relations (infobox → DB edges)
            </label>
            <label
              style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}
            >
              <input
                type="checkbox"
                checked={selectedActions.includes("import_images")}
                onChange={() => toggleAction("import_images")}
              />
              Import gallery images into Visual Memory
            </label>
            {(entityKind === "cutscene" ||
              status?.entity.node_type === "cutscene") && (
              <label
                style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}
              >
                <input
                  type="checkbox"
                  checked={selectedActions.includes("run_visual_analysis")}
                  onChange={() => toggleAction("run_visual_analysis")}
                />
                Queue cutscene visual analysis
              </label>
            )}
            <label
              style={{ display: "grid", gap: "0.25rem", marginTop: "0.25rem" }}
            >
              <span>License filter</span>
              <select
                value={licenseFilter}
                onChange={(e) =>
                  setLicenseFilter(
                    e.target.value === "cc_by_sa_only"
                      ? "cc_by_sa_only"
                      : "ignore",
                  )
                }
              >
                <option value="ignore">
                  Ignore license filter (save metadata)
                </option>
                <option value="cc_by_sa_only">Only CC-BY-SA compatible</option>
              </select>
            </label>
            <label style={{ display: "grid", gap: "0.25rem" }}>
              <span>Optional Fandom page title override</span>
              <input
                type="text"
                value={titleOverride}
                onChange={(e) => setTitleOverride(e.target.value)}
                placeholder="Leave blank to match by entity name"
              />
            </label>
          </fieldset>

          <div
            style={{
              display: "flex",
              gap: "0.5rem",
              justifyContent: "flex-end",
            }}
          >
            <button type="button" onClick={() => setOpen(false)}>
              Cancel
            </button>
            <button
              type="button"
              disabled={syncing || loading}
              onClick={() => void runSync()}
            >
              <RefreshCw size={15} aria-hidden="true" />{" "}
              {syncing ? "Syncing…" : "Run Fandom Sync"}
            </button>
          </div>
        </div>
      </dialog>
    </>
  );
}
