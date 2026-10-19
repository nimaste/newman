import { useEffect, useMemo, useState } from 'react';
import { onApiEvent } from './api.js';
import { HubContext, navigate, useApi, useRoute } from './hooks.js';
import { useLive, useLiveRefresh, useLiveStatus } from './live.js';
import { setCategoryScreens } from './model.js';
import Sidebar from './components/Sidebar.jsx';
import { ConfirmProvider, Toasts } from './components/Feedback.jsx';
import { ErrorBox, Loading } from './components/Controls.jsx';
import { Icon } from './icons.jsx';
import Overview from './pages/Overview.jsx';
import Devices from './pages/Devices.jsx';
import DeviceDetail from './pages/DeviceDetail.jsx';
import Cameras from './pages/Cameras.jsx';
import LiveView from './pages/LiveView.jsx';
import Automations from './pages/Automations.jsx';
import Upkeep from './pages/Upkeep.jsx';
import AutomationEditor from './pages/AutomationEditor.jsx';
import Activity from './pages/Activity.jsx';
import Integrations from './pages/Integrations.jsx';
import People from './pages/People.jsx';
import Phones from './pages/Phones.jsx';
import PeopleMap from './pages/PeopleMap.jsx';
import Places from './pages/Places.jsx';
import Profile from './pages/Profile.jsx';
import Dashboards from './pages/Dashboards.jsx';
import DashboardEditor from './pages/DashboardEditor.jsx';
import HouseSettings from './pages/HouseSettings.jsx';
import Accounts from './pages/Accounts.jsx';
import SignInSettings from './pages/SignInSettings.jsx';
import Backups from './pages/Backups.jsx';
import { AuthGate } from './auth.jsx';

const POLL_MS = 5000;
const PAIRING_POLL_MS = 3000;
// Devices and activity arrive over the live socket; status (next runs, connection health) is only
// polled slowly, and devices are polled only while the socket is down.
const STATUS_MS = 30000;
const FALLBACK_MS = 10000;

export function useHubData() {
  const meta = useApi('api/meta');
  // Poll faster while an integration waits for a pairing button press.
  const [pairing, setPairing] = useState(false);
  const status = useApi('api/status', { interval: pairing ? PAIRING_POLL_MS : STATUS_MS });
  const nowPairing = !!(status.data && (status.data.integrations || []).some((i) => i.status === 'pairing'));
  useEffect(() => setPairing(nowPairing), [nowPairing]);
  const live = useLiveStatus();
  const socketDown = live.state === 'down';
  const entities = useApi('api/entities', { interval: socketDown ? FALLBACK_MS : 0 });
  // Every category, made-in-Newman ones too (meta has the built-in ones only).
  const cats = useApi('api/categories');
  useLiveRefresh((m) => m.type === 'categories' || m.type === 'reconnected', () => cats.reload(), 200);
  // Each pushed device replaces its copy in the store (or is added); a removed one is dropped.
  useLive((m) => {
    if (m.type === 'entity-removed' && m.id) {
      entities.setData((list) => (Array.isArray(list) ? list.filter((x) => x.id !== m.id) : list));
    }
    if (m.type === 'entity' && m.entity && m.entity.id) {
      entities.setData((list) => {
        if (!Array.isArray(list)) return list;
        const i = list.findIndex((x) => x.id === m.entity.id);
        if (i < 0) return [...list, m.entity];
        const next = list.slice();
        next[i] = m.entity;
        return next;
      });
    }
  });
  // After a reconnect: catch up once. New activity may change next runs / connection health.
  useLiveRefresh((m) => m.type === 'reconnected', () => entities.reload(), 50);
  useLiveRefresh((m) => m.type === 'activity' || m.type === 'reconnected', () => status.reload(), 1500);
  const [hubDown, setHubDown] = useState(false);

  useEffect(() => onApiEvent((e) => e.type === 'hub' && setHubDown(!e.up)), []);

  // Meta is needed for almost everything: keep retrying until it arrives.
  const metaMissing = meta.data === undefined && !!meta.error;
  useEffect(() => {
    if (!metaMissing) return undefined;
    const t = setInterval(() => meta.reload(), POLL_MS);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [metaMissing]);

  return useMemo(() => {
    const m = meta.data || null;
    const kinds = (m && m.kinds) || [];
    const categories = Array.isArray(cats.data) && cats.data.length ? cats.data : (m && m.categories) || [];
    setCategoryScreens(categories);
    const kindsById = Object.fromEntries(kinds.map((k) => [k.id, k]));
    const categoriesById = Object.fromEntries(categories.map((c) => [c.id, c]));
    const list = Array.isArray(entities.data) ? entities.data : null;
    const entitiesById = Object.fromEntries((list || []).map((e) => [e.id, e]));
    return {
      meta: m,
      metaRes: meta,
      status: status.data || null,
      entities: list,
      entitiesError: entities.error,
      kinds,
      categories,
      kindsById,
      categoriesById,
      entitiesById,
      hubDown,
      live,
      reloadEntities: entities.reload,
      reloadStatus: status.reload,
      setEntities: entities.setData,
    };
  }, [meta, cats.data, status.data, entities.data, entities.error, hubDown, live]); // eslint-disable-line react-hooks/exhaustive-deps
}

/** Replaces the current hash (no extra history entry) - for renamed pages. */
function Redirect({ to }) {
  useEffect(() => {
    navigate(to, { replace: true });
  }, [to]);
  return null;
}

function Page({ route }) {
  const [section = 'overview', sub] = route.parts;
  switch (section) {
    case 'overview':
      return <Overview />;
    case 'devices':
      return sub ? <DeviceDetail key={sub} id={sub} /> : <Devices />;
    case 'cameras':
      // #/cameras/<id>: the full-screen live view (one instance, so a clip keeps playing while switching cameras).
      return sub ? <LiveView id={sub} /> : <Cameras />;
    case 'upkeep':
      return <Upkeep />;
    case 'automations':
      return sub ? (
        <AutomationEditor key={sub + '?' + route.query.toString()} id={sub} templateId={route.query.get('template')} query={route.query} />
      ) : (
        <Automations />
      );
    case 'activity':
      return <Activity />;
    case 'integrations':
      return <Integrations selected={sub} />;
    case 'household':
      if (sub === 'phones') return <Phones />;
      if (sub === 'accounts') return <Accounts />;
      if (sub === 'map') return <PeopleMap />;
      if (sub === 'places') return <Places />;
      // Links to the test-phone page open Phones with the developer tools shown.
      return sub === 'test-phones' ? <Phones key="dev" developer /> : <People />;
    // Old links (the section used to be called People): #/people/<tab>, #/phones, #/test-phones.
    case 'people':
      return <Redirect to={'household' + (sub ? '/' + sub : '')} />;
    case 'phones':
      return <Redirect to="household/phones" />;
    case 'test-phones':
      return <Redirect to="household/test-phones" />;
    case 'profile':
      return <Profile />;
    case 'dashboards':
      // (The phones have their own Home now - Edit Home on the phone; old #/dashboards/phone links land here.)
      if (sub === 'phone') return <Redirect to="dashboards" />;
      return sub ? <DashboardEditor key={sub} id={sub} /> : <Dashboards />;
    case 'house':
      return <HouseSettings />;
    case 'settings':
      return <SignInSettings />;
    case 'backups':
      return <Backups />;
    default:
      return (
        <div className="app-page">
          <div className="app-head">
            <h1 className="page-title">Page not found</h1>
          </div>
          <p>
            <a href="#/overview">Back to the overview</a>
          </p>
        </div>
      );
  }
}

/** A quiet "Reconnecting…" pill once the live connection has been down for more than 5 s. */
export function LiveIndicator() {
  const live = useLiveStatus();
  const [late, setLate] = useState(false);
  useEffect(() => {
    if (live.state !== 'down') {
      setLate(false);
      return undefined;
    }
    const t = setTimeout(() => setLate(true), Math.max(0, 5000 - (Date.now() - live.since)));
    return () => clearTimeout(t);
  }, [live]);
  if (!late) return null;
  return (
    <div className="live-pill" role="status">
      <span className="spinner" style={{ width: 12, height: 12 }} /> Reconnecting…
    </div>
  );
}

function Dashboard() {
  const route = useRoute();
  const hub = useHubData();
  const page = route.parts[0] || 'overview';
  // App-style pages fit the window: fixed header, panes that scroll on their own.
  // Every page now uses it.
  const appLayout = true;

  useEffect(() => {
    const titles = {
      overview: 'Overview',
      devices: 'Devices',
      cameras: 'Cameras',
      automations: 'Automations',
      upkeep: 'Upkeep',
      activity: 'Activity',
      integrations: 'Connections',
      household: 'Household · ' + ({ phones: 'Phones', 'test-phones': 'Phones', accounts: 'Accounts', map: 'Map', places: 'Places' }[route.parts[1]] || 'People'),
      profile: 'Your profile',
      dashboards: 'Dashboards',
      house: 'Settings',
      settings: 'Sign-in settings',
      backups: 'Backups',
    };
    document.title = (titles[page] ? titles[page] + ' · ' : '') + 'Newman';
  }, [page, route.parts[1]]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <HubContext.Provider value={hub}>
      <>
        <div className="app">
          <Sidebar page={page} />
          <main className={'main' + (appLayout ? ' main--app' : '')} id="main">
            <div className="main-inner">
              {hub.hubDown ? (
                <div className="hub-banner" role="alert">
                  <Icon name="alert" size={18} />
                  Can't reach the Newman hub.
                  <span>Showing the last known state — retrying every 5 seconds.</span>
                </div>
              ) : null}
              {hub.meta ? (
                <Page route={route} />
              ) : hub.metaRes.error ? (
                <ErrorBox error={hub.metaRes.error} onRetry={hub.metaRes.reload}>
                  Couldn't load Newman's device catalog ({hub.metaRes.error.message}). Retrying automatically.
                </ErrorBox>
              ) : (
                <Loading>Connecting to the hub…</Loading>
              )}
            </div>
          </main>
        </div>
        <LiveIndicator />
      </>
    </HubContext.Provider>
  );
}

/** Sign-in first (AuthGate), then the dashboard. Toasts and dialogs work on both. */
export default function App() {
  return (
    <ConfirmProvider>
      <AuthGate>
        <Dashboard />
      </AuthGate>
      <Toasts />
    </ConfirmProvider>
  );
}
