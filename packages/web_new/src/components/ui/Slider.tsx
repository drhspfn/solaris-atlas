'use client';

import { forwardRef, type InputHTMLAttributes, type ReactNode } from 'react';
import '@/styles/ui-controls.css';

export type SliderProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> & { label?: ReactNode };
export const Slider = forwardRef<HTMLInputElement, SliderProps>(function Slider({ label, className = '', ...props }, ref) {
  const control = <input {...props} ref={ref} type="range" className={`ui-slider ${className}`} />;
  return label == null ? control : <label className="ui-slider-field"><span>{label}</span>{control}</label>;
});
