import { useState } from 'react';
import { api, enc, toast } from '../api.js';
import { navigate, useApi, useHub } from '../hooks.js';
import { useLiveRefresh } from '../live.js';
import { useAuth } from '../auth.jsx';
import { Empty, ErrorBox, Loading, Switch } from '../components/Controls.jsx';
import { fmtNext, fmtWhen } from '../format.js';
import { Icon } from '../icons.jsx';
import { automationBody, runTime } from '../model.js';
import { cleanForSend, conditionEntityIds, normalize, toMulti, triggerEntityIds } from '../automation.js';

function lastText(a) {
  const r = a.lastRun;
  const t = runTime(r);
  if (!t) return 'Never run';
  const status = r && typeof r === 'object' && r.status ? ` · ${r.status}` : '';
  return `Ran ${fmtWhen(t)}${status}`;
}

/** An automation's category: that of the device that starts it, else what it checks, else what it controls. */
function categoryOf(a, hub) {
  const ids = [
    ...triggerEntityIds(a.trigger),
    ...conditionEntityIds(a.condition),
    ...(a.actions || []).flatMap((x) => toMulti(x).entities),
  ];
  for (const id of ids) {
    const e = hub.entitiesById[id];
    const k = e && hub.kindsById[e.kind];
    if (k && k.category) return k.category;
  }
  return 'other';
}

export default function Automations() {
  const hub = useHub();
  const { categories, categoriesById } = hub;
  const { user } = useAuth();
  // Admins always; others when an admin lets them (Accounts); everyone else just views.
  const isAdmin = !!(user && user.canAutomate);
  const autos = useApi('api/automations');
  // Last/next runs change when an automation runs: refresh on those live activity entries.
  useLiveRefresh((m) => m.type === 'activity' && !!m.entry && ['automation', 'followup'].includes(m.entry.kind), () => autos.reload(), 500);
  const templates = useApi('api/automations/templates');
  const [pending, setPending] = useState({});
  const [cat, setCat] = useState('');
  const [q, setQ] = useState('');

  const toggle = async (row) => {
    // eslint-disable-next-line no-unused-vars
    const { _cat, ...a } = row; // _cat is this page's grouping, never part of the automation
    const enabled = !a.enabled;
    setPending((p) => ({ ...p, [a.id]: true }));
    autos.setData((list) => list.map((x) => (x.id === a.id ? { ...x, enabled } : x)));
    try {
      const saved = await api.put('api/automations/' + enc(a.id), { ...automationBody(cleanForSend(normalize(a))), enabled });
      if (saved && typeof saved === 'object' && saved.id) autos.setData((list) => list.map((x) => (x.id === a.id ? { ...x, ...saved } : x)));
      toast(`${a.name} is ${enabled ? 'on' : 'off'}`, 'success');
    } catch {
      autos.setData((list) => list.map((x) => (x.id === a.id ? { ...x, enabled: !enabled } : x)));
    } finally {
      setPending((p) => ({ ...p, [a.id]: false }));
      autos.reload();
    }
  };

  const list = (autos.data || []).map((a) => ({ ...a, _cat: categoryOf(a, hub) }));
  const needle = q.trim().toLowerCase();
  const shown = list.filter((a) => (!cat || a._cat === cat) && (!needle || `${a.name} ${a.summary || ''}`.toLowerCase().includes(needle)));
  const order = [...new Set([...categories.map((c) => c.id), 'other'])];
  const groups = order
    .map((id) => ({ id, name: (categoriesById[id] && categoriesById[id].name) || 'Other', items: shown.filter((a) => a._cat === id) }))
    .filter((g) => g.items.length);
  const usedCats = order.filter((id) => list.some((a) => a._cat === id));

  return (
    <div className="app-page">
      <div className="app-head">
        <h1 className="page-title">Automations</h1>
        {isAdmin ? (
          <button type="button" className="btn btn-primary" onClick={() => navigate('automations/new')}>
            <Icon name="plus" size={16} /> New automation
          </button>
        ) : null}
      </div>
      {list.length ? (
        <div className="filter-bar filter-bar--inline page-tools" role="search">
          <label className="filter">
            <span className="filter-label">Category</span>
            <select className="select" value={cat} onChange={(e) => setCat(e.target.value)}>
              <option value="">All categories ({list.length})</option>
              {usedCats.map((id) => (
                <option key={id} value={id}>
                  {(categoriesById[id] && categoriesById[id].name) || 'Other'} ({list.filter((a) => a._cat === id).length})
                </option>
              ))}
            </select>
          </label>
          <label className="filter filter--grow">
            <span className="filter-label">Search</span>
            <input className="input" type="search" placeholder="Search automations" value={q} onChange={(e) => setQ(e.target.value)} />
          </label>
        </div>
      ) : null}

      {isAdmin && templates.data && templates.data.length ? (
        <section className="recipe-strip" aria-label="Start from a recipe">
          <span className="eyebrow eyebrow--sm recipe-strip-label">Start from a recipe</span>
          <div className="recipe-row">
            {templates.data.map((t) => (
              <button key={t.id} type="button" className="recipe recipe--compact" onClick={() => navigate('automations/new?template=' + enc(t.id))} title={t.hint || ''}>
                <span className="recipe-cat">{(categoriesById[t.category] && categoriesById[t.category].name) || t.category}</span>
                <span className="recipe-name">{t.name}</span>
              </button>
            ))}
          </div>
        </section>
      ) : null}

      <div className="pane">
        <div className="pane-body">
          {autos.data === undefined ? (
            autos.error ? <ErrorBox error={autos.error} onRetry={autos.reload} /> : <Loading>Loading automations…</Loading>
          ) : !list.length ? (
            <Empty title="No automations yet" action={isAdmin ? <button type="button" className="btn btn-primary" onClick={() => navigate('automations/new')}>New automation</button> : null}>
              Pick a recipe above or build one from scratch.
            </Empty>
          ) : !groups.length ? (
            <Empty title="Nothing matches">Try another category or search.</Empty>
          ) : (
            <div className="auto-groups">
              {groups.map((g) => (
                <section key={g.id} className="auto-group" aria-label={g.name}>
                  <h2 className="group-title">
                    {g.name} <span>· {g.items.length}</span>
                  </h2>
                  <div className="auto-list">
                    {g.items.map((a) => (
                      <article key={a.id} className={'auto-item' + (a.enabled ? '' : ' is-off')}>
                        <Switch checked={a.enabled} onChange={() => toggle(a)} disabled={pending[a.id] || !isAdmin} ariaLabel={(a.enabled ? 'Turn off ' : 'Turn on ') + a.name} />
                        <a className="auto-item-main" href={'#/automations/' + enc(a.id)}>
                          <span className="auto-item-name">{a.name}</span>
                          <span className="auto-item-sentence" title={a.summary}>
                            {a.summary}
                          </span>
                        </a>
                        <span className="auto-item-meta">
                          {a.enabled && runTime(a.nextRun) ? <span className="teal strong">Next {fmtNext(runTime(a.nextRun))}</span> : !a.enabled ? <span className="strong">Off</span> : null}
                          <span>{lastText(a)}</span>
                        </span>
                      </article>
                    ))}
                  </div>
                </section>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
