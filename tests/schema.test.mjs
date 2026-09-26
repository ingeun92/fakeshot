// supabase/schema.sql을 실제 Postgres(PGlite)에 올려, 브라우저(anon 역할)가 호출하는
// 경로만으로 파일럿 흐름과 보안 경계를 검증한다.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { PGlite } from '@electric-sql/pglite';

const schema = readFileSync(new URL('../supabase/schema.sql', import.meta.url), 'utf8');

async function freshDb() {
  const db = new PGlite();
  // Supabase는 public 스키마의 새 테이블과 함수에 anon 권한을 자동 부여한다. 같은 조건을 재현한다.
  await db.exec(`
    create role anon nologin; create role authenticated nologin;
    grant usage on schema public to anon, authenticated;
    alter default privileges in schema public grant all on tables to anon, authenticated;
    alter default privileges in schema public grant all on sequences to anon, authenticated;
    alter default privileges in schema public grant execute on functions to anon, authenticated;
  `);
  await db.exec(schema);
  await db.exec(`
    insert into items (item_id, set_name, half, label) values
      ('a1', 'A', 1, 'ai'), ('a2', 'A', 2, 'real'),
      ('b1', 'B', 1, 'ai'), ('b2', 'B', 2, 'real'),
      ('p1', 'P', null, 'real');
    insert into participants (token, order_group) values ('tokAB', 'AB'), ('tokBA', 'BA');
  `);
  return db;
}

// anon 역할로 함수를 호출한다. 브라우저가 PostgREST를 통해 하는 일과 같은 권한이다.
async function asAnon(db, sql, params = []) {
  await db.exec('set role anon');
  try {
    const res = await db.query(sql, params);
    return res.rows;
  } finally {
    await db.exec('reset role');
  }
}

async function call(db, fn, args) {
  const placeholders = args.map((_, i) => `$${i + 1}`).join(', ');
  const rows = await asAnon(db, `select ${fn}(${placeholders}) as r`, args);
  return rows[0].r;
}

const device = JSON.stringify({ dpr: 2 });
const submit = (db, token, s, phase, pos, item, outcome, choice) =>
  call(db, 'submit_response', [token, s, phase, pos, item, outcome, choice,
    1200, false, false, 390, 844, 358, new Date().toISOString()]);

async function passGap(db, token) {
  await db.exec(`update sessions set completed_at = now() - interval '13 hours'
                 where token = '${token}' and session_no = 1`);
}

test('anon은 정답 테이블과 내부 채점 함수에 직접 접근할 수 없다', async () => {
  const db = await freshDb();
  for (const table of ['items', 'responses', 'participants', 'sessions']) {
    const rows = await asAnon(db, `select * from ${table}`).catch((e) => {
      assert.match(e.message, /permission denied/);
      return [];
    });
    assert.deepEqual(rows, [], `${table} leaked to anon`);
  }
  await assert.rejects(asAnon(db, `select fs_session_score('tokAB', 1::smallint)`), /permission denied/);
  await assert.rejects(
    asAnon(db, `insert into responses (token, session_no, phase, position, item_id, outcome)
                values ('tokAB', 1, 'main', 0, 'a1', 'timeout')`),
    /permission denied/);
});

test('모르는 토큰은 거부된다', async () => {
  const db = await freshDb();
  assert.deepEqual(await call(db, 'get_state', ['nope']), { ok: false, error: 'unknown_token' });
  const started = await call(db, 'start_session', ['nope', 1, device]);
  assert.equal(started.ok, false);
});

test('AB 그룹은 A 세트로, BA 그룹은 B 세트로 1차를 시작하고 2차는 반대 세트다', async () => {
  const db = await freshDb();
  assert.equal((await call(db, 'start_session', ['tokAB', 1, device])).set_name, 'A');
  assert.equal((await call(db, 'start_session', ['tokBA', 1, device])).set_name, 'B');
  await call(db, 'finish_session', ['tokAB', 1]);
  await call(db, 'finish_session', ['tokBA', 1]);
  await passGap(db, 'tokAB');
  await passGap(db, 'tokBA');
  assert.equal((await call(db, 'start_session', ['tokAB', 2, device])).set_name, 'B');
  assert.equal((await call(db, 'start_session', ['tokBA', 2, device])).set_name, 'A');
});

test('1차 완료 직후에는 2차를 시작할 수 없고 간격이 지나면 열린다', async () => {
  const db = await freshDb();
  await call(db, 'start_session', ['tokAB', 1, device]);
  const afterFinish = await call(db, 'finish_session', ['tokAB', 1]);
  assert.equal(afterFinish.next, 'wait');
  assert.ok(afterFinish.wait_until);
  const early = await call(db, 'start_session', ['tokAB', 2, device]);
  assert.deepEqual(early, { ok: false, error: 'not_allowed', next: 'wait' });
  await passGap(db, 'tokAB');
  assert.equal((await call(db, 'get_state', ['tokAB'])).next, 'session2');
  assert.equal((await call(db, 'start_session', ['tokAB', 2, device])).ok, true);
});

test('1차를 건너뛰고 2차를 시작할 수 없다', async () => {
  const db = await freshDb();
  const r = await call(db, 'start_session', ['tokAB', 2, device]);
  assert.equal(r.ok, false);
  assert.equal(r.next, 'session1');
});

test('다른 세트의 문항이나 연습 문항을 본 문항으로 제출하면 거부된다', async () => {
  const db = await freshDb();
  await call(db, 'start_session', ['tokAB', 1, device]);   // A 세트
  assert.equal((await submit(db, 'tokAB', 1, 'main', 0, 'b1', 'answered', 3)).error, 'bad_item');
  assert.equal((await submit(db, 'tokAB', 1, 'main', 0, 'p1', 'answered', 3)).error, 'bad_item');
  assert.equal((await submit(db, 'tokAB', 1, 'practice', 0, 'a1', 'answered', 3)).error, 'bad_item');
  assert.equal((await submit(db, 'tokAB', 1, 'main', 0, 'a1', 'answered', 3)).ok, true);
  assert.equal((await submit(db, 'tokAB', 1, 'practice', 0, 'p1', 'answered', 1)).ok, true);
});

test('본 문항 위치가 세션당 문항 수를 넘으면 거부된다', async () => {
  const db = await freshDb();
  await call(db, 'start_session', ['tokAB', 1, device]);
  assert.equal((await submit(db, 'tokAB', 1, 'main', 30, 'a1', 'answered', 3)).error, 'bad_item');
  assert.equal((await submit(db, 'tokAB', 1, 'main', 29, 'a1', 'answered', 3)).ok, true);
});

test('같은 응답을 재전송해도 한 줄만 저장된다', async () => {
  const db = await freshDb();
  await call(db, 'start_session', ['tokAB', 1, device]);
  assert.equal((await submit(db, 'tokAB', 1, 'main', 0, 'a1', 'answered', 4)).ok, true);
  const again = await submit(db, 'tokAB', 1, 'main', 0, 'a1', 'answered', 4);
  assert.deepEqual(again, { ok: true, duplicate: true });
  const { rows } = await db.query(`select count(*)::int as n from responses`);
  assert.equal(rows[0].n, 1);
});

test('응답을 골랐다면서 선택값이 없거나 범위를 벗어나면 거부된다', async () => {
  const db = await freshDb();
  await call(db, 'start_session', ['tokAB', 1, device]);
  assert.equal((await submit(db, 'tokAB', 1, 'main', 0, 'a1', 'answered', null)).error, 'invalid_values');
  assert.equal((await submit(db, 'tokAB', 1, 'main', 1, 'a1', 'answered', 5)).error, 'invalid_values');
  assert.equal((await submit(db, 'tokAB', 1, 'main', 2, 'a1', 'bogus', null)).error, 'invalid_values');
});

test('시간 초과 응답은 선택값을 보내도 선택값 없이 저장된다', async () => {
  const db = await freshDb();
  await call(db, 'start_session', ['tokAB', 1, device]);
  assert.equal((await submit(db, 'tokAB', 1, 'main', 0, 'a1', 'timeout', 2)).ok, true);
  const { rows } = await db.query(`select outcome, choice from responses`);
  assert.deepEqual(rows, [{ outcome: 'timeout', choice: null }]);
});

test('완료된 세션에는 새 응답을 넣을 수 없다', async () => {
  const db = await freshDb();
  await call(db, 'start_session', ['tokAB', 1, device]);
  await call(db, 'finish_session', ['tokAB', 1]);
  assert.equal((await submit(db, 'tokAB', 1, 'main', 0, 'a1', 'answered', 3)).error, 'session_closed');
});

test('진행 중 새로고침하면 이어하기 상태와 마지막 본 문항 위치를 돌려준다', async () => {
  const db = await freshDb();
  await call(db, 'start_session', ['tokAB', 1, device]);
  await submit(db, 'tokAB', 1, 'practice', 0, 'p1', 'answered', 1);
  let st = await call(db, 'get_state', ['tokAB']);
  assert.equal(st.next, 'resume');
  assert.deepEqual(st.resume, { session_no: 1, set_name: 'A', max_main_position: null });
  await submit(db, 'tokAB', 1, 'main', 0, 'a1', 'answered', 3);
  await submit(db, 'tokAB', 1, 'main', 1, 'a2', 'timeout', null);
  st = await call(db, 'get_state', ['tokAB']);
  assert.equal(st.resume.max_main_position, 1);
  const again = await call(db, 'start_session', ['tokAB', 1, device]);
  assert.deepEqual(again, { ok: true, set_name: 'A', resumed: true });
});

test('세션이 끝나면 그 세션 점수가 공개되고 본 문항만 센다', async () => {
  const db = await freshDb();
  await call(db, 'start_session', ['tokAB', 1, device]);
  await submit(db, 'tokAB', 1, 'practice', 0, 'p1', 'answered', 1);   // 연습: 정답이지만 제외
  await submit(db, 'tokAB', 1, 'main', 0, 'a1', 'answered', 3);       // ai → 아마 AI: 정답
  await submit(db, 'tokAB', 1, 'main', 1, 'a2', 'answered', 4);       // real → 확실히 AI: 오답
  const s1 = await call(db, 'finish_session', ['tokAB', 1]);
  assert.deepEqual(s1.scores, { session1: { correct: 1, answered: 2, timeouts: 0, total: 2 }, session2: null });
  await passGap(db, 'tokAB');
  await call(db, 'start_session', ['tokAB', 2, device]);
  await submit(db, 'tokAB', 2, 'main', 0, 'b1', 'answered', 1);       // ai → 확실히 실물: 오답
  await submit(db, 'tokAB', 2, 'main', 1, 'b2', 'answered', 2);       // real → 아마 실물: 정답
  await submit(db, 'tokAB', 2, 'main', 2, 'b1', 'timeout', null);
  const s2 = await call(db, 'finish_session', ['tokAB', 2]);
  assert.equal(s2.next, 'done');
  assert.deepEqual(s2.scores, {
    session1: { correct: 1, answered: 2, timeouts: 0, total: 2 },
    session2: { correct: 1, answered: 2, timeouts: 1, total: 3 },
  });
});

test('진행 중인 세션의 점수는 새로고침해도 알 수 없다', async () => {
  const db = await freshDb();
  await call(db, 'start_session', ['tokAB', 1, device]);
  await submit(db, 'tokAB', 1, 'main', 0, 'a1', 'answered', 3);
  const mid1 = await call(db, 'get_state', ['tokAB']);
  assert.deepEqual(mid1.scores, { session1: null, session2: null });
  await call(db, 'finish_session', ['tokAB', 1]);
  await passGap(db, 'tokAB');
  await call(db, 'start_session', ['tokAB', 2, device]);
  await submit(db, 'tokAB', 2, 'main', 0, 'b1', 'answered', 3);
  const mid2 = await call(db, 'get_state', ['tokAB']);
  assert.equal(mid2.scores.session2, null);
  assert.equal(mid2.scores.session1.correct, 1);
});
