import { useState } from 'react';
import { api, setCsrf } from '../api/client';

export function LoginPage({ onLogin }: { onLogin: () => void }) {
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  return <main className="login">
    <form className="panel" onSubmit={e => {
      e.preventDefault(); setBusy(true); setError('');
      api<{ csrf: string }>('/login', 'POST', { password })
        .then(r => { setCsrf(r.csrf); setPassword(''); onLogin(); })
        .catch(err => setError((err as Error).message))
        .finally(() => setBusy(false));
    }}>
      <h1>石英后台</h1>
      <label className="field"><span>管理员密码</span>
        <input type="password" autoComplete="current-password" required autoFocus value={password} onChange={e => setPassword(e.target.value)} /></label>
      {error && <div role="alert" className="banner error">{error}</div>}
      <button className="primary" disabled={busy}>登录</button>
      <small>使用管理员密码登录石英后台。</small>
    </form>
  </main>;
}
