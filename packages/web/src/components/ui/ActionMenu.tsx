import '../../styles/action-menu.css';

import { MoreVertical } from 'lucide-react';
import { type ReactNode, useEffect, useRef } from 'react';

export function ActionMenu({ label, children }: { label: string; children: ReactNode }) {
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    function close(event: PointerEvent | KeyboardEvent) {
      const details = ref.current;
      if (!details?.open) return;
      if (event instanceof KeyboardEvent) {
        if (event.key !== 'Escape') return;
        event.preventDefault();
        details.querySelector('summary')?.focus();
      } else if (event.target instanceof Node && details.contains(event.target)) return;
      details.open = false;
    }
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', close);
    return () => {
      document.removeEventListener('pointerdown', close);
      document.removeEventListener('keydown', close);
    };
  }, []);
  return (
    <details className="action-menu" ref={ref}>
      <summary aria-label={label} title={label}>
        <MoreVertical size={20} aria-hidden="true" />
      </summary>
      <div className="action-menu-panel">{children}</div>
    </details>
  );
}
