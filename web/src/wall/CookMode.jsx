// Cook mode on the wall tablet (the Forkast connection; recipes.py on the hub). The hub keeps this tablet's
// open recipe - its step and servings - and pushes it as {type: "cook"} whenever voice ("Hey Newman, pull up
// the pancakes", "next step", "double it") or a touch here changes it:
//   CookEngine   loads api/cook, follows the pushes and draws CookSheet while a recipe is open.
//   CookSheet    full screen: picture, title, servings (− / +, scaled shown), the ingredients with their
//                amounts worked out by the hub, and the steps with the current one large. Tap a step, Previous
//                / Next, or Close. A dialog, so the wall stays put; the screen is kept awake meanwhile.
//   RecipesTile  the dashboard's Recipes tile: search and tap one to open it here.

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, enc, toast } from '../api.js';
import { useApi, useDebounced } from '../hooks.js';
import { useLive } from '../live.js';
import { Icon } from '../icons.jsx';
import { musicUrl } from './music.js';
import './cook.css';

let state = { cook: null, loaded: false };
const subs = new Set();
function setCook(cook) {
  state = { cook: cook || null, loaded: true };
  subs.forEach((fn) => fn(state));
}
function useCook() {
  const [s, setS] = useState(state);
  useEffect(() => {
    subs.add(setS);
    setS(state);
    return () => subs.delete(setS);
  }, []);
  return s;
}

async function change(body) {
  try {
    const r = await api.patch('api/cook', body, { quiet: true });
    if (r && 'cook' in r) setCook(r.cook);
  } catch (e) {
    toast(e.message || "The recipe didn't change", 'error');
  }
}

export async function openRecipe(id) {
  try {
    const r = await api.post('api/cook', { recipeId: id }, { quiet: true });
    setCook(r && r.cook);
    return true;
  } catch (e) {
    toast(e.message || "Couldn't open that recipe", 'error');
    return false;
  }
}

async function closeRecipe() {
  setCook(null);
  try {
    await api.del('api/cook', { quiet: true });
  } catch {
    /* closed here either way; the hub closes it after a while */
  }
}

/** The screen stays on while a recipe is open: the wall app's own wake (it also lifts its dimming), and the
 * browser's wake lock elsewhere. */
function useKeepAwake(on) {
  useEffect(() => {
    if (!on) return undefined;
    let lock = null;
    let gone = false;
    const nudge = () => {
      const n = window.NewmanWall;
      if (n && n.native && typeof n.wake === 'function') n.wake();
    };
    const grab = async () => {
      try {
        if (navigator.wakeLock && document.visibilityState === 'visible') {
          lock = await navigator.wakeLock.request('screen');
          if (gone) lock.release().catch(() => {});
        }
      } catch {
        /* not allowed here */
      }
    };
    nudge();
    grab();
    const t = setInterval(nudge, 30000);
    const vis = () => document.visibilityState === 'visible' && grab();
    document.addEventListener('visibilitychange', vis);
    return () => {
      gone = true;
      clearInterval(t);
      document.removeEventListener('visibilitychange', vis);
      if (lock) lock.release().catch(() => {});
    };
  }, [on]);
}

function Picture({ src, className = '' }) {
  const [bad, setBad] = useState(false);
  useEffect(() => setBad(false), [src]);
  return <span className={'ck-pic ' + className}>{src && !bad ? <img src={musicUrl(src)} alt="" onError={() => setBad(true)} /> : <Icon name="recipe" size={30} />}</span>;
}

function minutes(m) {
  if (!m) return '';
  const h = Math.floor(m / 60);
  return h ? `${h} h${m % 60 ? ' ' + (m % 60) + ' min' : ''}` : `${m} min`;
}

export function CookEngine() {
  const { cook } = useCook();
  const load = useCallback(async () => {
    try {
      const r = await api.get('api/cook', { quiet: true });
      setCook(r && r.cook);
    } catch {
      /* an older hub, or offline */
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  useLive((m) => {
    if (m.type === 'cook') setCook(m.cook);
    if (m.type === 'reconnected') load();
  });
  // The hub closes cook mode after hours untouched; so does the screen.
  const closesIn = cook ? cook.closesIn : 0;
  useEffect(() => {
    if (!cook || !closesIn) return undefined;
    const t = setTimeout(() => setCook(null), Math.min(closesIn * 1000, 2 ** 31 - 1));
    return () => clearTimeout(t);
  }, [cook, closesIn]);
  useKeepAwake(!!cook);
  return cook ? <CookSheet cook={cook} /> : null;
}

function CookSheet({ cook }) {
  const r = cook.recipe;
  const steps = cook.steps || [];
  const cur = Math.max(0, Math.min(steps.length - 1, cook.step || 0));
  const list = useRef(null);
  useEffect(() => {
    const el = list.current && list.current.querySelector(`[data-step="${cur}"]`);
    if (el && el.scrollIntoView) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }, [cur, r.id]);
  const go = (i) => {
    if (i < 0 || i >= steps.length) return;
    setCook({ ...cook, step: i, read: true });
    change({ step: i });
  };
  const serve = (d) => {
    const next = Math.max(1, Math.round(cook.servings) + d);
    if (next !== cook.servings) change({ servings: next });
  };
  const meta = [r.prepMinutes ? 'Prep ' + minutes(r.prepMinutes) : '', r.cookMinutes ? 'Cook ' + minutes(r.cookMinutes) : ''].filter(Boolean).join(' · ');
  const factor = cook.factor && cook.scaled ? (Number.isInteger(cook.factor) ? `×${cook.factor}` : `×${+cook.factor.toFixed(2)}`) : '';
  return (
    <div className="ck-screen" role="dialog" aria-modal="true" aria-label={'Cooking ' + r.title} data-wall-in-use>
      <header className="ck-head">
        <button type="button" className="wt-back ck-close" onClick={closeRecipe}>
          <Icon name="x" size={22} strokeWidth={2.4} /> Close
        </button>
        <Picture src={r.image} />
        <div className="ck-title-wrap">
          <h1 className="ck-title">{r.title}</h1>
          {meta ? <div className="ck-meta">{meta}</div> : null}
        </div>
        {r.baseServings ? (
          <div className={'ck-serves' + (cook.scaled ? ' is-scaled' : '')} role="group" aria-label="Servings">
            <button type="button" className="ck-round" onClick={() => serve(-1)} disabled={cook.servings <= 1} aria-label="Fewer servings">
              <Icon name="minus" size={20} strokeWidth={2.6} />
            </button>
            <span className="ck-serves-text">
              <b>{cook.servingsText}</b> serving{cook.servings === 1 ? '' : 's'}
              {cook.scaled ? (
                <span className="ck-scaled">
                  {factor}
                  <span className="ck-scaled-base"> · recipe makes {r.baseServings}</span>
                </span>
              ) : null}
            </span>
            <button type="button" className="ck-round" onClick={() => serve(1)} disabled={cook.servings >= 100} aria-label="More servings">
              <Icon name="plus" size={20} strokeWidth={2.6} />
            </button>
          </div>
        ) : null}
      </header>
      <div className="ck-body">
        <section className="ck-steps" ref={list} aria-label="Steps">
          {steps.length ? (
            steps.map((text, i) => (
              <button
                key={i}
                type="button"
                data-step={i}
                className={'ck-step' + (i === cur ? ' is-now' : i < cur && cook.read ? ' is-done' : '')}
                aria-current={i === cur ? 'step' : undefined}
                onClick={() => go(i)}
              >
                <span className="ck-step-n">{i + 1}</span>
                <span className="ck-step-text">{text}</span>
              </button>
            ))
          ) : (
            <div className="wt-muted">This recipe has no steps.</div>
          )}
        </section>
        <aside className="ck-ings" aria-label="Ingredients">
          <h2 className="ck-h2">Ingredients</h2>
          <ul>
            {(cook.ingredients || []).map((g, i) => (
              <li key={i}>
                {g.amount ? <b className="ck-amt">{g.amount}</b> : null}
                <span>
                  {g.name}
                  {g.notes ? <span className="ck-note">, {g.notes}</span> : null}
                </span>
              </li>
            ))}
          </ul>
        </aside>
      </div>
      {steps.length ? (
        <footer className="ck-foot">
          <button type="button" className="ck-nav" onClick={() => go(cur - 1)} disabled={cur <= 0}>
            <Icon name="chevronLeft" size={24} strokeWidth={2.6} /> Previous
          </button>
          <span className="ck-where">
            Step <b>{cur + 1}</b> of {steps.length}
          </span>
          <button type="button" className="ck-nav ck-nav--next" onClick={() => go(cur + 1)} disabled={cur >= steps.length - 1}>
            Next <Icon name="chevronRight" size={24} strokeWidth={2.6} />
          </button>
        </footer>
      ) : null}
    </div>
  );
}

/* ---------------- the Recipes tile ---------------- */

export function RecipesTile() {
  const [q, setQ] = useState('');
  const dq = useDebounced(q.trim(), 250);
  const got = useApi('api/recipes' + (dq ? '?q=' + enc(dq) : ''));
  useLive((m) => {
    if (m.type === 'reconnected') got.reload();
  });
  const d = got.data;
  if (!d) return <div className="wt-muted">{got.error ? "Couldn't load the recipes." : 'Loading…'}</div>;
  if (!d.status) {
    return (
      <div className="ck-tile ck-tile--off">
        <Icon name="recipe" size={32} />
        <span className="wt-muted">Recipes aren't connected.</span>
      </div>
    );
  }
  return (
    <div className="ck-tile">
      <label className="mu-search">
        <Icon name="search" size={18} />
        <input type="search" placeholder="Search recipes" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search recipes" />
        {q ? (
          <button type="button" className="mu-clear" onClick={() => setQ('')} aria-label="Clear">
            <Icon name="x" size={16} strokeWidth={2.4} />
          </button>
        ) : null}
      </label>
      <div className="ck-tile-list">
        {(d.recipes || []).map((x) => (
          <button key={x.id} type="button" className="ck-row" onClick={() => openRecipe(x.id)}>
            <Picture src={x.image} className="ck-pic--sm" />
            <span className="ck-row-text">
              <span className="ck-row-title">{x.title}</span>
              <span className="ck-row-sub">{[x.totalMinutes ? minutes(x.totalMinutes) : '', x.steps ? `${x.steps} step${x.steps === 1 ? '' : 's'}` : ''].filter(Boolean).join(' · ')}</span>
            </span>
          </button>
        ))}
        {!(d.recipes || []).length ? <div className="wt-muted mu-none">{dq ? 'Nothing matches.' : 'No recipes yet.'}</div> : null}
      </div>
    </div>
  );
}
