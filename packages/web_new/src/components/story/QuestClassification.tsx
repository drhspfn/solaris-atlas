export type QuestClassificationData = {
  kind: string;
  label: string;
  explanation: string;
  internal_name?: string | null;
  source?: { file: string; row: number; release_id: number };
  evidence?: { field: string; value: unknown }[];
};

export function QuestClassification({ value }: { value?: QuestClassificationData }) {
  if (!value || value.kind === 'unclassified') return null;
  return (
    <aside className="subsection quest-classification" aria-label="Quest classification">
      <strong>{value.label}</strong>
      <p>{value.explanation}</p>
      {value.internal_name && <p>Internal name: {value.internal_name}</p>}
      {value.source && (
        <details>
          <summary>Classification evidence</summary>
          {value.evidence?.map((entry) => (
            <p className="source-caption" key={entry.field}>
              {entry.field}: {String(entry.value)}
            </p>
          ))}
          <p className="source-caption">
            Source: {value.source.file}, row {value.source.row}
          </p>
        </details>
      )}
    </aside>
  );
}
