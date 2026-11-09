let csrf = '';

export function setCsrf(value: string) { csrf = value; }

export async function api<T = unknown>(path: string, method = 'GET', data?: unknown): Promise<T> {
  const multipart = data instanceof FormData;
  const response = await fetch('/api/v1' + path, {
    method, credentials: 'same-origin',
    headers: {
      ...(method === 'GET' ? {} : { 'X-CSRF-Token': csrf }),
      ...(data && !multipart ? { 'Content-Type': 'application/json' } : {})
    },
    body: data ? (multipart ? data : JSON.stringify(data)) : undefined,
  });
  const raw = await response.text();
  let body: any;
  try { body = raw ? JSON.parse(raw) : {}; }
  catch { body = { detail: response.ok ? '服务器返回的内容看不懂' : `服务器出错（${response.status}），稍后再试` }; }
  if(!response.ok) {
    if(response.status === 401 && path !== '/login') {
      csrf = '';
      window.dispatchEvent(new Event('signed-out'));
    }
    throw new Error(typeof body.detail === 'string' ? body.detail : '输入格式不正确，请检查后再试');
  }
  return body as T;
}
