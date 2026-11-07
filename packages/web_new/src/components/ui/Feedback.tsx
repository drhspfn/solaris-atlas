import { Skeleton, SkeletonGroup, TextSkeleton } from "./Skeleton";

export function PageLoader() {
  return (
    <div className="page-container">
      <SkeletonGroup label="Opening archive record" className="page-skeleton">
        <Skeleton style={{ width: "28%", height: ".7rem" }} />
        <Skeleton style={{ width: "62%", height: "2.5rem" }} />
        <TextSkeleton lines={2} />
        <div className="page-skeleton-panels">
          {[0, 1, 2].map((index) => (
            <div className="page-skeleton-panel" key={index}>
              <Skeleton style={{ width: "45%", height: "1.25rem" }} />
              <TextSkeleton lines={4} />
            </div>
          ))}
        </div>
      </SkeletonGroup>
    </div>
  );
}

export function ErrorPanel({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="error-panel" role="alert">
      <span>
        {message.includes("This archive selection is being prepared.")
          ? "Archive is being prepared"
          : "Archive connection interrupted"}
      </span>
      <p>{message}</p>
      <small>Try again in a moment.</small>
      {onRetry && (
        <button type="button" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function EmptyInline({ text }: { text: string }) {
  return <div className="empty-inline">{text}</div>;
}

export function EmptyState({ query }: { query: string }) {
  return (
    <div className="empty-state">
      <div>⌕</div>
      <h3>{query ? "No matching entries" : "Nothing here yet"}</h3>
      <p>
        {query
          ? "Try another name or switch the category filter."
          : "This collection has no entries in the active snapshot."}
      </p>
    </div>
  );
}
