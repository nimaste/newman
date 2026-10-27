import { useEffect, useRef, useState } from 'react';
import { useAuth } from '../auth.jsx';
import { useHub } from '../hooks.js';
import { cleanDetail, fmtUptime } from '../format.js';
import newmanIcon from '../assets/newman-icon-128.png';
import { cameraTiles } from './CameraGrid.jsx';

const NAV = [
  { id: 'overview', label: 'Overview' },
  { id: 'devices', label: 'Devices', count: (h) => h.entities && h.entities.length },
  // Only shown once there are cameras.
  { id: 'cameras', label: 'Cameras', count: (h) => h.entities && h.kindsById && cameraTiles(h.entities, h.kindsById).length, hideWhenEmpty: true },
  { id: 'automations', label: 'Automations', count: (h) => h.status && h.status.automations },
  { id: 'activity', label: 'Activity' },
  { id: 'dashboards', label: 'Dashboards' },
  { id: 'integrations', label: 'Connections', count: (h) => h.status && h.status.integrations && h.status.integrations.length },
  // One hub = one household: its people, where they are, their phones, places and accounts.
  { id: 'household', label: 'Household', count: (h) => h.status && h.status.people, also: ['people', 'phones', 'test-phones'] },
  // How the whole house behaves (battery warnings, hold-to-dim, climate colours, cameras, history).
  { id: 'house', label: 'Settings' },
  // (Profile and Sign-in settings live in the menu under your name.)
];

function count(v) {
  if (Array.isArray(v)) return v.length;
  return typeof v === 'number' ? v : '';
}

/** Connections that need attention: failed ones first, then ones waiting to pair. */
export function connectionProblems(status) {
  return ((status && status.integrations) || []).filter((i) => i.status === 'error' || i.status === 'pairing');
}

/** You, at the bottom of the sidebar: Profile, Sign-in settings and Backups (admins), and Sign out. */
function UserMenu({ page }) {
  const { user, isAdmin, signOut } = useAuth();
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
  if (!user) return null;
  const go = () => setOpen(false);
  return (
    <div className="user-menu" ref={ref}>
      {open ? (
        <div className="user-menu-list" role="menu">
          <a role="menuitem" className="user-menu-item" href="#/profile" onClick={go} aria-current={page === 'profile' ? 'page' : undefined}>
            Profile
          </a>
          {isAdmin ? (
            <a role="menuitem" className="user-menu-item" href="#/settings" onClick={go} aria-current={page === 'settings' ? 'page' : undefined}>
              Sign-in settings
            </a>
          ) : null}
          {isAdmin ? (
            <a role="menuitem" className="user-menu-item" href="#/backups" onClick={go} aria-current={page === 'backups' ? 'page' : undefined}>
              Backups
            </a>
          ) : null}
          <button type="button" role="menuitem" className="user-menu-item" onClick={() => { setOpen(false); signOut(); }}>
            Sign out
          </button>
        </div>
      ) : null}
      <button type="button" className="user-menu-btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className="avatar avatar--sm avatar--on" aria-hidden="true">
          {(user.name || user.username || '?').charAt(0).toUpperCase()}
        </span>
        <span className="user-menu-name">{user.name || user.username}</span>
        <span className="user-menu-role">{user.role === 'admin' ? 'Admin' : 'Family'}</span>
      </button>
    </div>
  );
}

export default function Sidebar({ page }) {
  const hub = useHub();
  const { status, hubDown } = hub;

  // One line of health, not a list of every connection.
  let dot = '';
  let text = 'Connecting…';
  let title;
  if (hubDown) {
    dot = 'dot--down';
    text = "Can't reach the hub";
  } else if (status) {
    const problems = connectionProblems(status);
    const errors = problems.filter((i) => i.status === 'error');
    dot = errors.length ? 'dot--down' : problems.length ? 'dot--warn' : 'dot--ok';
    text = problems.length ? (problems.length === 1 ? '1 problem' : `${problems.length} problems`) : 'All systems OK';
    title = [
      ...problems.map((i) => `${i.name}: ${i.status === 'pairing' ? 'waiting to pair' : cleanDetail(i.detail) || 'not working'}`),
      `Newman ${status.version || ''}${status.uptime !== undefined ? ' · up ' + fmtUptime(status.uptime) : ''}`,
    ].join('\n');
  }
  const pairing = (!hubDown && status && (status.integrations || []).filter((i) => i.status === 'pairing')) || [];

  return (
    <nav className="sidebar" aria-label="Main">
      <a className="brand" href="#/overview">
        <img className="brand-mark" src={newmanIcon} width="34" height="34" alt="" aria-hidden="true" />
        <span className="brand-name">Newman</span>
      </a>
      <div className="nav">
        {NAV.filter((n) => !n.hideWhenEmpty || n.count(hub)).map((n) => (
          <a
            key={n.id}
            className="nav-item"
            href={'#/' + n.id}
            aria-current={page === n.id || (n.also || []).includes(page) ? 'page' : undefined}
          >
            <span className="nav-label">{n.label}</span>
            {n.count ? <span className="nav-count">{count(n.count(hub))}</span> : null}
          </a>
        ))}
      </div>
      <div style={{ flexGrow: 1 }} />
      {pairing.length > 1 ? (
        <a className="pairing-side" href={'#/integrations/' + encodeURIComponent(pairing[0].id)} role="status" aria-live="polite">
          <span className="pairing-side-head">
            <span className="dot dot--pairing" aria-hidden="true" />
            {pairing.length} connections waiting
          </span>
          <span className="pairing-side-text">{pairing.map((i) => i.name).join(', ')}</span>
        </a>
      ) : null}
      {(pairing.length > 1 ? [] : pairing).map((i) => (
        <a key={i.id} className="pairing-side" href={'#/integrations/' + encodeURIComponent(i.id)} role="status" aria-live="polite">
          <span className="pairing-side-head">
            <span className="dot dot--pairing" aria-hidden="true" />
            Pairing {i.name}
          </span>
          <span className="pairing-side-text">{cleanDetail(i.detail) || 'Waiting for the device…'}</span>
        </a>
      ))}
      <a className="health-line" href="#/integrations" role="status" aria-live="polite" title={title}>
        <span className={'dot ' + dot} aria-hidden="true" />
        {text}
      </a>
      <UserMenu page={page} />
    </nav>
  );
}
