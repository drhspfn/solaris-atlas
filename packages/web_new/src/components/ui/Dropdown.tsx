'use client';

import type { ReactNode } from 'react';
import { ActionMenu } from './ActionMenu';

/** Disclosure dropdown using the same Escape/outside-dismissal owner as action menus. */
export function Dropdown({ label, trigger, children }: { label: string; trigger?: ReactNode; children: ReactNode }) {
  return <ActionMenu label={label} trigger={trigger}>{children}</ActionMenu>;
}
