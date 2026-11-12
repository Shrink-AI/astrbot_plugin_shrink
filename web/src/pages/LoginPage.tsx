import { useState } from 'react';
import { Gem, KeyRound, ShieldCheck } from 'lucide-react';
import { Alert, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field';
import { PasswordInput } from '@/components/password-input';
import { ThemeSwitch } from '@/components/theme-switch';
import { api, setCsrf } from '@/api/client';

export function LoginPage({ onLogin }: { onLogin: () => void }) {
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  return <div className="grid min-h-svh lg:grid-cols-2">
    <aside className="relative hidden flex-col justify-between overflow-hidden bg-zinc-900 p-10 text-zinc-100 lg:flex">
      <div className="relative z-10 flex items-center gap-2 text-sm font-medium">
        <div className="flex size-8 items-center justify-center rounded-lg bg-zinc-100 text-zinc-900"><Gem className="size-4" /></div>
        石英后台
      </div>
      <div className="relative z-10 flex flex-col gap-4">
        <p className="text-2xl leading-snug font-medium text-balance">群聊里的记忆、人物档案与学习记录，都收在这一处。</p>
        <ul className="flex flex-col gap-2 text-sm text-zinc-400">
          <li className="flex items-center gap-2"><ShieldCheck className="size-4" />只有管理员密码能进入</li>
          <li className="flex items-center gap-2"><KeyRound className="size-4" />所有改动都会记进整理记录</li>
        </ul>
      </div>
      <p className="relative z-10 text-xs text-zinc-500">插件自带的后台，数据留在本机。</p>
      <div className="pointer-events-none absolute -end-24 -top-24 size-72 rounded-full bg-zinc-800/60 blur-2xl" />
    </aside>
    <main className="relative flex items-center justify-center px-4 py-10">
      <div className="absolute end-3 top-3"><ThemeSwitch /></div>
      <form className="flex w-full max-w-[360px] flex-col gap-5" onSubmit={e => {
        e.preventDefault(); setBusy(true); setError('');
        api<{ csrf: string }>('/login', 'POST', { password })
          .then(r => { setCsrf(r.csrf); setPassword(''); onLogin(); })
          .catch(err => setError((err as Error).message))
          .finally(() => setBusy(false));
      }}>
        <div className="flex flex-col gap-1 text-center lg:text-start">
          <h1 className="text-xl font-semibold tracking-tight">登录后台</h1>
          <p className="text-sm text-muted-foreground">输入管理员密码继续。</p>
        </div>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">管理员</CardTitle>
            <CardDescription>密码在插件配置里，登录后可以修改。</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="admin-password">管理员密码</FieldLabel>
                <PasswordInput id="admin-password" autoComplete="current-password" required autoFocus
                  value={password} onChange={e => setPassword(e.target.value)} />
                <FieldDescription>忘记密码时，可以在插件数据目录里重置。</FieldDescription>
              </Field>
            </FieldGroup>
            {error && <Alert variant="destructive"><AlertTitle>{error}</AlertTitle></Alert>}
            <Button type="submit" className="w-full" disabled={busy}>登录</Button>
          </CardContent>
        </Card>
      </form>
    </main>
  </div>;
}