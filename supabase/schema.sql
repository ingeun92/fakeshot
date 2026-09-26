-- FakeShot Phase 0 pilot schema.
-- Supabase SQL Editor에 통째로 붙여 넣고 실행한다. 다시 실행해도 안전하다.
--
-- 보안 모델: 모든 테이블은 RLS가 켜져 있고 anon 정책이 없다.
-- 브라우저는 토큰을 검증하는 SECURITY DEFINER 함수 네 개만 호출할 수 있다.
-- 정답(items.label)은 어떤 함수도 문항 단위로 돌려주지 않는다.

create table if not exists settings (
  id boolean primary key default true check (id),
  gap_hours numeric not null default 12,       -- 1차 완료 후 2차까지 최소 간격
  main_items_per_session int not null default 30
);
insert into settings (id) values (true) on conflict do nothing;

create table if not exists participants (
  token text primary key,
  order_group text not null check (order_group in ('AB', 'BA')),
  created_at timestamptz not null default now()
);

create table if not exists items (
  item_id text primary key,
  set_name text not null check (set_name in ('A', 'B', 'P')),
  half smallint check (half in (1, 2)),         -- 반분 신뢰도용. 연습 문항은 null
  label text not null check (label in ('ai', 'real')),
  category text
);

create table if not exists sessions (
  token text not null references participants(token),
  session_no smallint not null check (session_no in (1, 2)),
  set_name text not null check (set_name in ('A', 'B')),
  status text not null default 'in_progress' check (status in ('in_progress', 'completed')),
  device jsonb,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  primary key (token, session_no)
);

create table if not exists responses (
  id bigint generated always as identity primary key,
  token text not null,
  session_no smallint not null,
  phase text not null check (phase in ('practice', 'main')),
  position smallint not null check (position >= 0),
  item_id text not null references items(item_id),
  outcome text not null check (outcome in ('answered', 'timeout', 'abandoned')),
  choice smallint check (choice between 1 and 4),   -- 1 확실히 실물, 2 아마 실물, 3 아마 AI, 4 확실히 AI
  rt_ms int check (rt_ms between 0 and 600000),
  left_tab boolean not null default false,
  lost_focus boolean not null default false,
  viewport_w int,
  viewport_h int,
  rendered_px int,
  client_ts timestamptz,
  created_at timestamptz not null default now(),
  foreign key (token, session_no) references sessions(token, session_no),
  unique (token, session_no, phase, position),
  check ((outcome = 'answered') = (choice is not null))
);

alter table settings enable row level security;
alter table participants enable row level security;
alter table items enable row level security;
alter table sessions enable row level security;
alter table responses enable row level security;

revoke all on settings, participants, items, sessions, responses from anon, authenticated;

-- 2차 세션 문항 세트: 1차의 반대
create or replace function fs_set_for(p_order text, p_session smallint)
returns text language sql immutable as $$
  select case when (p_order = 'AB') = (p_session = 1) then 'A' else 'B' end
$$;

drop function if exists fs_score(text);

-- 끝난 세션 하나의 점수. 진행 중인 세션 점수를 보여 주면 문항마다 새로고침해 정답을 알아낼 수 있으므로
-- 호출하는 쪽에서 완료된 세션에만 쓴다.
create or replace function fs_session_score(p_token text, p_session smallint)
returns jsonb language sql stable security definer set search_path = public, pg_temp as $$
  select jsonb_build_object(
    'correct', count(*) filter (
      where r.outcome = 'answered'
        and ((i.label = 'ai') = (r.choice >= 3))),
    'answered', count(*) filter (where r.outcome = 'answered'),
    'timeouts', count(*) filter (where r.outcome = 'timeout'),
    'total', count(*))
  from responses r join items i on i.item_id = r.item_id
  where r.token = p_token and r.session_no = p_session and r.phase = 'main'
$$;

create or replace function get_state(p_token text)
returns jsonb language plpgsql stable security definer set search_path = public, pg_temp as $$
declare
  v_order text;
  v_gap numeric;
  s1 sessions;
  s2 sessions;
  v_resume jsonb := null;
  v_next text;
  v_wait_until timestamptz := null;
  v_cur sessions;
begin
  select order_group into v_order from participants where token = p_token;
  if v_order is null then
    return jsonb_build_object('ok', false, 'error', 'unknown_token');
  end if;
  select gap_hours into v_gap from settings;
  select * into s1 from sessions where token = p_token and session_no = 1;
  select * into s2 from sessions where token = p_token and session_no = 2;

  if s1.token is null then
    v_next := 'session1';
  elsif s1.status = 'in_progress' then
    v_next := 'resume'; v_cur := s1;
  elsif s2.token is null then
    v_wait_until := s1.completed_at + make_interval(secs => v_gap * 3600);
    v_next := case when now() >= v_wait_until then 'session2' else 'wait' end;
  elsif s2.status = 'in_progress' then
    v_next := 'resume'; v_cur := s2;
  else
    v_next := 'done';
  end if;

  if v_cur.token is not null then
    select jsonb_build_object(
      'session_no', v_cur.session_no,
      'set_name', v_cur.set_name,
      'max_main_position', max(position) filter (where phase = 'main'))
    into v_resume
    from responses where token = p_token and session_no = v_cur.session_no;
  end if;

  return jsonb_build_object(
    'ok', true,
    'order_group', v_order,
    'next', v_next,
    'wait_until', v_wait_until,
    'resume', v_resume,
    'scores', jsonb_build_object(
      'session1', case when s1.status = 'completed' then fs_session_score(p_token, 1::smallint) end,
      'session2', case when s2.status = 'completed' then fs_session_score(p_token, 2::smallint) end));
end $$;

create or replace function start_session(p_token text, p_session_no smallint, p_device jsonb)
returns jsonb language plpgsql volatile security definer set search_path = public, pg_temp as $$
declare
  v_state jsonb := get_state(p_token);
  v_order text := v_state->>'order_group';
  v_next text := v_state->>'next';
  v_set text;
begin
  if not (v_state->>'ok')::boolean then
    return v_state;
  end if;
  if v_next = 'resume' and (v_state->'resume'->>'session_no')::smallint = p_session_no then
    return jsonb_build_object('ok', true, 'set_name', v_state->'resume'->>'set_name', 'resumed', true);
  end if;
  if v_next <> ('session' || p_session_no) then
    return jsonb_build_object('ok', false, 'error', 'not_allowed', 'next', v_next);
  end if;
  v_set := fs_set_for(v_order, p_session_no);
  insert into sessions (token, session_no, set_name, device)
  values (p_token, p_session_no, v_set, p_device);
  return jsonb_build_object('ok', true, 'set_name', v_set, 'resumed', false);
end $$;

create or replace function submit_response(
  p_token text, p_session_no smallint, p_phase text, p_position smallint, p_item_id text,
  p_outcome text, p_choice smallint, p_rt_ms int, p_left_tab boolean, p_lost_focus boolean,
  p_viewport_w int, p_viewport_h int, p_rendered_px int, p_client_ts timestamptz)
returns jsonb language plpgsql volatile security definer set search_path = public, pg_temp as $$
declare
  v_session sessions;
  v_item_set text;
  v_limit int;
begin
  select * into v_session from sessions where token = p_token and session_no = p_session_no;
  if v_session.token is null then
    return jsonb_build_object('ok', false, 'error', 'no_session');
  end if;
  if exists (select 1 from responses where token = p_token and session_no = p_session_no
             and phase = p_phase and position = p_position) then
    return jsonb_build_object('ok', true, 'duplicate', true);   -- 재전송은 성공으로 처리
  end if;
  if v_session.status <> 'in_progress' then
    return jsonb_build_object('ok', false, 'error', 'session_closed');
  end if;
  select set_name into v_item_set from items where item_id = p_item_id;
  select main_items_per_session into v_limit from settings;
  if v_item_set is null
     or (p_phase = 'main' and (v_item_set <> v_session.set_name or p_position >= v_limit))
     or (p_phase = 'practice' and v_item_set <> 'P') then
    return jsonb_build_object('ok', false, 'error', 'bad_item');
  end if;
  insert into responses (token, session_no, phase, position, item_id, outcome, choice, rt_ms,
                         left_tab, lost_focus, viewport_w, viewport_h, rendered_px, client_ts)
  values (p_token, p_session_no, p_phase, p_position, p_item_id, p_outcome,
          case when p_outcome = 'answered' then p_choice end, p_rt_ms,
          coalesce(p_left_tab, false), coalesce(p_lost_focus, false),
          p_viewport_w, p_viewport_h, p_rendered_px, p_client_ts);
  return jsonb_build_object('ok', true);
exception
  when unique_violation then
    return jsonb_build_object('ok', true, 'duplicate', true);
  when check_violation or not_null_violation then
    return jsonb_build_object('ok', false, 'error', 'invalid_values');
end $$;

create or replace function finish_session(p_token text, p_session_no smallint)
returns jsonb language plpgsql volatile security definer set search_path = public, pg_temp as $$
begin
  update sessions set status = 'completed', completed_at = coalesce(completed_at, now())
  where token = p_token and session_no = p_session_no;
  if not found then
    return jsonb_build_object('ok', false, 'error', 'no_session');
  end if;
  return get_state(p_token);
end $$;

revoke execute on function fs_set_for(text, smallint), fs_session_score(text, smallint) from public, anon, authenticated;
revoke execute on function get_state(text), start_session(text, smallint, jsonb),
  submit_response(text, smallint, text, smallint, text, text, smallint, int, boolean, boolean, int, int, int, timestamptz),
  finish_session(text, smallint) from public;
grant execute on function get_state(text), start_session(text, smallint, jsonb),
  submit_response(text, smallint, text, smallint, text, text, smallint, int, boolean, boolean, int, int, int, timestamptz),
  finish_session(text, smallint) to anon;
