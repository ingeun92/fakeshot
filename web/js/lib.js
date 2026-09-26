// DOM에 의존하지 않는 순수 로직. 브라우저와 node --test 양쪽에서 불러 쓴다.

// 문자열 → 32비트 시드 (cyrb53의 하위 32비트)
export function hashString(str) {
  let h1 = 0xdeadbeef;
  let h2 = 0x41c6ce57;
  for (let i = 0; i < str.length; i++) {
    const ch = str.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return (h1 ^ h2) >>> 0;
}

function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function seededShuffle(list, seedText) {
  const rand = mulberry32(hashString(seedText));
  const out = list.slice();
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

// 세션에서 보여 줄 문항 순서. 새로고침해도 같은 순서가 나와야 이어하기가 성립한다.
export function sessionPlan(items, token, sessionNo, setName) {
  const byId = (a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  const practice = seededShuffle(items.filter((it) => it.set === 'P').sort(byId), 'practice');
  const main = seededShuffle(
    items.filter((it) => it.set === setName).sort(byId),
    `${token}:${sessionNo}`,
  );
  return { practice, main };
}

// 중단된 세션을 어디서 이어갈지 정한다.
//   serverMax: 서버에 기록된 본 문항 최대 위치 (없으면 null)
//   localShown: 이 기기에서 마지막으로 화면에 띄운 본 문항 위치 (기록이 없으면 null)
//   localAvailable: localStorage를 쓸 수 있었는지
// 화면에 떴지만 응답이 기록되지 않은 문항은 다시 보여 주지 않고 abandoned로 남긴다.
export function resumePoint({ serverMax, localShown, localAvailable, mainCount }) {
  const recorded = serverMax ?? -1;
  let abandoned = [];
  let start;
  if (localAvailable) {
    const shown = Math.max(recorded, localShown ?? -1);
    for (let p = recorded + 1; p <= shown; p++) abandoned.push(p);
    start = shown + 1;
  } else if (serverMax === null || serverMax === undefined) {
    start = 0;
  } else {
    abandoned = [serverMax + 1];
    start = serverMax + 2;
  }
  abandoned = abandoned.filter((p) => p < mainCount);
  const mainStarted = serverMax !== null && serverMax !== undefined
    || (localAvailable && localShown !== null && localShown !== undefined);
  return { start: Math.min(start, mainCount), abandoned, skipPractice: mainStarted };
}

// 모든 기기에서 같은 최대 크기로 보이도록 표시 크기를 제한한다.
export function displaySize(viewportW, viewportH, maxPx, reservedH = 220) {
  const fit = Math.min(maxPx, viewportW - 32, viewportH - reservedH);
  return Math.max(200, Math.floor(fit));
}
