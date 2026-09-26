// 실제 브라우저(Chromium)에서 ?mock=1 모드로 전체 흐름을 돈다.
// 서버 대신 localStorage 모의 백엔드를 쓰므로, 화면 흐름과 기록 내용을 검증한다.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium, devices } from 'playwright';

const WEB = fileURLToPath(new URL('../web/', import.meta.url));
const SHOTS = process.env.SHOTS_DIR;
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.json': 'application/json', '.jpg': 'image/jpeg', '.woff2': 'font/woff2' };

let server; let base; let browser;
const items = JSON.parse(await readFile(join(WEB, 'items.json'), 'utf8'));
const setOf = Object.fromEntries(items.map((i) => [i.id, i.set]));

before(async () => {
  server = createServer(async (req, res) => {
    const path = normalize(decodeURIComponent(new URL(req.url, 'http://x').pathname)).replace(/^\/+/, '');
    const file = path || 'index.html';
    try {
      const body = await readFile(join(WEB, file));
      res.writeHead(200, { 'Content-Type': TYPES[extname(file)] ?? 'application/octet-stream' });
      res.end(body);
    } catch { res.writeHead(404); res.end(); }
  });
  await new Promise((r) => server.listen(0, r));
  base = `http://127.0.0.1:${server.address().port}/`;
  browser = await chromium.launch();
});
after(async () => { await browser?.close(); server?.close(); });

async function phone() {
  const ctx = await browser.newContext({ ...devices['iPhone 13'] });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  return { ctx, page, errors };
}

const clickText = (page, text) => page.getByRole('button', { name: text }).click();

// 버튼이 켜질 때까지 기다렸다가 고르고, 입력이 잠길 때까지 기다린다.
async function answer(page, value) {
  await page.waitForSelector('.choice:enabled');
  await page.locator('.choice').nth(value - 1).click();
  await page.waitForFunction(() => document.querySelectorAll('.choice:enabled').length === 0);
}

async function answerMany(page, n, offset = 0) {
  for (let i = 0; i < n; i++) await answer(page, ((i + offset) % 4) + 1);
}

const mockDb = (page) => page.evaluate(() => JSON.parse(localStorage.getItem('fs:mock:db')));
const mains = (db, s) => Object.values(db.responses)
  .filter((r) => r.p_session_no === s && r.p_phase === 'main')
  .sort((a, b) => a.p_position - b.p_position);

test('두 세션을 끝까지 풀면 세트가 겹치지 않고 모든 위치가 한 번씩 기록된다', async () => {
  const { ctx, page, errors } = await phone();
  await page.goto(`${base}?mock=1&reset=1&p=e2e-full`);
  await clickText(page, '시작하기');
  await clickText(page, '연습 시작');
  await page.waitForSelector('canvas.stage');
  if (SHOTS) await page.waitForSelector('.choice:enabled').then(() => page.screenshot({ path: `${SHOTS}/trial.png` }));
  await answerMany(page, 3);
  await clickText(page, '본 문항 시작');

  await answer(page, 4);
  // 두 번째 문항에서 탭 이탈과 포커스 이탈을 흉내 낸다
  await page.waitForSelector('.choice:enabled');
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { value: true, configurable: true });
    document.dispatchEvent(new Event('visibilitychange'));
    Object.defineProperty(document, 'hidden', { value: false, configurable: true });
    window.dispatchEvent(new Event('blur'));
  });
  await answer(page, 1);
  // 세 번째 문항은 시간 초과
  await page.waitForSelector('.choice:enabled');
  await page.waitForFunction(() => document.querySelectorAll('.choice:enabled').length === 0, null, { timeout: 7000 });
  await answerMany(page, 27);

  await page.getByRole('heading', { name: '2차 테스트' }).waitFor();
  let db = await mockDb(page);
  const s1 = mains(db, 1);
  assert.deepEqual(s1.map((r) => r.p_position), [...Array(30).keys()]);
  const s1Set = db.sessions['e2e-full:1'].set_name;
  assert.ok(s1.every((r) => setOf[r.p_item_id] === s1Set), '1차 문항이 모두 1차 세트에 속해야 한다');
  assert.equal(new Set(s1.map((r) => r.p_item_id)).size, 30);
  assert.deepEqual([s1[0].p_outcome, s1[0].p_choice, s1[0].p_left_tab], ['answered', 4, false]);
  assert.deepEqual([s1[1].p_left_tab, s1[1].p_lost_focus], [true, true]);
  assert.deepEqual([s1[2].p_outcome, s1[2].p_choice, s1[2].p_rt_ms], ['timeout', null, null]);
  assert.ok(s1[0].p_rt_ms > 0 && s1[0].p_rt_ms < 5000);
  assert.ok(s1.every((r) => r.p_rendered_px <= 480 && r.p_rendered_px >= 200));
  const practice = Object.values(db.responses).filter((r) => r.p_phase === 'practice');
  assert.ok(practice.every((r) => setOf[r.p_item_id] === 'P'));

  await clickText(page, '시작하기');
  await clickText(page, '연습 시작');
  await answerMany(page, 3);
  await clickText(page, '본 문항 시작');
  await answerMany(page, 30, 1);
  await page.getByRole('heading', { name: '모두 끝났습니다' }).waitFor();
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/done.png` });

  db = await mockDb(page);
  const s2 = mains(db, 2);
  assert.deepEqual(s2.map((r) => r.p_position), [...Array(30).keys()]);
  const s2Set = db.sessions['e2e-full:2'].set_name;
  assert.notEqual(s2Set, s1Set);
  assert.ok(s2.every((r) => setOf[r.p_item_id] === s2Set));
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('fs:q:e2e-full')).length), 0,
    '보내지 못한 응답이 남으면 안 된다');
  assert.deepEqual(errors, []);
  await ctx.close();
});

test('풀던 중 새로고침하면 보던 문항은 버리고 다음 문항부터 이어간다, 1차 후에는 대기 화면', async () => {
  const { ctx, page, errors } = await phone();
  await page.goto(`${base}?mock=1&reset=1&gap=3600&p=e2e-resume`);
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/intro.png`, fullPage: true });
  await clickText(page, '시작하기');
  await clickText(page, '연습 시작');
  await answerMany(page, 3);
  await clickText(page, '본 문항 시작');
  await answerMany(page, 5);                       // 위치 0~4 응답
  await page.waitForSelector('.choice:enabled');   // 위치 5가 화면에 뜬 상태
  const shownItem = await page.evaluate(() => localStorage.getItem('fs:shown:e2e-resume:1'));
  assert.equal(shownItem, '5');
  await page.reload();

  await clickText(page, '이어하기');
  await page.getByRole('heading', { name: '이어서 진행합니다' }).waitFor();
  await clickText(page, '계속하기');
  await page.waitForSelector('.trial-top');
  assert.equal(await page.locator('.trial-top span').first().textContent(), '7 / 30');
  await answerMany(page, 24);

  await page.getByRole('heading', { name: '1차 완료!' }).waitFor();
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/wait.png` });
  const db = await mockDb(page);
  const s1 = mains(db, 1);
  assert.deepEqual(s1.map((r) => r.p_position), [...Array(30).keys()]);
  assert.equal(s1[5].p_outcome, 'abandoned');
  assert.equal(s1.filter((r) => r.p_outcome === 'abandoned').length, 1);

  await page.reload();   // 대기 중 다시 열어도 대기 화면
  await page.getByRole('heading', { name: '1차 완료!' }).waitFor();
  assert.deepEqual(errors, []);
  await ctx.close();
});

test('링크에 토큰이 없으면 안내 화면을 보여 준다', async () => {
  const { ctx, page } = await phone();
  await page.goto(base);
  await page.getByRole('heading', { name: '링크가 올바르지 않습니다' }).waitFor();
  await ctx.close();
});

// Supabase 모드: config.js와 서버 응답을 가로채 실제 통신 코드와 점수 화면을 확인한다.
async function withFakeSupabase(state) {
  const ctx = await browser.newContext({ ...devices['iPhone 13'] });
  const page = await ctx.newPage();
  const calls = [];
  await page.route('**/config.js', (route) => route.fulfill({
    contentType: 'text/javascript',
    body: `export const CONFIG = { SUPABASE_URL: 'https://fake.supabase.test', SUPABASE_KEY: 'sb_publishable_test',
      TIME_LIMIT_MS: 5000, BLANK_MS: 500, INPUT_GUARD_MS: 200, MAX_DISPLAY_PX: 480, MAIN_PER_SESSION: 30 };`,
  }));
  await page.route('https://fake.supabase.test/**', (route) => {
    const req = route.request();
    calls.push({ url: req.url(), headers: req.headers(), body: req.postDataJSON() });
    route.fulfill({ contentType: 'application/json', body: JSON.stringify(state) });
  });
  await page.goto(`${base}?p=tok123`);
  return { ctx, page, calls };
}

const s1Score = { correct: 21, answered: 28, timeouts: 2, total: 30 };
const s2Score = { correct: 19, answered: 30, timeouts: 0, total: 30 };

test('Supabase 모드: 1차를 마친 대기 화면에 1차 점수가 보이고 키는 apikey 헤더로만 간다', async () => {
  const { ctx, page, calls } = await withFakeSupabase({
    ok: true, order_group: 'AB', next: 'wait', wait_until: new Date(Date.now() + 3600e3).toISOString(),
    resume: null, scores: { session1: s1Score, session2: null } });
  await page.getByRole('heading', { name: '1차 완료!' }).waitFor();
  const text = await page.textContent('#app');
  assert.match(text, /30문항 중\s*21개 정답/);
  assert.match(text, /시간 초과 2개/);
  assert.equal(calls[0].url, 'https://fake.supabase.test/rest/v1/rpc/get_state');
  assert.deepEqual(calls[0].body, { p_token: 'tok123' });
  assert.equal(calls[0].headers.apikey, 'sb_publishable_test');
  assert.equal(calls[0].headers.authorization, undefined);
  await ctx.close();
});

test('Supabase 모드: 모두 마치면 합계와 세션별 점수가 보인다', async () => {
  const { ctx, page } = await withFakeSupabase({
    ok: true, order_group: 'AB', next: 'done', wait_until: null, resume: null,
    scores: { session1: s1Score, session2: s2Score } });
  await page.getByRole('heading', { name: '모두 끝났습니다' }).waitFor();
  const text = await page.textContent('#app');
  assert.match(text, /60문항 중\s*40개 정답/);
  assert.match(text, /1차 21개, 2차 19개, 시간 초과 2개/);
  await ctx.close();
});

test('문항 수가 다른 세트(샘플 점검용)에서도 안내 문구와 실제 문항 수가 맞다', async () => {
  const small = [
    ...items.filter((i) => i.set === 'P').slice(0, 2),
    ...items.filter((i) => i.set === 'A').slice(0, 4),
    ...items.filter((i) => i.set === 'B').slice(0, 4),
  ];
  const ctx = await browser.newContext({ ...devices['iPhone 13'] });
  const page = await ctx.newPage();
  await page.route('**/items.json', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(small) }));
  await page.goto(`${base}?mock=1&reset=1&p=e2e-small`);
  await page.getByRole('heading', { name: '진짜 사진일까, AI일까?' }).waitFor();
  assert.match(await page.textContent('#app'), /연습 2문항과 본 문항 4개\(약 1분\)/);
  await clickText(page, '시작하기');
  await page.getByRole('heading', { name: '연습 문항 2개' }).waitFor();
  await clickText(page, '연습 시작');
  await answerMany(page, 2);
  await page.getByRole('heading', { name: '본 문항 4개' }).waitFor();
  await clickText(page, '본 문항 시작');
  assert.equal(await page.locator('.trial-top span').first().textContent(), '1 / 4');
  await answerMany(page, 4);
  await page.getByRole('heading', { name: '2차 테스트' }).waitFor();
  assert.match(await page.textContent('#app'), /4문항을 풉니다/);
  assert.equal(mains(await mockDb(page), 1).length, 4);
  await ctx.close();
});

test('연습 문항이 없는 세트는 연습 단계를 건너뛴다', async () => {
  const noPractice = [...items.filter((i) => i.set === 'A').slice(0, 2), ...items.filter((i) => i.set === 'B').slice(0, 2)];
  const ctx = await browser.newContext({ ...devices['iPhone 13'] });
  const page = await ctx.newPage();
  await page.route('**/items.json', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(noPractice) }));
  await page.goto(`${base}?mock=1&reset=1&p=e2e-nopractice`);
  await page.getByRole('heading', { name: '진짜 사진일까, AI일까?' }).waitFor();
  assert.match(await page.textContent('#app'), /오늘은 본 문항 2개/);
  await clickText(page, '시작하기');
  await page.getByRole('heading', { name: '본 문항 2개' }).waitFor();
  await clickText(page, '본 문항 시작');
  await answerMany(page, 2);
  await page.getByRole('heading', { name: '2차 테스트' }).waitFor();
  await ctx.close();
});

test('타이머 테두리는 처음엔 기본 상태였다가 마지막 1.5초에 경고 상태가 되고, 다음 문항에서 원래대로 돌아온다', async () => {
  const small = [...items.filter((i) => i.set === 'A').slice(0, 2), ...items.filter((i) => i.set === 'B').slice(0, 2)];
  const ctx = await browser.newContext({ ...devices['iPhone 13'] });
  const page = await ctx.newPage();
  await page.route('**/items.json', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(small) }));
  await page.goto(`${base}?mock=1&reset=1&p=e2e-clock`);
  await clickText(page, '시작하기');
  await clickText(page, '본 문항 시작');
  await page.waitForSelector('.choice:enabled');
  const late = () => page.evaluate(() => document.querySelector('.clock .hand').classList.contains('late'));
  const stroke = () => page.evaluate(() => getComputedStyle(document.querySelector('.clock .hand')).stroke);
  assert.equal(await late(), false);
  const early = await stroke();
  await page.waitForTimeout(3000);                 // 약 3.2초 경과: 아직 1.5초 이상 남음
  assert.equal(await late(), false);
  await page.waitForFunction(() => document.querySelector('.clock .hand').classList.contains('late'), null, { timeout: 2000 });
  await page.waitForTimeout(300);                  // 색 전환이 끝날 시간
  assert.notEqual(await stroke(), early);
  await page.waitForFunction(() => document.querySelector('.note')?.textContent === '시간 초과', null, { timeout: 3000 });
  await page.waitForSelector('.choice:enabled');   // 다음 문항
  assert.equal(await late(), false);
  await ctx.close();
});
