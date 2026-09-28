

export function PanelTitle({
  number,
  title,
  count,
}: {
  number: string;
  title: string;
  count?: number;
}) {
  return (
    <div className="panel-title">
      <span>{number}</span>
      <h2>{title}</h2>
      {count != null && <small>{count}</small>}
    </div>
  );
}
