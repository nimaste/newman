// The wall tablet (/#/wall, or /wall): the signed-in person's dashboard from the hub, drawn in the
// approved "night glass" look. A tablet session can only look and send device commands, so there is
// no editing here at all.
//   #/wall            the home page      #/wall/camera/<id>   full-screen camera
// The home page (dashboard.home; Today in the mornings if set) and going back to it: home.js.
//   #/wall/<pageId>   that page          #/wall/cameras       all cameras

import { useCallback, useEffect, useRef, useState } from 'react';
import '@fontsource/sora/latin-500.css';
import '@fontsource/sora/latin-600.css';
import './wall.css';
import { api, enableTabletToken, onApiEvent, setTabletToken } from '../api.js';
import { HubContext, navigate, useApi, useRoute } from '../hooks.js';
import { stopLive, useLive } from '../live.js';
import { Icon } from '../icons.jsx';
import { ConfirmProvider, Toasts } from '../components/Feedback.jsx';
import { LiveIndicator, useHubData } from '../App.jsx';
import LiveView from '../pages/LiveView.jsx';
import CameraGrid from '../components/CameraGrid.jsx';
import { deviceName } from '../pages/SignIn.jsx';
import { StatusChips, Tile } from './tiles.jsx';
import { CommandProvider } from './PinPad.jsx';
import { NamedHub } from './names.jsx';
import WallAlerts from './WallAlerts.jsx';
import VoiceLayer from './VoiceLayer.jsx';
import { MusicEngine, NowPlayingChip } from './Music.jsx';
import { busyNow } from './music.js';
import { onBeforeReload, reloadNote, setIdleProbe } from '../build.js';
import { CookEngine } from './CookMode.jsx';
import CalendarView from './CalendarView.jsx';
import TodoView from './TodoView.jsx';
import WeatherView from './WeatherView.jsx';
import { enc } from '../api.js';
import { currentHome, homeOf, useHomeSchedule } from './home.js';
import { WallTime, wallDay } from './clock.jsx';
import WallTabs from './tabs.jsx';
import { Backdrop, DimGreeting, Snow, hasCast, useDecor, useWallTheme } from './Theme.jsx';

export function isWallUrl() {
  const path = window.location.pathname.replace(/\/+$/, '');
  return /\/wall$/.test(path) || /^#\/wall(\/|$|\?)/.test(window.location.hash);
}

/** The route inside the wall: [] | [pageId] | ['camera', id] | ['cameras']. */
function wallParts(route) {
  const p = route.parts;
  return p[0] === 'wall' ? p.slice(1) : [];
}

/* ---------------- sign-in (tablet-styled) ---------------- */

/** Inside a wall app (Android or iPad): its "Connect to your Newman" screen (address, certificate, token). */
function useNativeServer() {
  const find = () => (typeof window !== 'undefined' && window.NewmanWall && typeof window.NewmanWall.openServer === 'function' ? window.NewmanWall : null);
  const [bridge, setBridge] = useState(find);
  useEffect(() => {
    const on = () => setBridge(find());
    window.addEventListener('newmanwall', on);
    on();
    return () => window.removeEventListener('newmanwall', on);
  }, []);
  return bridge ? () => bridge.openServer() : null;
}

function WallSignIn({ where, onDone }) {
  const openServer = useNativeServer();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [needCode, setNeedCode] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const res = await api.post(
        'api/auth/login',
        { username: username.trim(), password, kind: 'tablet', deviceName: 'Wall tablet · ' + deviceName(), remember: true, ...(needCode ? { code: code.replace(/\s+/g, '') } : {}) },
        { quiet: true },
      );
      if (res && res.token) setTabletToken(res.token);
      await onDone();
    } catch (err) {
      const d = err.data && typeof err.data === 'object' ? err.data : {};
      if (err.status === 401 && d.needs === 'totp') {
        setNeedCode(true);
        if (d.error) setError(d.error);
      } else setError(err.status === 0 ? "Can't reach the Newman hub." : err.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="wall wall--signin">
      <form className="wt-signin" onSubmit={submit}>
        <div className="wt-signin-brand">Newman</div>
        <h1 className="wt-signin-title">{needCode ? 'Enter your code' : 'Sign in this tablet'}</h1>
        <p className="wt-muted">{needCode ? 'Type the 6-digit code from your authenticator app.' : 'It stays signed in. It can show the house and control devices, but not change settings.'}</p>
        {needCode ? (
          <input className="wt-input wt-input--code" inputMode="numeric" autoComplete="one-time-code" maxLength={7} placeholder="123456" aria-label="6-digit code" value={code} onChange={(e) => setCode(e.target.value)} autoFocus />
        ) : (
          <>
            <input className="wt-input" placeholder="Username" aria-label="Username" autoComplete="username" autoCapitalize="none" spellCheck={false} value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
            <input className="wt-input" type="password" placeholder="Password" aria-label="Password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </>
        )}
        <div className={'wt-signin-error' + (error ? '' : ' is-empty')} role={error ? 'alert' : undefined}>
          {error || ' '}
        </div>
        <button type="submit" className="wt-signin-btn" disabled={busy || (needCode ? code.replace(/\D/g, '').length < 6 : !username.trim() || !password)}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
        {needCode ? (
          <button type="button" className="wt-link-btn" onClick={() => (setNeedCode(false), setCode(''), setError(''))}>
            Back
          </button>
        ) : null}
        <div className="wt-where">
          <span className={'wt-where-dot' + (where === 'home' ? '' : ' is-away')} /> {where === 'home' ? 'At home' : 'From outside'}
        </div>
        {openServer && !needCode ? (
          <button type="button" className="wt-link-btn" onClick={openServer}>
            Server
          </button>
        ) : null}
      </form>
    </main>
  );
}

/* ---------------- top bar ---------------- */

function useNow() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  return now;
}

function TopBar({ pages, current, tabNames, decor = null, decorRuler = null, decorKey = '', onDecorRoom, onDim, user, onSignOut, skewMin, musicChip }) {
  const now = useNow();
  const [menu, setMenu] = useState(false);
  const press = useRef(null);
  const { day, date } = wallDay(now);
  // Inside a wall app (Android or iPad): its own settings (behind the tablet's PIN), also on a corner hold.
  const bridge = typeof window !== 'undefined' ? window.NewmanWall : null;
  const nativeSettings = bridge && typeof bridge.openSettings === 'function' ? () => (setMenu(false), bridge.openSettings()) : null;
  // A long press on the clock shows who is signed in (there are no settings on the tablet).
  const down = () => {
    clearTimeout(press.current);
    press.current = setTimeout(() => setMenu(true), 700);
  };
  const up = () => clearTimeout(press.current);
  return (
    <header className="wt-top">
      <div className="wt-clock" onPointerDown={down} onPointerUp={up} onPointerLeave={up} onContextMenu={(e) => e.preventDefault()}>
        <WallTime date={now} />
        <div className="wt-date">
          <span>{day}</span>
          <span className="wt-dim-text">{date}</span>
        </div>
        {menu ? (
          <div className="wt-menu" role="dialog" aria-label="Signed in">
            <span>
              Signed in as <b>{user.name}</b>
            </span>
            <button type="button" className="wt-pill" onClick={onSignOut}>
              Sign out
            </button>
            {nativeSettings ? (
              <button type="button" className="wt-pill" onClick={nativeSettings}>
                Tablet settings
              </button>
            ) : null}
            <button type="button" className="wt-pill" onClick={() => setMenu(false)}>
              Close
            </button>
          </div>
        ) : null}
      </div>
      {/* Width goes to the clock, then the right side, then the tabs (tabfit.js). A decoration for
          the bar goes here, as <div className="wt-decor">: it only gets what the tabs leave over. */}
      {decor ? <div className="wt-decor">{decor}</div> : null}
      {decorRuler}
      <WallTabs pages={pages} current={current} tabNames={tabNames} hrefFor={(p) => '#/wall/' + enc(p.id)} onDecorRoom={onDecorRoom} decor={decorKey} />
      <div className="wt-top-right">
        {musicChip ? <NowPlayingChip className="mu-chip--top" /> : null}
        {skewMin ? <span className="wt-skew">Tablet clock is {skewMin} min off</span> : null}
        <button type="button" className="wt-round" aria-label="Dim the screen" onClick={onDim}>
          <Icon name="moon" size={22} />
        </button>
      </div>
    </header>
  );
}

/* ---------------- the dimmed screen ---------------- */

/** The wall app's bridge (window.NewmanWall, Android or iPad), or null in a browser. */
function nativeWall() {
  return typeof window !== 'undefined' && window.NewmanWall && window.NewmanWall.native === true ? window.NewmanWall : null;
}

function nativeDimmed() {
  const b = nativeWall();
  try {
    return !!(b && typeof b.isDimmed === 'function' && b.isDimmed());
  } catch {
    return false;
  }
}

/** The time, soft, on the dimmed screen: dimmed, not off. */
function DimClock() {
  const now = useNow();
  const { day, date } = wallDay(now);
  return (
    <div className="wt-dim-clock" aria-hidden="true">
      <WallTime date={now} />
      <div className="wt-dim-day">
        {day} · {date}
      </div>
    </div>
  );
}

/* ---------------- the wall ---------------- */

function WallScreens({ auth, signOut }) {
  const route = useRoute();
  const parts = wallParts(route);
  const dash = useApi('api/dashboards/current');
  // Dimmed stays dimmed when the page reloads itself for a new build (build.js), or when it loads
  // while the wall app has the screen dimmed.
  const [dim, setDim] = useState(() => reloadNote('wallDim') === true || nativeDimmed());
  const [skew, setSkew] = useState(0);
  const lastPage = useRef(null);
  const dimRef = useRef(dim);
  dimRef.current = dim;
  useEffect(() => onBeforeReload('wallDim', () => dimRef.current), []);
  // The wall app (Android or iPad) dims after a while without a touch and wakes on the first touch (which it
  // keeps from the page): it says so with "newmanwall-dim"; preventDefault() = this page draws the
  // dimmed screen itself. Dimming here (the moon button, a reload) dims the tablet's backlight too.
  useEffect(() => {
    const on = (e) => {
      e.preventDefault();
      setDim(!!(e.detail && e.detail.dimmed));
    };
    window.addEventListener('newmanwall-dim', on);
    return () => window.removeEventListener('newmanwall-dim', on);
  }, []);
  useEffect(() => {
    const b = nativeWall();
    if (dim && b && typeof b.dim === 'function') b.dim();
  }, [dim]);
  useEffect(() => {
    setIdleProbe(busyNow);
    return () => setIdleProbe(null);
  }, []);

  // A changed dashboard (edited on the web) re-renders in place; the tablet's clock is checked on hello.
  useLive((m) => {
    if (m.type === 'dashboard' || m.type === 'reconnected') dash.reload();
    if (m.type === 'hello' && typeof m.time === 'number') {
      const off = Math.round((m.time * 1000 - Date.now()) / 60000);
      setSkew(Math.abs(m.time * 1000 - Date.now()) > 60000 ? Math.abs(off) || 1 : 0);
    }
  });

  const d = dash.data;
  const pages = (d && d.pages) || [];
  // The look (theme.js): Christmas in its season; its greeting and characters by the clock when the
  // tabs leave room, otherwise on the dimmed screen.
  const { theme, xmas } = useWallTheme(d);
  const festive = theme === 'christmas';
  const [decorRoom, setDecorRoom] = useState(null);
  const deco = useDecor(xmas, d && d.id, festive, decorRoom);
  const FULL = ['camera', 'cameras', 'calendar', 'todo', 'weather'];
  const pageId = parts[0] && !FULL.includes(parts[0]) ? parts[0] : null;
  // Where the wall rests: the dashboard's home page (Today in the mornings, if it's set up that way).
  const home = homeOf(d);
  const page = pages.find((p) => p.id === pageId) || pages.find((p) => p.id === lastPage.current) || pages.find((p) => p.id === currentHome(home)) || pages[0] || null;
  if (page && !parts[0]) lastPage.current = page.id;
  if (page && pageId) lastPage.current = page.id;
  const backToPage = () => navigate('wall/' + (lastPage.current ? enc(lastPage.current) : ''));
  useHomeSchedule(d ? home : null, FULL.includes(parts[0]) ? parts[0] : page && page.id, (id) => navigate('wall/' + enc(id)));

  let body;
  if (parts[0] === 'camera' && parts[1]) {
    body = <LiveView id={parts[1]} wall={{ back: backToPage }} />;
  } else if (parts[0] === 'cameras') {
    body = (
      <div className="wt-cams">
        <div className="wt-cams-head">
          <button type="button" className="wt-back" onClick={backToPage}>
            <Icon name="chevronLeft" size={26} strokeWidth={2.4} /> Back
          </button>
          <span className="wt-cams-title">All cameras</span>
          <span className="wt-dim-text">Tap one to watch it full screen</span>
        </div>
        <div className="wt-cams-grid">
          <CameraGrid hrefFor={(id) => '#/wall/camera/' + enc(id)} />
        </div>
      </div>
    );
  } else if (parts[0] === 'todo') {
    body = <TodoView key={route.query.get('item') || ''} itemId={route.query.get('item')} onBack={backToPage} />;
  } else if (parts[0] === 'weather') {
    body = <WeatherView onBack={backToPage} />;
  } else if (parts[0] === 'calendar') {
    const cals = (route.query.get('cals') || '').split(',').filter(Boolean);
    body = <CalendarView key={route.query.toString()} day={route.query.get('day')} cals={cals} onBack={backToPage} />;
  } else if (!d) {
    body = <div className="wt-loading">{dash.error ? "Couldn't load this tablet's dashboard. Retrying…" : 'Loading…'}</div>;
  } else if (!page) {
    body = <div className="wt-loading">This dashboard has no pages yet.</div>;
  } else {
    body = (
      <>
        {page.id === home.page ? <StatusChips /> : null}
        <div className="wt-grid">
          {page.tiles.map((t) => (
            <Tile key={t.id} tile={t} />
          ))}
        </div>
      </>
    );
  }

  // Retry quietly while the dashboard can't be loaded.
  useEffect(() => {
    if (d || !dash.error) return undefined;
    const t = setTimeout(() => dash.reload(), 5000);
    return () => clearTimeout(t);
  }, [d, dash.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const full = FULL.includes(parts[0]);
  return (
    <div className="wall">
      {festive && xmas.place === 'background' && hasCast(xmas) ? <Backdrop xmas={xmas} dashId={d.id} /> : null}
      {!full ? <TopBar pages={pages} current={page && page.id} tabNames={d && d.tabNames} decor={deco.decor} decorRuler={deco.ruler} decorKey={deco.fit} onDecorRoom={festive ? setDecorRoom : undefined} onDim={() => setDim(true)} user={auth.user} onSignOut={signOut} skewMin={skew} musicChip={!page || page.id !== home.page} /> : null}
      <NamedHub names={d && d.names}>
        {body}
        <WallAlerts auth={auth} onWake={() => setDim(false)} />
        <VoiceLayer homePage={currentHome(home)} />
        <MusicEngine />
        <CookEngine />
      </NamedHub>
      {festive && xmas.snow ? <Snow paused={dim} /> : null}
      {dim ? (
        <div className="wt-dim" role="button" tabIndex={0} aria-label="Wake the screen" onPointerDown={() => setDim(false)} onKeyDown={() => setDim(false)}>
          <DimClock />
          {festive && deco.moved ? <DimGreeting xmas={xmas} dashId={d.id} /> : null}
        </div>
      ) : null}
    </div>
  );
}

function WallHub({ auth, signOut, refresh }) {
  const hub = useHubData();
  // An admin changed a sign-in setting (e.g. what unlocking asks for): read it again.
  useLive((m) => {
    if (m.type === 'settings') refresh();
  });
  return (
    <HubContext.Provider value={hub}>
      <CommandProvider mode={auth.tabletSensitive || 'none'}>
        {hub.meta ? <WallScreens auth={auth} signOut={signOut} /> : <div className="wall"><div className="wt-loading">Connecting to the hub…</div></div>}
      </CommandProvider>
      <LiveIndicator />
    </HubContext.Provider>
  );
}

export default function WallApp() {
  enableTabletToken(true);
  const [state, setState] = useState(null);
  const refresh = useCallback(async () => {
    try {
      const s = await api.get('api/auth/state', { quiet: true });
      setState(s);
      return s;
    } catch {
      setTimeout(() => refresh(), 5000);
      return null;
    }
  }, []);
  useEffect(() => {
    document.title = 'Newman';
    document.documentElement.classList.add('is-wall');
    refresh();
  }, [refresh]);
  useEffect(
    () =>
      onApiEvent((e) => {
        if (e.type === 'auth' && !e.signedIn) {
          stopLive();
          setTabletToken(null);
          setState((s) => (s ? { ...s, signedIn: false, user: null } : s));
        }
      }),
    [],
  );
  const signOut = async () => {
    try {
      await api.post('api/auth/logout', {}, { quiet: true });
    } catch {
      /* signed out either way */
    }
    stopLive();
    setTabletToken(null);
    setState((s) => ({ ...(s || {}), signedIn: false, user: null }));
  };

  let body;
  if (!state) body = <div className="wall"><div className="wt-loading">Connecting to the hub…</div></div>;
  else if (state.setupNeeded)
    body = (
      <div className="wall">
        <div className="wt-loading">Set Newman up on the dashboard first, then sign this tablet in.</div>
      </div>
    );
  else if (!state.signedIn || !state.user) body = <WallSignIn where={state.where} onDone={refresh} />;
  else body = <WallHub auth={state} signOut={signOut} refresh={refresh} />;
  return (
    <ConfirmProvider>
      {body}
      <Toasts />
    </ConfirmProvider>
  );
}
