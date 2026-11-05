'use client';

import { forwardRef, type InputHTMLAttributes } from 'react';
import '@/styles/ui-controls.css';

export type InputProps = InputHTMLAttributes<HTMLInputElement>;
export const Input = forwardRef<HTMLInputElement, InputProps>(function Input({ className = '', ...props }, ref) {
  return <input {...props} ref={ref} className={`ui-input ${className}`} />;
});
