// 배포 전에 Supabase 프로젝트 값으로 채운다 (Project Settings → API).
// anon/publishable 키는 공개되어도 되는 키다. 정답은 서버에서만 접근할 수 있다.
export const CONFIG = {
  SUPABASE_URL: 'https://eeskbgqnioimmkjvbsxd.supabase.co',
  SUPABASE_KEY: 'sb_publishable_ouAqZPsvslyvkyYJwz3roA_hMN1xfGF',

  TIME_LIMIT_MS: 5000,     // 문항당 제한 시간
  WARN_MS: 1500,           // 남은 시간이 이만큼이 되면 타이머 테두리가 빨갛고 굵어진다
  BLANK_MS: 500,           // 문항 사이 빈 화면
  INPUT_GUARD_MS: 200,     // 이미지가 뜬 직후 입력 무시 (이전 탭이 넘어오는 것 방지)
  MAX_DISPLAY_PX: 480,     // 모든 기기의 이미지 표시 최대 크기 (CSS px)
  MAIN_PER_SESSION: 30,
};
