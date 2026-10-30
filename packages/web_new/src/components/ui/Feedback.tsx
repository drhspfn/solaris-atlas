export function PageLoader() {
  return (
    <div className="page-container">
      <div className="loading-detail" role="status">
        <div />
        <span>Opening archive record…</span>
      </div>
    </div>
  );
}

export function ErrorPanel({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="error-panel" role="alert">
      <span>{message.includes('This archive selection is being prepared.')
        ? 'Archive is being prepared'
        : 'Archive connection interrupted'}</span>
      <p>{message}</p>
      <small>Try again in a moment.</small>
      {onRetry && <button type="button" onClick={onRetry}>Try again</button>}
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
      <h3>{query ? 'No matching entries' : 'Nothing here yet'}</h3>
      <p>
        {query
          ? 'Try another name or switch the category filter.'
          : 'This collection has no entries in the active snapshot.'}
      </p>
    </div>
  );
}
