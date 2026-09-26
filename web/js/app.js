import { CONFIG } from '../config.js';
import { sessionPlan, resumePoint, displaySize } from './lib.js';
import { createApi, createSupabaseBackend } from './api.js';
import { createMockBackend, resetMock } from './mock.js';

const params = new URLSearchParams(location.search);
const MOCK = params.get('mock') === '1';
const app = document.getElementById('app');

const CHOICES = [
  { value: 1, cls: 'real-sure', html: '확실히<br>실물' },
  { value: 2, cls: 'real-maybe', html: '아마<br>실물' },
  { value: 3, cls: 'ai-maybe', html: '아마<br>AI' },
  { value: 4, cls: 'ai-sure', html: '확실히<br>AI' },
];

blockZoom();
start().catch(showFatal);

async function start() {
  if (MOCK && params.get('reset') === '1') {
    resetMock();
    params.delete('reset');   // 새로고침할 때마다 초기화되지 않도록 주소에서 뺀다
    history.replaceState(null, '', `${location.pathname}?${params}`);
  }
  const token = params.get('p') || (MOCK ? 'mock-user' : null);
  if (!token) {
    return showMessage('링크가 올바르지 않습니다', '받은 링크를 그대로 열어 주세요.');
  }
  if (!MOCK && (!CONFIG.SUPABASE_URL || !CONFIG.SUPABASE_KEY)) {
    return showMessage('설정이 필요합니다', 'config.js에 Supabase 주소와 키를 넣어 주세요.');
  }
  const rpc = MOCK
    ? createMockBackend({ gapSeconds: Number(params.get('gap') ?? 0), mainPerSession: CONFIG.MAIN_PER_SESSION })
    : createSupabaseBackend(CONFIG.SUPABASE_URL, CONFIG.SUPABASE_KEY);
  const api = createApi(rpc, token);
  const items = await fetch('items.json', { cache: 'no-cache' }).then((r) => r.json());
  window.addEventListener('online', () => api.flush());
  await api.flush();   // 지난 접속에서 못 보낸 응답
  const ctx = { api, items, token };
  routeState(ctx, await withRetry(() => api.getState()));
}

function routeState(ctx, state) {
  if (!state.ok) {
    return showMessage('링크가 올바르지 않습니다', '받은 링크를 그대로 열어 주세요. 문제가 계속되면 링크를 보낸 사람에게 알려 주세요.');
  }
  switch (state.next) {
    case 'session1':
      return showIntro(1, () => runSession(ctx, 1, null));
    case 'session2':
      return showIntro(2, () => runSession(ctx, 2, null));
    case 'resume':
      return showResumePrompt(() => runSession(ctx, state.resume.session_no, state.resume));
    case 'wait':
      return showWait(state.wait_until, state.scores?.session1, state.mock);
    case 'done':
      return showDone(state.scores, state.mock);
    default:
      return showMessage('알 수 없는 상태입니다', '잠시 후 다시 열어 주세요.');
  }
}

// ── 세션 진행 ──────────────────────────────────────────────

async function runSession(ctx, sessionNo, resumeInfo) {
  const { api, items, token } = ctx;
  showMessage('준비 중…', '');
  const res = await withRetry(() => api.startSession(sessionNo, deviceSnapshot()));
  if (!res.ok) return routeState(ctx, await withRetry(() => api.getState()));

  const plan = sessionPlan(items, token, sessionNo, res.set_name);
  const mainCount = Math.min(CONFIG.MAIN_PER_SESSION, plan.main.length);
  const store = api.localStore;
  const shownKey = `fs:shown:${token}:${sessionNo}`;
  const record = (phase, position, item, t) => ({
    p_session_no: sessionNo,
    p_phase: phase,
    p_position: position,
    p_item_id: item.id,
    p_outcome: t.outcome,
    p_choice: t.choice ?? null,
    p_rt_ms: t.rt_ms ?? null,
    p_left_tab: t.left_tab ?? false,
    p_lost_focus: t.lost_focus ?? false,
    p_viewport_w: window.innerWidth,
    p_viewport_h: window.innerHeight,
    p_rendered_px: t.rendered_px ?? null,
    p_client_ts: new Date().toISOString(),
  });

  let startAt = 0;
  let skipPractice = false;
  if (res.resumed) {
    const raw = store?.getItem(shownKey);
    const rp = resumePoint({
      serverMax: resumeInfo?.max_main_position ?? null,
      localShown: raw === null || raw === undefined ? null : Number(raw),
      localAvailable: Boolean(store),
      mainCount,
    });
    for (const p of rp.abandoned) api.submit(record('main', p, plan.main[p], { outcome: 'abandoned' }));
    startAt = rp.start;
    skipPractice = rp.skipPractice;
  }

  const stage = createStage();
  const cache = new Map();
  const preload = (list, from) => {
    for (const it of list.slice(from, from + 3)) if (!cache.has(it.file)) cache.set(it.file, loadImage(it.file));
  };

  if (!skipPractice) {
    await showInterstitial('연습 문항 3개', '채점하지 않습니다. 버튼 위치와 5초 제한에 익숙해져 보세요.', '연습 시작');
    mountStage(stage);
    for (let i = 0; i < plan.practice.length; i++) {
      preload(plan.practice, i);
      const item = plan.practice[i];
      const t = await runTrial(stage, item, cache, `연습 ${i + 1} / ${plan.practice.length}`, '연습');
      api.submit(record('practice', i, item, t));
    }
    await showInterstitial(`본 문항 ${mainCount}개`, '이제부터 채점합니다. 중간에 다른 앱이나 탭으로 넘어가지 마세요.', '본 문항 시작');
  } else if (startAt < mainCount) {
    await showInterstitial('이어서 진행합니다', `${startAt + 1}번째 문항부터 이어집니다. 중단 직전에 보던 문항은 건너뜁니다.`, '계속하기');
  }

  if (startAt < mainCount) {
    mountStage(stage);
    for (let pos = startAt; pos < mainCount; pos++) {
      preload(plan.main, pos);
      const item = plan.main[pos];
      // 화면에 띄우기 직전에 위치를 남겨야, 새로고침 후 본 문항을 다시 보여 주지 않는다.
      const t = await runTrial(stage, item, cache, `${pos + 1} / ${mainCount}`, '', () => {
        try { store?.setItem(shownKey, String(pos)); } catch { /* 저장 불가 */ }
      });
      api.submit(record('main', pos, item, t));
    }
  }

  await saveAndFinish(ctx, sessionNo);
}

async function saveAndFinish(ctx, sessionNo) {
  const { api } = ctx;
  showMessage('저장 중…', '잠시만 기다려 주세요. 창을 닫지 마세요.');
  const flushed = await api.flushAll();
  if (!flushed) {
    return showAction('저장하지 못한 응답이 있습니다',
      `네트워크 연결을 확인한 뒤 다시 시도해 주세요. (남은 응답 ${api.pending()}개)`,
      '다시 시도', () => saveAndFinish(ctx, sessionNo));
  }
  const state = await withRetry(() => api.finishSession(sessionNo));
  routeState(ctx, state);
}

// ── 문항 한 개 ─────────────────────────────────────────────

function createStage() {
  const root = el('div', 'trial');
  const top = el('div', 'trial-top');
  const progress = el('span');
  const tag = el('span', 'tag');
  top.append(progress, tag);
  const timer = el('div', 'timer');
  const bar = el('div', 'bar');
  timer.append(bar);
  const canvas = el('canvas', 'stage');
  canvas.addEventListener('contextmenu', (e) => e.preventDefault());
  const choices = el('div', 'choices');
  const stage = { root, progress, tag, timer, bar, canvas, ctx2d: canvas.getContext('2d'), buttons: [], onChoice: null };
  for (const c of CHOICES) {
    const b = el('button', `choice ${c.cls}`);
    b.type = 'button';
    b.innerHTML = c.html;
    b.disabled = true;
    b.addEventListener('click', () => { b.blur(); stage.onChoice?.(c.value); });
    choices.append(b);
    stage.buttons.push(b);
  }
  const legend = el('div', 'legend');
  legend.append(el('span', '', '← 실제 사진'), el('span', '', 'AI 생성 →'));
  root.append(top, timer, canvas, choices, legend);
  return stage;
}

function mountStage(stage) {
  app.replaceChildren(stage.root);
}

function sizeStage(stage) {
  const size = displaySize(window.innerWidth, window.innerHeight, CONFIG.MAX_DISPLAY_PX);
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  stage.canvas.style.width = `${size}px`;
  stage.canvas.style.height = `${size}px`;
  stage.canvas.width = Math.round(size * dpr);
  stage.canvas.height = Math.round(size * dpr);
  stage.timer.style.width = `${size}px`;
  stage.root.querySelector('.choices').style.maxWidth = `${Math.max(size, 320)}px`;
  stage.root.querySelector('.legend').style.maxWidth = `${Math.max(size, 320)}px`;
  return size;
}

async function runTrial(stage, item, cache, progressText, tagText, onShow) {
  stage.progress.textContent = progressText;
  stage.tag.textContent = tagText;
  setButtons(stage, false);
  resetBar(stage);
  const size = sizeStage(stage);
  stage.ctx2d.clearRect(0, 0, stage.canvas.width, stage.canvas.height);

  if (!cache.has(item.file)) cache.set(item.file, loadImage(item.file));
  const [img] = await Promise.all([cache.get(item.file), sleep(CONFIG.BLANK_MS)]);
  if (!img) return { outcome: 'abandoned', rendered_px: size };

  onShow?.();
  stage.ctx2d.imageSmoothingEnabled = true;
  stage.ctx2d.imageSmoothingQuality = 'high';
  stage.ctx2d.drawImage(img, 0, 0, stage.canvas.width, stage.canvas.height);
  await nextPaint();

  const t0 = performance.now();
  let leftTab = false;
  let lostFocus = false;
  const onVisibility = () => { if (document.hidden) leftTab = true; };
  const onBlur = () => { lostFocus = true; };
  document.addEventListener('visibilitychange', onVisibility);
  window.addEventListener('blur', onBlur);
  startBar(stage, CONFIG.TIME_LIMIT_MS);
  const guard = setTimeout(() => setButtons(stage, true), CONFIG.INPUT_GUARD_MS);

  const result = await new Promise((resolve) => {
    let done = false;
    const finish = (choice) => {
      if (done) return;
      done = true;
      clearTimeout(deadline);
      document.removeEventListener('visibilitychange', onBack);
      stage.onChoice = null;
      resolve({ choice, rt: Math.round(performance.now() - t0) });
    };
    const deadline = setTimeout(() => finish(null), CONFIG.TIME_LIMIT_MS);
    // 백그라운드에서 타이머가 늦게 도는 경우를 대비해 돌아오는 즉시 시간을 확인한다.
    const onBack = () => {
      if (!document.hidden && performance.now() - t0 >= CONFIG.TIME_LIMIT_MS) finish(null);
    };
    document.addEventListener('visibilitychange', onBack);
    stage.onChoice = (choice) => finish(performance.now() - t0 <= CONFIG.TIME_LIMIT_MS ? choice : null);
  });

  clearTimeout(guard);
  document.removeEventListener('visibilitychange', onVisibility);
  window.removeEventListener('blur', onBlur);
  setButtons(stage, false);
  stage.ctx2d.clearRect(0, 0, stage.canvas.width, stage.canvas.height);
  resetBar(stage);

  return {
    outcome: result.choice ? 'answered' : 'timeout',
    choice: result.choice,
    rt_ms: result.choice ? result.rt : null,
    left_tab: leftTab,
    lost_focus: lostFocus,
    rendered_px: size,
  };
}

function setButtons(stage, enabled) {
  for (const b of stage.buttons) b.disabled = !enabled;
}

function resetBar(stage) {
  stage.bar.style.transition = 'none';
  stage.bar.style.transform = 'scaleX(1)';
}

function startBar(stage, ms) {
  void stage.bar.offsetWidth;   // 초기 상태를 먼저 반영시킨다
  stage.bar.style.transition = `transform ${ms}ms linear`;
  stage.bar.style.transform = 'scaleX(0)';
}

async function loadImage(src, tries = 2) {
  for (let i = 0; i < tries; i++) {
    const img = new Image();
    img.decoding = 'async';
    img.src = src;
    try {
      await img.decode();
      return img;
    } catch {
      await sleep(400);
    }
  }
  return null;
}

// ── 화면 ───────────────────────────────────────────────────

function showIntro(sessionNo, onStart) {
  const wrap = el('div');
  if (sessionNo === 1) {
    wrap.innerHTML = `
      <h1>진짜 사진일까, AI일까?</h1>
      <p>사진이 한 장씩 나옵니다. 실제로 촬영한 사진인지 AI가 만든 이미지인지 골라 주세요.</p>
      <div class="card">
        <ul>
          <li>문항마다 <b>5초</b> 안에 골라야 합니다. 첫인상대로 고르면 됩니다.</li>
          <li>이미지는 확대할 수 없습니다.</li>
          <li>푸는 동안 다른 앱이나 탭으로 넘어가지 마세요. 넘어가면 기록됩니다.</li>
          <li>문제나 답을 다른 사람과 이야기하지 말아 주세요.</li>
        </ul>
      </div>
      <p>오늘은 연습 3문항과 본 문항 30개(약 3분)입니다. 다음 날 <b>같은 링크</b>로 30문항을 한 번 더 풉니다. 끝날 때마다 점수를 알려 드립니다.</p>
      <p class="muted small">응답, 응답 시간, 화면 크기 정보만 익명으로 저장합니다.</p>`;
  } else {
    wrap.innerHTML = `
      <h1>2차 테스트</h1>
      <p>어제와 같은 방식으로 30문항을 풉니다. 모두 새로운 사진입니다.</p>
      <div class="card">
        <ul>
          <li>문항마다 <b>5초</b>, 확대 없음</li>
          <li>푸는 동안 다른 앱이나 탭으로 넘어가지 마세요.</li>
        </ul>
      </div>
      <p>마치면 2차 점수와 1차, 2차 합계를 알려 드립니다.</p>`;
  }
  const btn = button('시작하기', onStart);
  app.replaceChildren(wrap, el('div', 'spacer'), btn);
}

function showResumePrompt(onGo) {
  showAction('진행 중이던 테스트가 있습니다', '중단된 지점부터 이어서 풉니다.', '이어하기', onGo);
}

function hasScore(score) {
  return score && score.correct !== null && score.correct !== undefined;
}

function showWait(waitUntil, score, mock) {
  const when = new Intl.DateTimeFormat('ko-KR', {
    month: 'long', day: 'numeric', weekday: 'short', hour: 'numeric', minute: '2-digit',
  }).format(new Date(waitUntil));
  const wrap = el('div');
  const scoreHtml = hasScore(score)
    ? `<p class="muted">${score.total}문항 중</p>
       <p class="score">${score.correct}개 정답</p>
       <p class="muted small">시간 초과 ${score.timeouts}개</p>`
    : `<p class="muted">${mock ? '모의 모드라 점수는 계산하지 않습니다.' : ''}</p>`;
  wrap.innerHTML = `
    <h1>1차 완료!</h1>
    ${scoreHtml}
    <div class="card">
      <p><b>${when}</b> 이후에 이 링크로 다시 들어와 2차를 풀어 주세요.</p>
      <p class="muted small">링크를 잃어버리지 않도록 카톡 대화방이나 즐겨찾기에 남겨 두세요. 점수나 문제 이야기는 다른 참가자와 나누지 말아 주세요.</p>
    </div>`;
  app.replaceChildren(wrap);
}

function showDone(scores, mock) {
  const s1 = scores?.session1;
  const s2 = scores?.session2;
  const wrap = el('div');
  if (!hasScore(s1) || !hasScore(s2)) {
    wrap.innerHTML = `
      <h1>모두 끝났습니다</h1>
      <p>${mock ? '모의 모드라 점수는 계산하지 않습니다.' : '점수를 불러오지 못했습니다.'}</p>`;
  } else {
    wrap.innerHTML = `
      <h1>모두 끝났습니다</h1>
      <p class="muted">${s1.total + s2.total}문항 중</p>
      <p class="score">${s1.correct + s2.correct}개 정답</p>
      <p class="muted small">1차 ${s1.correct}개, 2차 ${s2.correct}개, 시간 초과 ${s1.timeouts + s2.timeouts}개</p>
      <div class="card"><p>문항별 정답은 파일럿이 모두 끝난 뒤 알려 드립니다. 그 전까지 다른 참가자에게 점수나 문제 이야기는 삼가 주세요.</p></div>`;
  }
  app.replaceChildren(wrap);
}

function showInterstitial(title, body, label) {
  return new Promise((resolve) => showAction(title, body, label, resolve));
}

function showAction(title, body, label, onClick) {
  const wrap = el('div');
  wrap.append(el('h1', '', title), el('p', '', body));
  app.replaceChildren(wrap, el('div', 'spacer'), button(label, onClick));
}

function showMessage(title, body) {
  const wrap = el('div');
  wrap.append(el('h1', '', title));
  if (body) wrap.append(el('p', 'muted', body));
  app.replaceChildren(wrap);
}

function showFatal(err) {
  console.error(err);
  showAction('연결에 문제가 생겼습니다', '네트워크를 확인한 뒤 다시 시도해 주세요. 진행 중이던 테스트는 이어서 풀 수 있습니다.',
    '다시 시도', () => location.reload());
}

// ── 유틸 ───────────────────────────────────────────────────

function el(tag, cls = '', text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function button(label, onClick) {
  const b = el('button', 'primary', label);
  b.type = 'button';
  b.addEventListener('click', () => { b.disabled = true; onClick(); }, { once: true });
  return b;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const nextPaint = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

async function withRetry(fn, tries = 3) {
  let lastErr;
  for (let i = 0; i < tries; i++) {
    try { return await fn(); } catch (e) { lastErr = e; await sleep(800 * (i + 1)); }
  }
  throw lastErr;
}

function deviceSnapshot() {
  return {
    dpr: window.devicePixelRatio || 1,
    screen_w: window.screen?.width ?? null,
    screen_h: window.screen?.height ?? null,
    viewport_w: window.innerWidth,
    viewport_h: window.innerHeight,
    coarse_pointer: window.matchMedia?.('(pointer: coarse)').matches ?? null,
    max_touch_points: navigator.maxTouchPoints ?? 0,
    display_px: displaySize(window.innerWidth, window.innerHeight, CONFIG.MAX_DISPLAY_PX),
  };
}

function blockZoom() {
  const stop = (e) => e.preventDefault();
  for (const type of ['gesturestart', 'gesturechange', 'gestureend']) {
    document.addEventListener(type, stop, { passive: false });
  }
  document.addEventListener('touchmove', (e) => { if (e.touches.length > 1) e.preventDefault(); }, { passive: false });
  document.addEventListener('dblclick', stop, { passive: false });
  window.addEventListener('wheel', (e) => { if (e.ctrlKey) e.preventDefault(); }, { passive: false });
  window.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && ['+', '-', '=', '0'].includes(e.key)) e.preventDefault();
  });
}
