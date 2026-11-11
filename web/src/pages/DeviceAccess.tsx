import { useEffect, useRef, useState } from 'react';
import { commandResult, deviceCommand } from '../api/commands';

type Grant = { grantee: string; created: number };
export type Administrator = { id: string; label: string };

export function DeviceAccess({ origin, device, account, administrators }: {
  origin: string;
  device?: { id: string; name: string; access?: 'owner' | 'delegated' };
  account: string;
  administrators: Administrator[];
}) {
  const [grants, setGrants] = useState<Grant[]>([]);
  const [target, setTarget] = useState('');
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const generation = useRef(0);
  const owner = device?.access === 'owner';
  const candidates = administrators.filter(a => a.id !== account && !grants.some(g => g.grantee === a.id));

  const refresh = async (version = generation.current) => {
    if (!device || !origin || !owner) return;
    setLoading(true); setError(''); setLoaded(false);
    try {
      const result = commandResult(await deviceCommand(origin, device.id, 'grants')).data;
      if (version !== generation.current) return;
      if (!Array.isArray(result)) throw new Error('授权列表格式异常，请刷新后重试。');
      setGrants(result); setLoaded(true);
    } catch (e) {
      if (version === generation.current) setError((e as Error).message);
    } finally {
      if (version === generation.current) setLoading(false);
    }
  };

  useEffect(() => {
    const version = ++generation.current;
    setGrants([]); setTarget(''); setError(''); setNotice('');
    setLoaded(false); setBusy(false); setLoading(false);
    void refresh(version);
    return () => { generation.current++; };
  }, [origin, device?.id, device?.access]);

  const change = async (action: 'grant' | 'ungrant', grantee: string) => {
    if (!device || !owner || !loaded || busy || loading) return;
    const version = generation.current;
    setBusy(true); setError(''); setNotice('');
    try {
      commandResult(await deviceCommand(origin, device.id, action, { grantee }));
      if (version !== generation.current) return;
      setTarget('');
      setNotice(action === 'grant' ? '授权已生效。对方可以查看和控制这台电脑。' : '授权已撤销。已发出操作是否完成，仍以任务记录为准。');
      await refresh(version);
    } catch (e) {
      if (version === generation.current) {
        setLoaded(false);
        setError(`${(e as Error).message} 请刷新授权名单核对结果，暂不重复提交。`);
      }
    } finally {
      if (version === generation.current) setBusy(false);
    }
  };

  return <section className="panel stack" aria-label="设备授权">
    <h3>设备授权</h3>
    {!device ? <p>先选择一台电脑，再管理它的授权。</p> : !owner ? <p>
      {device.access === 'delegated' ? '你通过授权使用这台电脑。只有设备所有者可以添加、查看和撤销其他人的授权。' : '无法确认设备所有者权限，请刷新设备列表。'}
    </p> : <>
      <p>把 {device.name} 的查看与控制权限授予另一位管理员。对方可以操作已有 Agent 会话，但不能转授权或撤销设备。原任务仍向原会话回传。</p>
      {error && <div className="banner error" role="alert">{error}</div>}
      {notice && <p role="status">{notice}</p>}
      <div className="form-grid">
        <label className="field"><span>授权给</span>
          <select value={target} disabled={!loaded || loading || busy || !candidates.length} onChange={e => setTarget(e.target.value)}>
            <option value="">{loading ? '正在读取授权…' : candidates.length ? '选择管理员' : '没有其他可授权的管理员'}</option>
            {candidates.map(a => <option key={a.id} value={a.id}>{a.label}</option>)}
          </select>
        </label>
        <div className="row">
          <button className="primary" disabled={!target || !loaded || busy || loading} onClick={() => void change('grant', target)}>授予权限</button>
          <button disabled={busy || loading} onClick={() => void refresh()}>刷新授权</button>
        </div>
      </div>
      {loading ? <p role="status">正在读取授权名单…</p> : loaded && !grants.length ? <p>尚未授权给其他管理员。</p> : null}
      {loaded && grants.map(grant => <div className="row" key={grant.grantee}>
        <span className="break-text">{administrators.find(a => a.id === grant.grantee)?.label || `账号 ${grant.grantee}（已不在管理员名单）`} · {new Date(grant.created * 1000).toLocaleString()}</span>
        <button disabled={busy || loading} onClick={() => {
          if (confirm(`撤销账号 ${grant.grantee} 对 ${device.name} 的访问权限？已在电脑执行的操作不会自动撤回。`)) void change('ungrant', grant.grantee);
        }}>撤销授权</button>
      </div>)}
      <small>授权保存在服务端，电脑离线时也可以管理。目标账号必须已配置为 AstrBot 管理员。</small>
    </>}
  </section>;
}
