

export function PageLoader() {
  return (
    <div className="page-container">
      <div className="loading-detail">
        <div />
        <span>Opening archive record…</span>
      </div>
    </div>
  );
}

export function ErrorPanel({ message }: { message: string }) {
  return (
    <div className="error-panel">
      <span>Archive connection interrupted</span>
      <p>{message}</p>
      <small>Make sure the API and database services are running.</small>
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
