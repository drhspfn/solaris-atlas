'use client';

import { forwardRef, type InputHTMLAttributes, type ReactNode } from 'react';
import '@/styles/ui-controls.css';

export type CheckboxProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> & { label?: ReactNode };
export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox({ label, className = '', ...props }, ref) {
  const control = <input {...props} ref={ref} type="checkbox" className={`ui-checkbox ${className}`} />;
  return label == null ? control : <label className={`ui-checkbox-field${props.disabled ? ' ui-field-disabled' : ''}`}>{control}<span>{label}</span></label>;
});
