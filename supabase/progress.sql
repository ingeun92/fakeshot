-- 진행 현황. 2차를 아직 안 한 사람에게 알림을 보낼 때 쓴다.
-- participants.csv의 토큰과 대조해 누구인지 확인한다.
select
  p.token,
  p.order_group,
  coalesce(s1.status, '-') as session1,
  s1.completed_at as session1_done,
  coalesce(s2.status, '-') as session2,
  s2.completed_at as session2_done,
  (select count(*) from responses r where r.token = p.token and r.phase = 'main') as main_responses
from participants p
left join sessions s1 on s1.token = p.token and s1.session_no = 1
left join sessions s2 on s2.token = p.token and s2.session_no = 2
order by s1.completed_at nulls last, p.token;
