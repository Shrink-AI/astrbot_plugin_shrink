import { api } from './client';

export function requestId(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
}

export function commandResult(value: any): { pending: boolean; data: any } {
  if (value?.state === 'pending' || (value?.command_id && value.result === null)) {
    return { pending: true, data: null };
  }
  const data = value && Object.prototype.hasOwnProperty.call(value, 'result') ? value.result : value;
  if (data?.ok === false || value?.ok === false) throw new Error(data?.error || value?.error || '操作失败');
  return { pending: false, data };
}

export async function deviceCommand(session: string, device: string, action: string, args: Record<string, unknown> = {}, id = requestId()) {
  return api<any>('/operations/plugin-capabilities', 'POST', {
    capability: 'computer.control', action, session,
    arguments: { device_id: device, request_id: id, ...args },
  });
}
