-- 테스트로 쌓인 세션과 응답을 모두 지운다. 참가자 목록과 문항은 남긴다.
-- 실제 파일럿을 시작하기 직전에 한 번 실행한다.
truncate responses, sessions restart identity;
