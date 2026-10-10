// Accounts & sign-in. GET api/auth/state decides what the dashboard shows:
//   setupNeeded → first-run setup · signedIn false → sign-in page · else the dashboard.
// Any call that comes back 401 {signIn: true} (an expired session) returns to sign-in.

import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { api, onApiEvent, toast } from './api.js';
import { stopLive } from './live.js';
import { Loading } from './components/Controls.jsx';
import { SetupPage, SignInPage } from './pages/SignIn.jsx';

export const AuthContext = createContext(null);

/** { user, session, where, isAdmin, refresh, signOut, state } */
export function useAuth() {
  return useContext(AuthContext) || { user: null, isAdmin: false, where: 'home', refresh: () => {}, signOut: () => {} };
}

export function AuthGate({ children }) {
  const [state, setState] = useState(null);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const s = await api.get('api/auth/state', { quiet: true });
      setState(s);
      setError(null);
      return s;
    } catch (e) {
      setError(e);
      return null;
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Hub unreachable at start: keep trying.
  useEffect(() => {
    if (state || !error) return undefined;
    const t = setTimeout(refresh, 5000);
    return () => clearTimeout(t);
  }, [state, error, refresh]);

  // A 401 {signIn:true} anywhere: the session is gone.
  useEffect(
    () =>
      onApiEvent((e) => {
        if (e.type !== 'auth' || e.signedIn) return;
        stopLive();
        setState((s) => (s && s.signedIn ? { ...s, signedIn: false, user: null, session: null } : s));
      }),
    [],
  );

  const signOut = useCallback(async () => {
    try {
      await api.post('api/auth/logout', {}, { quiet: true });
    } catch {
      /* signed out either way */
    }
    stopLive();
    setState((s) => ({ ...(s || {}), signedIn: false, user: null, session: null }));
    toast('Signed out', 'success');
  }, []);

  if (!state) {
    return (
      <div className="auth-screen">
        <Loading>{error ? "Can't reach the Newman hub — retrying…" : 'Connecting to the hub…'}</Loading>
      </div>
    );
  }
  if (state.setupNeeded) return <SetupPage where={state.where} onDone={refresh} />;
  if (!state.signedIn || !state.user) return <SignInPage where={state.where} awayPolicy={state.awayPolicy} onDone={refresh} />;

  const value = { state, user: state.user, session: state.session, where: state.where, isAdmin: state.user.role === 'admin', refresh, signOut };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
