// ?mock=1 로 열면 서버 없이 localStorage에서 서버 함수를 흉내 낸다.
// 화면 흐름 점검용이다. 정답을 모르므로 점수는 계산하지 않는다.
import { hashString } from './lib.js';

export function createMockBackend({ gapSeconds = 0, mainPerSession = 30 } = {}) {
  const key = 'fs:mock:db';
  const load = () => JSON.parse(localStorage.getItem(key) ?? '{"sessions":{},"responses":{}}');
  const save = (db) => localStorage.setItem(key, JSON.stringify(db));
  const orderOf = (token) => (hashString(token) % 2 === 0 ? 'AB' : 'BA');
  const setFor = (order, s) => ((order === 'AB') === (s === 1) ? 'A' : 'B');

  function state(token) {
    const db = load();
    const s1 = db.sessions[`${token}:1`];
    const s2 = db.sessions[`${token}:2`];
    let next; let cur = null; let waitUntil = null;
    if (!s1) next = 'session1';
    else if (s1.status === 'in_progress') { next = 'resume'; cur = s1; }
    else if (!s2) {
      waitUntil = new Date(s1.completed_at + gapSeconds * 1000).toISOString();
      next = Date.now() >= s1.completed_at + gapSeconds * 1000 ? 'session2' : 'wait';
    } else if (s2.status === 'in_progress') { next = 'resume'; cur = s2; }
    else next = 'done';
    let resume = null;
    if (cur) {
      const mains = Object.values(db.responses)
        .filter((r) => r.p_token === token && r.p_session_no === cur.session_no && r.p_phase === 'main')
        .map((r) => r.p_position);
      resume = { session_no: cur.session_no, set_name: cur.set_name,
        max_main_position: mains.length ? Math.max(...mains) : null };
    }
    const scoreOf = (s) => {
      if (s?.status !== 'completed') return null;
      const mains = Object.values(db.responses)
        .filter((r) => r.p_token === token && r.p_session_no === s.session_no && r.p_phase === 'main');
      return { correct: null, answered: mains.filter((r) => r.p_outcome === 'answered').length,
        timeouts: mains.filter((r) => r.p_outcome === 'timeout').length, total: mains.length };
    };
    const scores = { session1: scoreOf(s1), session2: scoreOf(s2) };
    return { ok: true, order_group: orderOf(token), next, wait_until: waitUntil, resume, scores, mock: true };
  }

  return async function rpc(fn, args) {
    await new Promise((r) => setTimeout(r, 30));
    const db = load();
    const token = args.p_token;
    if (fn === 'get_state') return state(token);
    if (fn === 'start_session') {
      const st = state(token);
      if (st.next === 'resume' && st.resume.session_no === args.p_session_no) {
        return { ok: true, set_name: st.resume.set_name, resumed: true };
      }
      if (st.next !== `session${args.p_session_no}`) return { ok: false, error: 'not_allowed', next: st.next };
      const setName = setFor(st.order_group, args.p_session_no);
      db.sessions[`${token}:${args.p_session_no}`] = {
        session_no: args.p_session_no, set_name: setName, status: 'in_progress', device: args.p_device };
      save(db);
      return { ok: true, set_name: setName, resumed: false };
    }
    if (fn === 'submit_response') {
      const k = `${token}:${args.p_session_no}:${args.p_phase}:${args.p_position}`;
      if (db.responses[k]) return { ok: true, duplicate: true };
      if (args.p_phase === 'main' && args.p_position >= mainPerSession) return { ok: false, error: 'bad_item' };
      db.responses[k] = args;
      save(db);
      return { ok: true };
    }
    if (fn === 'finish_session') {
      const s = db.sessions[`${token}:${args.p_session_no}`];
      if (!s) return { ok: false, error: 'no_session' };
      s.status = 'completed';
      s.completed_at ??= Date.now();
      save(db);
      return state(token);
    }
    throw new Error(`unknown rpc ${fn}`);
  };
}

export function resetMock() {
  for (const k of Object.keys(localStorage)) if (k.startsWith('fs:')) localStorage.removeItem(k);
}
