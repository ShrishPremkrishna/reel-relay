import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';
import { Field } from '../primitives';

export function Advanced({
  title,
  description,
  children,
  open = false,
}: {
  title: string;
  description?: string;
  children: ReactNode;
  open?: boolean;
}) {
  return (
    <details className="disclosure advanced-settings" open={open}>
      <summary>
        {title}
        <ChevronDown size={16} />
      </summary>
      {description && <p className="field-help">{description}</p>}
      <div className="advanced-body">{children}</div>
    </details>
  );
}

export function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
  hint,
  disabled = false,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min: number;
  max: number;
  step?: number;
  hint?: string;
  disabled?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [text, setText] = useState(String(value));
  useEffect(() => {
    if (document.activeElement !== input.current)
      setText(Number.isFinite(value) ? String(value) : '');
  }, [value]);
  return (
    <Field label={label} hint={hint}>
      <input
        ref={input}
        type="number"
        value={text}
        min={min}
        max={max}
        step={step}
        required
        disabled={disabled}
        onChange={(e) => {
          setText(e.target.value);
          onChange(e.target.value === '' ? NaN : e.currentTarget.valueAsNumber);
        }}
      />
    </Field>
  );
}
export function StringListField({
  label,
  value,
  onChange,
  hint,
}: {
  label: string;
  value: string[];
  onChange: (value: string[]) => void;
  hint?: string;
}) {
  const input = useRef<HTMLTextAreaElement>(null);
  const [text, setText] = useState(value.join(', '));
  useEffect(() => {
    if (document.activeElement !== input.current) setText(value.join(', '));
  }, [value]);
  return (
    <Field label={label} hint={hint}>
      <textarea
        ref={input}
        value={text}
        rows={2}
        onChange={(e) => {
          setText(e.target.value);
          onChange(
            Array.from(
              new Set(
                e.target.value
                  .split(/[,\n]/)
                  .map((s) => s.trim())
                  .filter(Boolean),
              ),
            ),
          );
        }}
      />
    </Field>
  );
}
export function CommandField({
  label,
  value,
  onChange,
  onInvalidEdit,
}: {
  label: string;
  value: string[];
  onChange: (value: string[]) => void;
  onInvalidEdit: () => void;
}) {
  const input = useRef<HTMLTextAreaElement>(null);
  const [text, setText] = useState(JSON.stringify(value));
  useEffect(() => {
    if (document.activeElement !== input.current) {
      setText(JSON.stringify(value));
      input.current?.setCustomValidity('');
    }
  }, [value]);
  return (
    <Field
      label={label}
      hint="Advanced: a JSON array containing a trusted executable and its arguments. No shell expansion is performed."
    >
      <textarea
        ref={input}
        rows={3}
        spellCheck={false}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          try {
            const parsed: unknown = JSON.parse(e.target.value);
            if (!Array.isArray(parsed) || !parsed.every((v) => typeof v === 'string'))
              throw new Error();
            e.target.setCustomValidity('');
            onChange(parsed);
          } catch {
            e.target.setCustomValidity(
              'Enter a JSON array of strings, such as ["/path/to/editor", "--json"].',
            );
            onInvalidEdit();
          }
        }}
      />
    </Field>
  );
}
export function Toggle({
  label,
  description,
  checked,
  onChange,
  disabled = false,
}: {
  label: string;
  description?: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label className="settings-toggle">
      <input
        type="checkbox"
        aria-label={label}
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>
        <strong>{label}</strong>
        {description && <small>{description}</small>}
      </span>
    </label>
  );
}
