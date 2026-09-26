import csv
import math

import numpy as np
import pytest

from analysis.analyze import (analyze, auc, fisher_ci, load_excluded, retest, session_scores,
                              spearman_brown, split_half)


def row(token="t1", session_no=1, phase="main", position=0, item_id="i", label="ai", half=1,
        outcome="answered", choice=4, left_tab=False, lost_focus=False, set_name="A",
        status="completed", rt_ms=1500):
    return {"token": token, "order_group": "AB", "session_no": session_no, "set_name": set_name,
            "session_status": status, "phase": phase, "position": position, "item_id": item_id,
            "label": label, "half": half, "category": "c", "outcome": outcome,
            "choice": choice if outcome == "answered" else None, "rt_ms": rt_ms,
            "left_tab": left_tab, "lost_focus": lost_focus, "coarse_pointer": "true"}


def test_spearman_brown_known_values():
    assert spearman_brown(0.5) == pytest.approx(2 / 3)
    assert spearman_brown(0.6) == pytest.approx(0.75)


def test_fisher_ci_matches_hand_calculation():
    # z = atanh(0.5) = 0.5493, se = 1/sqrt(17) = 0.2425, ±1.96se → tanh(0.0739), tanh(1.0247)
    lo, hi = fisher_ci(0.5, 20)
    assert lo == pytest.approx(0.0738, abs=1e-3)
    assert hi == pytest.approx(0.7717, abs=1e-3)


def test_auc_counts_ties_as_half():
    # AI 문항 확신도 [4, 3], 실물 [1, 3]: 4>1, 4>3, 3>1, 3=3 → 3.5 / 4
    assert auc([4, 3], [1, 3]) == pytest.approx(0.875)


def test_only_valid_main_answers_are_scored():
    rows = [
        row(position=0, label="ai", choice=4),                       # 정답
        row(position=1, label="real", choice=3),                     # 오답
        row(position=2, label="real", choice=1, left_tab=True),      # 탭 이탈: 제외
        row(position=3, label="ai", outcome="timeout"),               # 시간 초과: 제외
        row(position=4, label="real", choice=2, lost_focus=True),    # 포커스 이탈: 제외
        row(phase="practice", position=0, label="ai", choice=1, half=None),
    ]
    s = session_scores(rows)[("t1", 1)]
    assert (s.n_items, s.n_valid, s.correct, s.n_timeout, s.n_flagged) == (5, 2, 1, 1, 2)
    assert s.acc == 0.5


def test_maybe_and_sure_count_the_same_for_accuracy():
    rows = [row(position=0, label="real", choice=2), row(position=1, label="ai", choice=3)]
    assert session_scores(rows)[("t1", 1)].correct == 2


def simulate(n_people, ability_sd, seed, items=30):
    """로짓 척도 실력 ~ N(0, sd)인 참가자가 두 세션을 푼 응답을 만든다."""
    rng = np.random.default_rng(seed)
    ability = rng.normal(0, ability_sd, n_people)
    difficulty = {s: rng.normal(0, 0.5, items) for s in (1, 2)}
    rows = []
    for p in range(n_people):
        for s in (1, 2):
            for i in range(items):
                prob = 1 / (1 + math.exp(-(ability[p] - difficulty[s][i])))
                label = "ai" if i % 2 == 0 else "real"
                correct = rng.random() < prob
                choice = (4 if label == "ai" else 1) if correct else (1 if label == "ai" else 4)
                rows.append(row(token=f"p{p}", session_no=s, position=i, item_id=f"s{s}i{i}",
                                label=label, half=1 + (i // 2) % 2, choice=choice,
                                set_name="A" if s == 1 else "B"))
    return rows


def retest_r(rows):
    sc = session_scores(rows)
    tokens = {t for t, _ in sc}
    return retest([(sc[(t, 1)].acc, sc[(t, 2)].acc) for t in tokens], "x").r


def test_no_skill_gives_retest_r_near_zero():
    assert abs(retest_r(simulate(2000, 0.0, seed=1))) < 0.08


def test_real_skill_is_recovered():
    # 실력 sd 1(로짓)이면 30문항 정답률의 참 신뢰도는 약 0.8
    r = retest_r(simulate(2000, 1.0, seed=2))
    assert 0.7 < r < 0.92


def test_split_half_rises_with_skill():
    weak = session_scores(simulate(1000, 0.0, seed=3))
    strong = session_scores(simulate(1000, 1.0, seed=4))
    r_weak = split_half([s for (t, k), s in weak.items() if k == 1], "w", 5)
    r_strong = split_half([s for (t, k), s in strong.items() if k == 1], "s", 5)
    assert abs(r_weak.r) < 0.1
    assert r_strong.corrected > 0.6


def test_excluded_and_incomplete_participants_stay_out_of_retest(tmp_path):
    rows = simulate(4, 1.0, seed=5)
    for r in rows:
        if r["token"] == "p3" and r["session_no"] == 2:
            r["session_status"] = "in_progress"
    pcsv = tmp_path / "participants.csv"
    with pcsv.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["no", "token", "order_group", "link", "name", "exclude"])
        w.writeheader()
        w.writerow({"no": 1, "token": "p0", "order_group": "AB", "link": "", "name": "me", "exclude": "1"})
        w.writerow({"no": 2, "token": "p1", "order_group": "BA", "link": "", "name": "", "exclude": ""})
    excluded = load_excluded(pcsv, [])
    assert excluded == {"p0"}
    report = analyze(rows, excluded)
    assert "p0" not in report
    assert "둘 다 2명" in report      # p1, p2만. p0 제외, p3 미완료
    assert "p3… 2차 (미완료)" in report


def test_sessions_with_too_few_valid_answers_are_listed_with_reason():
    rows = simulate(3, 1.0, seed=6)
    for r in rows:
        if r["token"] == "p1" and r["session_no"] == 1 and r["position"] < 15:
            r["outcome"], r["choice"] = "timeout", None
    report = analyze(rows, set(), min_valid=20)
    assert "p1… 1차 (유효 응답 15개)" in report
    assert "둘 다 2명" in report


def test_participant_list_saved_by_excel_in_cp949_is_read(tmp_path):
    # 한국어 윈도우 엑셀은 CSV를 CP949로 저장한다
    pcsv = tmp_path / "participants.csv"
    pcsv.write_bytes("no,token,order_group,link,name,exclude\n1,p0,AB,,김본인,1\n2,p1,BA,,이친구,\n".encode("cp949"))
    assert load_excluded(pcsv, []) == {"p0"}
