import { useEffect, useRef, useState } from 'react';
import { Icon } from '../icons.jsx';

/** On/off switch. `label` is visible text; `ariaLabel` overrides it for screen readers. */
export function Switch({ checked, onChange, label, ariaLabel, disabled, className = '' }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={!!checked}
      aria-label={ariaLabel}
      disabled={disabled}
      className={'switch ' + className}
      onClick={() => onChange(!checked)}
    >
      <span className="switch-track" aria-hidden="true">
        <span className="switch-knob" />
      </span>
      {label}
    </button>
  );
}

/** Checkbox styled like the design's rounded square. */
export function Check({ checked, onChange, children, disabled, strong }) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={!!checked}
      disabled={disabled}
      className={'check' + (strong ? ' check--strong' : '')}
      onClick={() => onChange(!checked)}
    >
      <span className="box" aria-hidden="true">
        {checked ? <Icon name="check" size={14} strokeWidth={3} /> : null}
      </span>
      <span>{children}</span>
    </button>
  );
}

export function Chip({ on, onClick, children, icon, sensitive, disabled, title }) {
  return (
    <button
      type="button"
      className={'chip' + (sensitive ? ' chip--sensitive' : '')}
      aria-pressed={!!on}
      onClick={onClick}
      disabled={disabled}
      title={title}
    >
      {icon ? <Icon name={icon} size={15} strokeWidth={2.2} /> : null}
      {children}
    </button>
  );
}

/** Radio-like segmented control. options: [{id, label, disabled?}] */
export function Segmented({ value, options, onChange, label }) {
  return (
    <div className="seg" role="radiogroup" aria-label={label} style={{ gridTemplateColumns: `repeat(${options.length}, minmax(0, 1fr))` }}>
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          role="radio"
          aria-checked={value === o.id}
          disabled={o.disabled}
          title={o.title}
          onClick={() => onChange(o.id)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Loading({ children = 'Loading…' }) {
  return (
    <div className="loading" role="status">
      <span className="spinner" aria-hidden="true" />
      {children}
    </div>
  );
}

export function Empty({ title, children, action }) {
  return (
    <div className="empty">
      {title ? <div className="empty-title">{title}</div> : null}
      {children ? <div>{children}</div> : null}
      {action || null}
    </div>
  );
}

export function ErrorBox({ error, onRetry, children }) {
  return (
    <div className="error-box" role="alert">
      <span>{children || (error && error.message) || 'Something went wrong.'}</span>
      {onRetry ? (
        <button type="button" className="btn btn-sm" onClick={onRetry}>
          <Icon name="refresh" size={15} /> Retry
        </button>
      ) : null}
    </div>
  );
}

/** Loading / error / content switch for a useApi() result. */
export function Async({ res, children, loadingText, errorText }) {
  if (res.data === undefined) {
    if (res.error) return <ErrorBox error={res.error} onRetry={res.reload}>{errorText}</ErrorBox>;
    return <Loading>{loadingText}</Loading>;
  }
  return children(res.data);
}

/** Tab strip (role=tablist). tabs: [{id, label}] */
export function Tabs({ tabs, value, onChange, label }) {
  return (
    <div className="tabstrip" role="tablist" aria-label={label}>
      {tabs.map((t) => (
        <button key={t.id} type="button" role="tab" aria-selected={value === t.id} className="tabstrip-tab" onClick={() => onChange(t.id)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

/** Centered dialog for rare or multi-step tasks. Esc / backdrop closes. */
export function Modal({ title, onClose, children, wide, footer }) {
  const box = useRef(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const last = document.activeElement;
    const first =
      box.current && (box.current.querySelector('.app-modal-body input, .app-modal-body select, .app-modal-body button') || box.current.querySelector('button'));
    first && first.focus();
    const onKey = (e) => e.key === 'Escape' && closeRef.current();
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      if (last && last.focus) last.focus();
    };
  }, []);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={box} className={'modal app-modal' + (wide ? ' app-modal--wide' : '')} role="dialog" aria-modal="true" aria-label={title}>
        <div className="app-modal-head">
          <h2 className="modal-title">{title}</h2>
          <button type="button" className="icon-btn icon-btn--lg" aria-label="Close" onClick={onClose}>
            <Icon name="x" size={18} />
          </button>
        </div>
        <div className="app-modal-body">{children}</div>
        {footer ? <div className="app-modal-foot">{footer}</div> : null}
      </div>
    </div>
  );
}

/** "⋯" button with a small menu of rare actions. items: [{label, onClick, danger}] */
export function MoreMenu({ items, label = 'More' }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => ref.current && !ref.current.contains(e.target) && setOpen(false);
    const onKey = (e) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('mousedown', onDoc);
    window.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      window.removeEventListener('keydown', onKey);
    };
  }, [open]);
  return (
    <div className="more" ref={ref}>
      <button type="button" className="btn more-btn" aria-label={label} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        ⋯
      </button>
      {open ? (
        <div className="more-menu" role="menu">
          {items.map((it) => (
            <button
              key={it.label}
              type="button"
              role="menuitem"
              className={'more-item' + (it.danger ? ' more-item--danger' : '')}
              disabled={it.disabled}
              onClick={() => {
                setOpen(false);
                it.onClick();
              }}
            >
              {it.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}