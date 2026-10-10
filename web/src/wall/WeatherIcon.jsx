// Stroke icons for the weather kind's states (night variants for the clear ones).

/* ---------------- condition icons ---------------- */

const CLOUD = 'M7 18h10a4 4 0 0 0 .5-8A6 6 0 0 0 6 9a4.5 4.5 0 0 0 1 9z';
const CLOUD_HIGH = 'M7 14h10a4 4 0 0 0 .5-8A6 6 0 0 0 6 5a4.5 4.5 0 0 0 1 9z';
const SUN_RAYS = 'M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4';
const MOON = 'M20 14a8 8 0 1 1-10-10 7 7 0 0 0 10 10z';
const SMALL_CLOUD = 'M10 21h8a3.2 3.2 0 0 0 .4-6.4A4.6 4.6 0 0 0 9.6 15a3 3 0 0 0 .4 6z';
const TINY_CLOUD = 'M14.5 21.5h5a2.3 2.3 0 0 0 .3-4.6 3.3 3.3 0 0 0-6.3 1A1.8 1.8 0 0 0 14.5 21.5z';
const SUN = '#F5B942';
const WET = '#7FB8FF';
// The cloud's cut-out over the sun: the tile's colour (the wall theme's, theme.css).
const CUT = { fill: 'var(--wt-icon-cut, #1a2447)' };

function Sun({ small }) {
  return small ? (
    <g stroke={SUN}>
      <circle cx="8" cy="8" r="3" />
      <path d="M8 2v1.3M2 8h1.3M3.8 3.8l.9.9M12.2 3.8l-.9.9M3.8 12.2l.9-.9" />
    </g>
  ) : (
    <g stroke={SUN}>
      <circle cx="12" cy="12" r="4" />
      <path d={SUN_RAYS} />
    </g>
  );
}

function Moon({ small }) {
  return <path d={small ? 'M11.5 9.5A4.8 4.8 0 0 1 6.5 3.5a4.8 4.8 0 1 0 5 6z' : MOON} />;
}

/** A stroke icon for each weather state; the moon stands in for the sun at night. */
export function WeatherIcon({ condition, isDay = true, size = 24, strokeWidth = 1.6 }) {
  const c = condition || 'cloudy';
  let body;
  if (c === 'clear') body = isDay ? <Sun /> : <Moon />;
  else if (c === 'mostly-clear')
    body = (
      <>
        {isDay ? <Sun /> : <Moon />}
        <path d={TINY_CLOUD} style={CUT} />
      </>
    );
  else if (c === 'partly-cloudy')
    body = (
      <>
        {isDay ? <Sun small /> : <Moon small />}
        <path d={SMALL_CLOUD} style={CUT} />
      </>
    );
  else if (c === 'fog')
    body = (
      <>
        <path d={CLOUD_HIGH} />
        <path d="M5 17.5h14M7 21h10" />
      </>
    );
  else if (c === 'drizzle')
    body = (
      <>
        <path d={CLOUD_HIGH} />
        <path stroke={WET} strokeWidth={strokeWidth + 0.8} d="M8 18v.01M12 19v.01M16 18v.01M10 21.5v.01M14 21.5v.01" />
      </>
    );
  else if (c === 'rain' || c === 'showers')
    body = (
      <>
        <path d={CLOUD_HIGH} />
        <path stroke={WET} d={c === 'rain' ? 'M8 17l-1.2 3.5M12 17l-1.2 3.5M16 17l-1.2 3.5' : 'M9.5 17l-1.5 4.5M14.5 17L13 21.5'} />
      </>
    );
  else if (c === 'freezing-rain')
    body = (
      <>
        <path d={CLOUD_HIGH} />
        <path stroke={WET} d="M8 17l-1.2 3.5M16 17l-1.2 3.5" />
        <path stroke={WET} strokeWidth={strokeWidth + 0.8} d="M12 18v.01M11.5 21.5v.01" />
      </>
    );
  else if (c === 'snow' || c === 'snow-showers')
    body = (
      <>
        <path d={CLOUD_HIGH} />
        <path d="M12 16.5v5M9.8 17.8l4.4 2.4M14.2 17.8l-4.4 2.4" />
        {c === 'snow' ? <path strokeWidth={strokeWidth + 0.8} d="M6.5 19v.01M17.5 19v.01" /> : <path stroke={WET} d="M7.5 17L6.3 20.5" />}
      </>
    );
  else if (c === 'thunderstorm')
    body = (
      <>
        <path d={CLOUD_HIGH} />
        <path stroke={SUN} d="M13 15l-3 4h4l-3 4" />
      </>
    );
  else body = <path d={CLOUD} />;
  return (
    <svg className="wx-icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="#C9D4F5" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {body}
    </svg>
  );
}
