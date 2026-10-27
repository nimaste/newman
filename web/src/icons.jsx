// Inline stroke icons (24x24 grid, currentColor). No emoji, no icon font.

const PATHS = {
  lock: (
    <>
      <rect x="5" y="11" width="14" height="10" rx="2" />
      <path d="M8 11V7a4 4 0 0 1 8 0v4" />
    </>
  ),
  unlock: (
    <>
      <rect x="5" y="11" width="14" height="10" rx="2" />
      <path d="M8 11V7a4 4 0 0 1 7.5-2" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  x: <path d="M6 6l12 12M18 6L6 18" />,
  check: <path d="M5 12l5 5 9-10" />,
  plus: <path d="M12 5v14M5 12h14" />,
  minus: <path d="M5 12h14" />,
  recipe: (
    <>
      <path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z" />
      <path d="M5 17a3 3 0 0 1 3-3h11M9 8h6" />
    </>
  ),
  pencil: (
    <>
      <path d="M4 20h4L19 9l-4-4L4 16z" />
      <path d="M13.5 6.5l4 4" />
    </>
  ),
  trash: (
    <>
      <path d="M4 7h16M10 11v6M14 11v6" />
      <path d="M6 7l1 13h10l1-13M9 7V4h6v3" />
    </>
  ),
  chevronRight: <path d="M9 6l6 6-6 6" />,
  chevronLeft: <path d="M15 6l-6 6 6 6" />,
  play: <path d="M7 5v14l11-7z" />,
  skipNext: <path d="M5 5v14l10-7zM19 5v14" />,
  skipBack: <path d="M19 5v14L9 12zM5 5v14" />,
  shuffle: <path d="M16 3h5v5M4 20 21 3M21 16v5h-5M15 15l6 6M4 4l5 5" />,
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="M20 20l-4-4" />
    </>
  ),
  music: (
    <>
      <path d="M9 18V5l11-2v13" />
      <circle cx="6" cy="18" r="3" />
      <circle cx="17" cy="16" r="3" />
    </>
  ),
  alert: (
    <>
      <path d="M12 3l10 18H2z" />
      <path d="M12 10v4M12 17.5v.5" />
    </>
  ),
  bell: (
    <>
      <path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z" />
      <path d="M10 20a2 2 0 0 0 4 0" />
    </>
  ),
  shield: <path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" />,
  shieldOff: (
    <>
      <path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" />
      <path d="M4 4l16 16" />
    </>
  ),
  pin: (
    <>
      <path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z" />
      <circle cx="12" cy="10" r="2.4" />
    </>
  ),
  home: (
    <>
      <path d="M4 11l8-7 8 7" />
      <path d="M6 10v10h12V10" />
    </>
  ),
  door: (
    <>
      <path d="M6 21V4h12v17M3 21h18" />
      <path d="M14 12v1" />
    </>
  ),
  doorOpen: (
    <>
      <path d="M6 21V4h12v17M3 21h18" />
      <path d="M6 4l7 2v15" />
    </>
  ),
  garage: (
    <>
      <path d="M3 21V9l9-5 9 5v12" />
      <path d="M7 21v-8h10v8M7 16h10" />
    </>
  ),
  light: (
    <>
      <path d="M9 18h6M10 21h4" />
      <path d="M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z" />
    </>
  ),
  power: (
    <>
      <path d="M12 3v9" />
      <path d="M6.3 7a8 8 0 1 0 11.4 0" />
    </>
  ),
  camera: (
    <>
      <path d="M4 8h3l2-3h6l2 3h3v11H4z" />
      <circle cx="12" cy="13" r="3.5" />
    </>
  ),
  phone: (
    <>
      <rect x="7" y="2.5" width="10" height="19" rx="2.5" />
      <path d="M11 18.5h2" />
    </>
  ),
  people: (
    <>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M2.5 20a6.5 6.5 0 0 1 13 0" />
      <path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14a6.5 6.5 0 0 1 3.5 6" />
    </>
  ),
  plug: (
    <>
      <path d="M9 3v5M15 3v5" />
      <path d="M6 8h12v3a6 6 0 0 1-12 0z" />
      <path d="M12 17v4" />
    </>
  ),
  bolt: <path d="M13 2L4 14h7l-1 8 9-12h-7z" />,
  grid: (
    <>
      <rect x="4" y="4" width="7" height="7" rx="1.5" />
      <rect x="13" y="4" width="7" height="7" rx="1.5" />
      <rect x="4" y="13" width="7" height="7" rx="1.5" />
      <rect x="13" y="13" width="7" height="7" rx="1.5" />
    </>
  ),
  list: <path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01" />,
  sliders: <path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4" />,
  thermometer: (
    <>
      <path d="M10 14V5a2 2 0 0 1 4 0v9" />
      <circle cx="12" cy="17" r="3.5" />
    </>
  ),
  motion: (
    <>
      <circle cx="13" cy="4.5" r="1.8" />
      <path d="M9 21l2.5-6 2.5 2v4M8 11l3-3 3 2 3 1M11 8l-1.5 5" />
    </>
  ),
  vacuum: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M5.6 8.2a7.6 7.6 0 0 1 12.8 0" />
      <circle cx="12" cy="8" r="1.6" />
      <path d="M9 15.5h6" />
    </>
  ),
  pause: <path d="M8 5v14M16 5v14" />,
  open: (
    <>
      <path d="M14 4h6v6M20 4l-9 9" />
      <path d="M18 14v6H4V6h6" />
    </>
  ),
  refresh: (
    <>
      <path d="M20 11a8 8 0 0 0-14.5-4.5L4 8M4 4v4h4" />
      <path d="M4 13a8 8 0 0 0 14.5 4.5L20 16M20 20v-4h-4" />
    </>
  ),
  dot: <circle cx="12" cy="12" r="3" />,
  key: (
    <>
      <circle cx="8" cy="15" r="4" />
      <path d="M10.8 12.2L20 3M16 7l3 3M14 9l2 2" />
    </>
  ),
  copy: (
    <>
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M5 15V6a2 2 0 0 1 2-2h8" />
    </>
  ),
  download: <path d="M12 4v11M7 10l5 5 5-5M5 20h14" />,
  moon: <path d="M20 14a8 8 0 1 1-10-10 7 7 0 0 0 10 10z" />,
  bulb: <path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.8.8 1 1.5 1 2.5h6c0-1 .2-1.7 1-2.5A6 6 0 0 0 12 3z" />,
  star: <path d="M12 3l2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z" />,
  bed: <path d="M3 18V7M3 14h18v4M21 14v-2a3 3 0 0 0-3-3h-7v5M7 11.5a1.5 1.5 0 1 0 0-.01" />,
  sofa: <path d="M5 11V8a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v3M3 13a2 2 0 0 1 4 0v2h10v-2a2 2 0 0 1 4 0v5H3zM6 18v2M18 18v2" />,
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </>
  ),
  fan: (
    <>
      <circle cx="12" cy="12" r="1.6" />
      <path d="M12 10.4c-1-3.4.3-6.4 3-6.4 2.2 0 2.6 2.8.4 4.4L13.4 11M13.6 12c3.4-1 6.4.3 6.4 3 0 2.2-2.8 2.6-4.4.4L13 13.4M12 13.6c1 3.4-.3 6.4-3 6.4-2.2 0-2.6-2.8-.4-4.4l2-1.6M10.4 12c-3.4 1-6.4-.3-6.4-3 0-2.2 2.8-2.6 4.4-.4l2 2" />
    </>
  ),
  blinds: <path d="M4 4h16M5 4v12M19 4v12M5 8h14M5 12h14M5 16h14M12 16v4M10.5 21h3" />,
  gauge: (
    <>
      <path d="M4.5 18a8.5 8.5 0 1 1 15 0" />
      <path d="M12 14l4-5M12 14.1v.01" />
    </>
  ),
  droplet: <path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z" />,
  flame: <path d="M12 21c-3.9 0-6.5-2.6-6.5-6.1 0-3.7 3-5.6 3.6-9.4 2.4 1.5 3.4 3.6 3.5 5.6 1-.6 1.7-1.7 1.9-3 2.4 1.9 4 4.5 4 6.8 0 3.5-2.6 6.1-6.5 6.1zM12 21c-1.5 0-2.6-1.1-2.6-2.6 0-1.6 1.3-2.4 2.6-4 1.3 1.6 2.6 2.4 2.6 4 0 1.5-1.1 2.6-2.6 2.6z" />,
  toggle: (
    <>
      <rect x="3" y="7" width="18" height="10" rx="5" />
      <circle cx="16" cy="12" r="2.6" />
    </>
  ),
  megaphone: <path d="M4 10v4a1 1 0 0 0 1 1h2l8 4V5L7 9H5a1 1 0 0 0-1 1zM7 15l1.5 5h2.5L9.8 15.6M18.5 9.5a3.5 3.5 0 0 1 0 5" />,
  pointer: <path d="M9 11V5.5a1.5 1.5 0 0 1 3 0V11M12 10.5V9a1.5 1.5 0 0 1 3 0v2M15 10.5a1.5 1.5 0 0 1 3 0V15a6 6 0 0 1-6 6h-.7a6 6 0 0 1-4.9-2.6L4 15a1.6 1.6 0 0 1 2.5-2L9 15.5" />,
  up: <path d="M12 19V5M6 11l6-6 6 6" />,
  down: <path d="M12 5v14M6 13l6 6 6-6" />,
  bellOff: <path d="M6.3 6.3A6 6 0 0 0 6 8c0 7-3 9-3 9h14M18 13.5c-.1-1.3-.1-2.9 0-5.5a6 6 0 0 0-8.9-5.2M10.3 21a1.9 1.9 0 0 0 3.4 0M3 3l18 18" />,  speaker: (
    <>
      <rect x="6" y="3" width="12" height="18" rx="2" />
      <circle cx="12" cy="14" r="3" />
      <path d="M12 7.5v.01" />
    </>
  ),
  webhook: <path d="M9 8.5a3.5 3.5 0 1 1 5.3 3L17 16M8.2 12.6 5.5 17a3.5 3.5 0 1 0 6 1.5h6M17 16.5a3.5 3.5 0 1 1-1.5 6" />,
  send: <path d="M21 3 10 14M21 3l-7 18-4-7-7-4z" />,
  volume: <path d="M4 9v6h4l5 4V5L8 9zM16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12" />,  car: <path d="M5 16h14M6 16l1.5-5h9L18 16M7 16v2M17 16v2M8 13h8" />,
  wrench: <path d="M14.7 6.3a4 4 0 0 0-5.4 5.2L4 16.8V20h3.2l5.3-5.3a4 4 0 0 0 5.2-5.4l-2.6 2.6-2.4-.6-.6-2.4z" />,  cloud: <path d="M7 18h10a4 4 0 0 0 .5-8A6 6 0 0 0 6 9a4.5 4.5 0 0 0 1 9z" />,
  calendar: (
    <>
      <rect x="4" y="5" width="16" height="16" rx="2" />
      <path d="M4 10h16M9 3v4M15 3v4" />
    </>
  ),
  user: (
    <>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21a8 8 0 0 1 16 0" />
    </>
  ),
};

// Command / kind icon names from the hub mapped onto our set.
const ALIASES = {
  arm: 'shield',
  arm_home: 'shield',
  arm_away: 'shield',
  disarm: 'shieldOff',
  alarm: 'shield',
  snooze: 'clock',
  dismiss: 'x',
  close: 'door',
  contact: 'door',
  open_door: 'doorOpen',
  on: 'power',
  off: 'power',
  turn_on: 'power',
  turn_off: 'power',
  switch: 'power',
  doorbell: 'bell',
  climate: 'thermometer',
  sensor: 'gauge',
  'bell-off': 'bellOff',
  bell_off: 'bellOff',
  siren: 'megaphone',
  press: 'pointer',
  set_volume: 'volume',
  play_pause: 'play',
  leak: 'droplet',
  smoke: 'flame',
  cover: 'blinds',
  dock: 'home',
  locate: 'bell',
  cleaning: 'vacuum',
  robot: 'vacuum',
  // The hub's generic icon names (a device's own icon, Devices > a device > Icon).
  appliance: 'bolt',
  outlet: 'plug',
  lamp: 'light',
  heater: 'flame',
  pump: 'droplet',
  pool: 'droplet',
  sprinkler: 'droplet',
  water: 'droplet',
  garden: 'sun',
  tv: 'grid',
  speaker: 'volume',
  printer: 'list',
  server: 'list',
  router: 'webhook',
  car: 'garage',
  battery: 'bolt',
  window: 'blinds',
  button: 'pointer',
};

// Last resort for icon names from the hub we don't know: match on keywords.
const KEYWORDS = [
  [/unlock/, 'unlock'],
  [/lock/, 'lock'],
  [/garage|door/, 'garage'],
  [/bulb|light|bright|dim|lamp/, 'light'],
  [/temp|thermo|heat|cool|climate/, 'thermometer'],
  [/power|off|on$/, 'power'],
  [/bell|ring/, 'bell'],
  [/shield|arm|alarm/, 'shield'],
];

function resolve(name) {
  if (!name) return 'dot';
  if (PATHS[name]) return name;
  if (ALIASES[name] && PATHS[ALIASES[name]]) return ALIASES[name];
  const n = String(name).toLowerCase();
  const hit = KEYWORDS.find(([re]) => re.test(n));
  return hit ? hit[1] : 'dot';
}

export function Icon({ name, size = 18, strokeWidth = 2, className, title }) {
  const key = resolve(name);
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={title ? undefined : 'true'}
      role={title ? 'img' : undefined}
      focusable="false"
    >
      {title ? <title>{title}</title> : null}
      {PATHS[key]}
    </svg>
  );
}
