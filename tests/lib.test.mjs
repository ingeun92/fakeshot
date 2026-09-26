import { test } from 'node:test';
import assert from 'node:assert/strict';
import { sessionPlan, resumePoint, displaySize, seededShuffle } from '../web/js/lib.js';

const items = [
  { id: 'c3', set: 'A' }, { id: 'a1', set: 'A' }, { id: 'b2', set: 'A' }, { id: 'd4', set: 'A' },
  { id: 'e5', set: 'B' }, { id: 'f6', set: 'B' },
  { id: 'p2', set: 'P' }, { id: 'p1', set: 'P' },
];

test('같은 토큰과 세션이면 순서가 같고, 원래 목록 순서에 영향받지 않는다', () => {
  const first = sessionPlan(items, 'tok', 1, 'A').main.map((i) => i.id);
  const reversed = sessionPlan(items.slice().reverse(), 'tok', 1, 'A').main.map((i) => i.id);
  assert.deepEqual(first, reversed);
});

test('세션에는 해당 세트 문항만 빠짐없이 들어간다', () => {
  const plan = sessionPlan(items, 'tok', 1, 'A');
  assert.deepEqual(plan.main.map((i) => i.id).sort(), ['a1', 'b2', 'c3', 'd4']);
  assert.deepEqual(plan.practice.map((i) => i.id).sort(), ['p1', 'p2']);
});

test('참가자마다 본 문항 순서가 달라진다', () => {
  const ids = Array.from({ length: 30 }, (_, i) => ({ id: `i${String(i).padStart(2, '0')}`, set: 'A' }));
  const orders = new Set(['t1', 't2', 't3', 't4', 't5'].map(
    (t) => sessionPlan(ids, t, 1, 'A').main.map((i) => i.id).join(',')));
  assert.equal(orders.size, 5);
});

test('연습 문항 순서는 모든 참가자에게 같다', () => {
  const a = sessionPlan(items, 'tokA', 1, 'A').practice.map((i) => i.id);
  const b = sessionPlan(items, 'tokB', 2, 'B').practice.map((i) => i.id);
  assert.deepEqual(a, b);
});

test('seededShuffle은 원본 배열을 바꾸지 않는다', () => {
  const src = [1, 2, 3, 4, 5];
  seededShuffle(src, 'x');
  assert.deepEqual(src, [1, 2, 3, 4, 5]);
});

test('이어하기: 기록과 화면 표시가 같으면 다음 문항부터, 버리는 문항 없음', () => {
  assert.deepEqual(
    resumePoint({ serverMax: 4, localShown: 4, localAvailable: true, mainCount: 30 }),
    { start: 5, abandoned: [], skipPractice: true });
});

test('이어하기: 화면에 떴지만 기록되지 않은 문항은 버린다', () => {
  assert.deepEqual(
    resumePoint({ serverMax: 4, localShown: 5, localAvailable: true, mainCount: 30 }),
    { start: 6, abandoned: [5], skipPractice: true });
});

test('이어하기: 본 문항 첫 문제에서 끊기면 0번을 버리고 연습은 건너뛴다', () => {
  assert.deepEqual(
    resumePoint({ serverMax: null, localShown: 0, localAvailable: true, mainCount: 30 }),
    { start: 1, abandoned: [0], skipPractice: true });
});

test('이어하기: 본 문항 시작 전이면 연습부터 다시 한다', () => {
  assert.deepEqual(
    resumePoint({ serverMax: null, localShown: null, localAvailable: true, mainCount: 30 }),
    { start: 0, abandoned: [], skipPractice: false });
});

test('이어하기: localStorage가 없으면 기록 다음 문항을 본 것으로 간주해 버린다', () => {
  assert.deepEqual(
    resumePoint({ serverMax: 4, localShown: null, localAvailable: false, mainCount: 30 }),
    { start: 6, abandoned: [5], skipPractice: true });
});

test('이어하기: 마지막 문항에서 끊기면 버린 뒤 바로 종료 위치가 된다', () => {
  assert.deepEqual(
    resumePoint({ serverMax: 28, localShown: 29, localAvailable: true, mainCount: 30 }),
    { start: 30, abandoned: [29], skipPractice: true });
  assert.deepEqual(
    resumePoint({ serverMax: 29, localShown: null, localAvailable: false, mainCount: 30 }),
    { start: 30, abandoned: [], skipPractice: true });
});

test('표시 크기: 데스크톱은 상한, 좁은 폰은 화면 폭에 맞춘다', () => {
  assert.equal(displaySize(1920, 1080, 480), 480);
  assert.equal(displaySize(390, 844, 480), 358);
  assert.equal(displaySize(800, 500, 480), 280);
});
