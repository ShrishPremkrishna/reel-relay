import {
  cloneElement,
  isValidElement,
  useId,
  type ButtonHTMLAttributes,
  type ReactNode,
} from 'react';
import { AlertCircle, CheckCircle2, Film, LoaderCircle } from 'lucide-react';
import type { Tone } from '../lib/domain';

export function Logo() {
  return (
    <span className="brand">
      <span className="brand-icon">
        <Film size={23} strokeWidth={2.2} />
      </span>
      <span>
        reel relay<span className="brand-sub">YOUR LOCAL CLIP STUDIO</span>
      </span>
    </span>
  );
}
export function Button({
  children,
  variant = 'secondary',
  busy = false,
  className = '',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  busy?: boolean;
}) {
  return (
    <button
      {...props}
      disabled={props.disabled || busy}
      className={`button button-${variant} ${className}`}
      aria-busy={busy || undefined}
    >
      {busy && <LoaderCircle size={16} className="spin" aria-hidden="true" />}
      {children}
    </button>
  );
}
export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: Tone }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}
export function Notice({
  children,
  tone = 'warning',
  action,
}: {
  children: ReactNode;
  tone?: 'warning' | 'danger' | 'success' | 'neutral';
  action?: ReactNode;
}) {
  return (
    <div className={`notice notice-${tone}`} role={tone === 'danger' ? 'alert' : undefined}>
      {tone === 'success' ? (
        <CheckCircle2 size={18} aria-hidden="true" />
      ) : (
        <AlertCircle size={18} aria-hidden="true" />
      )}
      <div>{children}</div>
      {action}
    </div>
  );
}
export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span className="empty-icon" aria-hidden="true">
        {icon}
      </span>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}
export function PageHeader({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <header className="page-heading">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p className="page-description">{description}</p>
      </div>
      {action}
    </header>
  );
}
export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  const id = useId();
  const control = isValidElement<Record<string, unknown>>(children)
    ? cloneElement(children, {
        'aria-labelledby': children.props['aria-labelledby'] || `${id}-label`,
        'aria-describedby': hint ? `${id}-help` : children.props['aria-describedby'],
      })
    : children;
  return (
    <label className="field">
      <span id={`${id}-label`}>{label}</span>
      {control}
      {hint && <small id={`${id}-help`}>{hint}</small>}
    </label>
  );
}
export function Section({
  title,
  description,
  children,
  id,
  action,
}: {
  title: string;
  description?: string;
  children: ReactNode;
  id?: string;
  action?: ReactNode;
}) {
  return (
    <section className="panel section-panel" id={id}>
      <header className="section-heading">
        <div>
          <h2>{title}</h2>
          {description && <p>{description}</p>}
        </div>
        {action}
      </header>
      {children}
    </section>
  );
}
