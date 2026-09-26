-- 분석용 내보내기. SQL Editor에서 실행한 뒤 결과를 CSV로 내려받아
-- private/export.csv 로 저장한다.
select
  r.token,
  p.order_group,
  r.session_no,
  s.set_name,
  s.status as session_status,
  r.phase,
  r.position,
  r.item_id,
  i.label,
  i.half,
  i.category,
  r.outcome,
  r.choice,
  r.rt_ms,
  r.left_tab,
  r.lost_focus,
  r.viewport_w,
  r.viewport_h,
  r.rendered_px,
  s.device->>'coarse_pointer' as coarse_pointer,
  s.device->>'dpr' as dpr,
  r.created_at
from responses r
join sessions s on s.token = r.token and s.session_no = r.session_no
join participants p on p.token = r.token
join items i on i.item_id = r.item_id
order by r.token, r.session_no, r.phase, r.position;
