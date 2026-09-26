// Supabase RPC 호출과 응답 재전송 큐.
// 응답은 문항마다 즉시 보내고, 실패하면 localStorage에 남겨 두었다가 다시 보낸다.

const PERMANENT_ERRORS = new Set(['bad_item', 'invalid_values', 'session_closed', 'no_session']);

export function storage() {
  try {
    const k = '__fs_probe__';
    localStorage.setItem(k, '1');
    localStorage.removeItem(k);
    return localStorage;
  } catch {
    return null;
  }
}

export function createSupabaseBackend(url, key) {
  const headers = { 'Content-Type': 'application/json', apikey: key };
  if (key.startsWith('eyJ')) headers.Authorization = `Bearer ${key}`;   // 구형 JWT anon 키
  return async function rpc(fn, args) {
    const res = await fetch(`${url.replace(/\/$/, '')}/rest/v1/rpc/${fn}`, {
      method: 'POST',
      headers,
      body: JSON.stringify(args),
    });
    if (!res.ok) throw new Error(`rpc ${fn} failed: ${res.status}`);
    return res.json();
  };
}

export function createApi(rpc, token) {
  const store = storage();
  const qKey = `fs:q:${token}`;
  let queue = [];
  try { queue = JSON.parse(store?.getItem(qKey) ?? '[]'); } catch { queue = []; }
  const persist = () => { try { store?.setItem(qKey, JSON.stringify(queue)); } catch { /* 저장 공간 부족 */ } };
  const dropped = [];
  let flushing = null;

  async function flushOnce() {
    while (queue.length) {
      const r = queue[0];
      let res;
      try {
        res = await rpc('submit_response', r);
      } catch {
        return false;   // 네트워크 오류: 큐를 그대로 두고 나중에 다시 보낸다
      }
      if (!res.ok && !PERMANENT_ERRORS.has(res.error)) return false;
      if (!res.ok) dropped.push({ response: r, error: res.error });
      queue.shift();
      persist();
    }
    return true;
  }

  function flush() {
    flushing ??= flushOnce().finally(() => { flushing = null; });
    return flushing;
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  return {
    localStore: store,
    dropped,
    pending: () => queue.length,
    getState: () => rpc('get_state', { p_token: token }),
    startSession: (sessionNo, device) =>
      rpc('start_session', { p_token: token, p_session_no: sessionNo, p_device: device }),
    finishSession: (sessionNo) =>
      rpc('finish_session', { p_token: token, p_session_no: sessionNo }),
    submit(response) {
      queue.push({ p_token: token, ...response });
      persist();
      flush();
    },
    flush,
    // 큐가 빌 때까지 재시도한다. 끝내 실패하면 false.
    async flushAll(attempts = 8) {
      for (let i = 0; i < attempts; i++) {
        if (await flush()) return true;
        await sleep(Math.min(1000 * 2 ** i, 8000));
      }
      return queue.length === 0;
    },
  };
}
