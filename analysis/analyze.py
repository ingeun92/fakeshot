"""파일럿 응답을 분석해 신뢰도를 계산한다.

입력: private/export.csv (supabase/export.sql 결과를 CSV로 저장한 것)
제외: private/participants.csv 의 exclude 칸이 1인 참가자 (본인 등), --exclude 로 준 토큰
출력: 화면과 private/report.md

유효 응답 = 5초 안에 고른 응답 중 탭 이탈이나 포커스 이탈이 없는 것.
정답 기준 = 실물을 1·2로, AI를 3·4로 고르면 정답 (확신도 무관).
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / "private"


# ── 입력 ───────────────────────────────────────────────────

def _bool(v: str) -> bool:
    return str(v).strip().lower() in {"true", "t", "1", "yes"}


def _int(v: str):
    v = str(v).strip()
    return int(float(v)) if v not in {"", "null", "None"} else None


def load_export(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        out.append({
            "token": r["token"],
            "order_group": r["order_group"],
            "session_no": _int(r["session_no"]),
            "set_name": r["set_name"],
            "session_status": r["session_status"],
            "phase": r["phase"],
            "position": _int(r["position"]),
            "item_id": r["item_id"],
            "label": r["label"],
            "half": _int(r["half"]),
            "category": r.get("category", ""),
            "outcome": r["outcome"],
            "choice": _int(r["choice"]),
            "rt_ms": _int(r["rt_ms"]),
            "left_tab": _bool(r["left_tab"]),
            "lost_focus": _bool(r["lost_focus"]),
            "coarse_pointer": r.get("coarse_pointer", ""),
        })
    return out


def load_excluded(participants_csv: Path, extra: list[str]) -> set[str]:
    excluded = set(t for t in extra if t)
    if participants_csv.exists():
        with participants_csv.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if str(r.get("exclude", "")).strip() in {"1", "y", "yes", "true", "o"}:
                    excluded.add(r["token"])
    return excluded


# ── 기본 계산 ──────────────────────────────────────────────

def is_valid(r: dict) -> bool:
    return r["outcome"] == "answered" and not r["left_tab"] and not r["lost_focus"]


def is_correct(r: dict) -> bool:
    return (r["label"] == "ai") == (r["choice"] >= 3)


def spearman_brown(r: float, factor: float = 2.0) -> float:
    return factor * r / (1 + (factor - 1) * r)


def fisher_ci(r: float, n: int, conf: float = 0.95) -> tuple[float, float]:
    if n <= 3 or abs(r) >= 1:
        return (float("nan"), float("nan"))
    z = math.atanh(r)
    half = stats.norm.ppf(0.5 + conf / 2) / math.sqrt(n - 3)
    return (math.tanh(z - half), math.tanh(z + half))


def pearson(x, y) -> float:
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 3 or x.std() == 0 or y.std() == 0:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def auc(scores_ai: list[int], scores_real: list[int]) -> float:
    """확신도(1~4)를 'AI일 것 같은 정도'로 보고 AI 문항이 실물보다 높게 매겨질 확률."""
    if not scores_ai or not scores_real:
        return float("nan")
    u = stats.mannwhitneyu(scores_ai, scores_real, alternative="two-sided").statistic
    return float(u / (len(scores_ai) * len(scores_real)))


@dataclass
class SessionScore:
    token: str
    session_no: int
    set_name: str
    completed: bool
    n_items: int = 0
    n_valid: int = 0
    n_timeout: int = 0
    n_flagged: int = 0
    correct: int = 0
    half_valid: dict = field(default_factory=lambda: {1: 0, 2: 0})
    half_correct: dict = field(default_factory=lambda: {1: 0, 2: 0})
    auc: float = float("nan")

    @property
    def acc(self) -> float:
        return self.correct / self.n_valid if self.n_valid else float("nan")

    def half_acc(self, h: int) -> float:
        return self.half_correct[h] / self.half_valid[h] if self.half_valid[h] else float("nan")


def session_scores(rows: list[dict]) -> dict[tuple[str, int], SessionScore]:
    out: dict[tuple[str, int], SessionScore] = {}
    ratings: dict[tuple[str, int], dict[str, list[int]]] = {}
    for r in rows:
        if r["phase"] != "main":
            continue
        key = (r["token"], r["session_no"])
        s = out.get(key)
        if s is None:
            s = out[key] = SessionScore(r["token"], r["session_no"], r["set_name"],
                                        r["session_status"] == "completed")
            ratings[key] = {"ai": [], "real": []}
        s.n_items += 1
        if r["outcome"] == "timeout":
            s.n_timeout += 1
        if r["outcome"] == "answered" and (r["left_tab"] or r["lost_focus"]):
            s.n_flagged += 1
        if not is_valid(r):
            continue
        ok = is_correct(r)
        s.n_valid += 1
        s.correct += ok
        if r["half"] in (1, 2):
            s.half_valid[r["half"]] += 1
            s.half_correct[r["half"]] += ok
        ratings[key][r["label"]].append(r["choice"])
    for key, s in out.items():
        s.auc = auc(ratings[key]["ai"], ratings[key]["real"])
    return out


# ── 신뢰도 ─────────────────────────────────────────────────

@dataclass
class Reliability:
    name: str
    n: int
    r: float
    ci: tuple[float, float]
    corrected: float | None = None   # Spearman-Brown 보정값 (반분일 때)


def split_half(scores: list[SessionScore], name: str, min_half: int) -> Reliability:
    pairs = [(s.half_acc(1), s.half_acc(2)) for s in scores
             if s.half_valid[1] >= min_half and s.half_valid[2] >= min_half]
    if len(pairs) < 3:
        return Reliability(name, len(pairs), float("nan"), (float("nan"), float("nan")))
    r = pearson(*zip(*pairs))
    return Reliability(name, len(pairs), r, fisher_ci(r, len(pairs)),
                       spearman_brown(r) if not math.isnan(r) else float("nan"))


def retest(pairs: list[tuple[float, float]], name: str) -> Reliability:
    pairs = [p for p in pairs if not any(math.isnan(v) for v in p)]
    if len(pairs) < 3:
        return Reliability(name, len(pairs), float("nan"), (float("nan"), float("nan")))
    r = pearson(*zip(*pairs))
    return Reliability(name, len(pairs), r, fisher_ci(r, len(pairs)))


def verdict(r: float, ci: tuple[float, float]) -> str:
    if math.isnan(r):
        return "계산 불가 (표본 부족)"
    if r >= 0.7:
        band = "실력이 명확히 존재하는 수준"
    elif r >= 0.4:
        band = "실력은 있으나 노이즈가 큰 수준"
    elif r > 0.2:
        band = "약한 신호"
    else:
        band = "사실상 운에 가까운 수준"
    note = ""
    if not math.isnan(ci[0]) and ci[0] <= 0:
        note = " 단, 신뢰구간 하한이 0 이하라 운과 통계적으로 구분되지 않습니다."
    elif not math.isnan(ci[0]) and ci[1] - ci[0] > 0.5:
        note = " 신뢰구간이 넓어 확정적인 판단은 어렵습니다."
    return band + "." + note


# ── 문항 ───────────────────────────────────────────────────

def item_stats(rows: list[dict]) -> list[dict]:
    by: dict[str, list[dict]] = {}
    for r in rows:
        if r["phase"] == "main":
            by.setdefault(r["item_id"], []).append(r)
    out = []
    for item_id, rs in by.items():
        valid = [r for r in rs if is_valid(r)]
        n = len(valid)
        ai_votes = sum(r["choice"] >= 3 for r in valid)
        share_ai = ai_votes / n if n else float("nan")
        out.append({
            "item_id": item_id,
            "set": rs[0]["set_name"],
            "label": rs[0]["label"],
            "category": rs[0]["category"],
            "n": n,
            "acc": sum(is_correct(r) for r in valid) / n if n else float("nan"),
            "share_ai": share_ai,
            # 1이면 정확히 반반으로 갈림, 0이면 만장일치
            "polarization": 1 - abs(2 * share_ai - 1) if n else float("nan"),
            "timeouts": sum(r["outcome"] == "timeout" for r in rs),
            "median_rt": float(np.median([r["rt_ms"] for r in valid])) if n else float("nan"),
        })
    return sorted(out, key=lambda d: d["acc"])


# ── 보고서 ─────────────────────────────────────────────────

def fmt(v, d=2):
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.{d}f}"


def fmt_ci(ci):
    return "—" if math.isnan(ci[0]) else f"{ci[0]:.2f} ~ {ci[1]:.2f}"


def analyze(rows: list[dict], excluded: set[str], min_valid: int = 20, min_half: int = 5) -> str:
    rows = [r for r in rows if r["token"] not in excluded]
    scores = session_scores(rows)
    usable = {k: s for k, s in scores.items() if s.completed and s.n_valid >= min_valid}
    tokens = sorted({t for t, _ in scores})

    lines = ["# FakeShot 파일럿 분석", ""]
    n_s1 = sum(1 for (t, s) in usable if s == 1)
    n_s2 = sum(1 for (t, s) in usable if s == 2)
    both = sorted(t for t in tokens if (t, 1) in usable and (t, 2) in usable)
    lines += [f"- 참가자 {len(tokens)}명 (제외 {len(excluded)}명)",
              f"- 분석에 쓴 세션: 1차 {n_s1}명, 2차 {n_s2}명, 둘 다 {len(both)}명 "
              f"(완료했고 유효 응답 {min_valid}개 이상인 세션만)"]
    main_rows = [r for r in rows if r["phase"] == "main"]
    if main_rows:
        n = len(main_rows)
        answered = [r for r in main_rows if r["outcome"] == "answered"]
        lines += [f"- 본 문항 응답 {n}개 중 시간 초과 {sum(r['outcome'] == 'timeout' for r in main_rows) / n:.1%}, "
                  f"탭 이탈 {sum(r['left_tab'] for r in answered) / n:.1%}, "
                  f"포커스 이탈 {sum(r['lost_focus'] and not r['left_tab'] for r in answered) / n:.1%}, "
                  f"중단 {sum(r['outcome'] == 'abandoned' for r in main_rows) / n:.1%}"]
    done = sorted(s.n_valid for s in scores.values() if s.completed)
    if done:
        lines.append(f"- 완료 세션의 유효 응답 수: 최소 {done[0]}, 중앙값 {int(np.median(done))}, 최대 {done[-1]} "
                     f"(기준 {min_valid}개. 기준에 걸리는 세션이 많으면 --min-valid 를 낮출지 검토)")
    dropped = [f"{t[:6]}… {k}차 ({'미완료' if not s.completed else f'유효 응답 {s.n_valid}개'})"
               for (t, k), s in sorted(scores.items()) if (t, k) not in usable]
    lines.append(f"- 분석에서 빠진 세션: {', '.join(dropped) if dropped else '없음'}")
    lines.append("")

    # 신뢰도
    rel = []
    for sn in (1, 2):
        rel.append(split_half([s for (t, k), s in usable.items() if k == sn], f"{sn}차 세션 반분 (30문항)", min_half))
    rel.append(retest([(usable[(t, 1)].acc, usable[(t, 2)].acc) for t in both], "재검사: 1차 정답률 ↔ 2차 정답률"))
    rel.append(retest([(usable[(t, 1)].auc, usable[(t, 2)].auc) for t in both], "재검사: 1차 AUC ↔ 2차 AUC (확신도 반영)"))
    combined = []
    for t in both:
        a, b = usable[(t, 1)], usable[(t, 2)]
        v1, v2 = a.half_valid[1] + b.half_valid[1], a.half_valid[2] + b.half_valid[2]
        c1, c2 = a.half_correct[1] + b.half_correct[1], a.half_correct[2] + b.half_correct[2]
        if v1 >= min_half and v2 >= min_half:
            combined.append((c1 / v1, c2 / v2))
    r60 = retest(combined, "60문항 전체 반분")
    r60.corrected = spearman_brown(r60.r) if not math.isnan(r60.r) else float("nan")
    rel.append(r60)

    lines += ["## 신뢰도", "",
              "| 지표 | n | r | 95% 신뢰구간 | Spearman-Brown 보정 |",
              "|---|---|---|---|---|"]
    for x in rel:
        lines.append(f"| {x.name} | {x.n} | {fmt(x.r)} | {fmt_ci(x.ci)} | {fmt(x.corrected) if x.corrected is not None else ''} |")
    main_rel = rel[2]
    lines += ["", f"**판정 (재검사 r 기준):** {verdict(main_rel.r, main_rel.ci)}", "",
              "참가자가 20명 안팎이면 신뢰구간이 넓습니다. 극단값(0 근처 또는 0.8 이상)만 해석하고, "
              "판단용 수치는 100명 규모 라운드에서 다시 재야 합니다.", ""]

    # 순서와 세트 효과
    if both:
        d = [usable[(t, 2)].acc - usable[(t, 1)].acc for t in both]
        lines += ["## 순서와 세트 효과", "",
                  f"- 2차 정답률 − 1차 정답률 평균: {np.mean(d):+.3f} "
                  f"(연습 효과나 학습이 있으면 양수)"]
        if len(both) >= 6 and any(d):
            lines[-1] += f", Wilcoxon p = {stats.wilcoxon(d).pvalue:.3f}"
    set_acc = {sn: [s.acc for s in usable.values() if s.set_name == sn] for sn in ("A", "B")}
    lines += [f"- 세트 A 평균 정답률 {fmt(np.mean(set_acc['A']) if set_acc['A'] else float('nan'), 3)}, "
              f"세트 B {fmt(np.mean(set_acc['B']) if set_acc['B'] else float('nan'), 3)} "
              "(차이가 크면 두 세트 난이도가 다름)", ""]

    # 기기
    dev = {}
    for s in usable.values():
        pr = next((r["coarse_pointer"] for r in rows if r["token"] == s.token and r["session_no"] == s.session_no), "")
        dev.setdefault("모바일(터치)" if _bool(pr) else "데스크톱", []).append(s.acc)
    if dev:
        lines += ["## 기기별 정답률", ""] + [f"- {k}: {len(v)}세션, 평균 {np.mean(v):.3f}" for k, v in dev.items()] + [""]

    # 참가자
    lines += ["## 참가자별", "", "| 참가자 | 1차 정답률 | 2차 정답률 | 1차 AUC | 2차 AUC | 시간 초과 | 이탈 |",
              "|---|---|---|---|---|---|---|"]
    for i, t in enumerate(tokens, 1):
        s1, s2 = scores.get((t, 1)), scores.get((t, 2))
        to = sum(s.n_timeout for s in (s1, s2) if s)
        fl = sum(s.n_flagged for s in (s1, s2) if s)
        lines.append(f"| {t[:6]}… | {fmt(s1.acc if s1 else None)} | {fmt(s2.acc if s2 else None)} | "
                     f"{fmt(s1.auc if s1 else None)} | {fmt(s2.auc if s2 else None)} | {to} | {fl} |")
    lines.append("")

    # 문항
    items = item_stats(rows)
    if items:
        lines += ["## 문항별 (정답률 낮은 순)", "",
                  "갈림 정도: 1이면 정확히 반반, 0이면 만장일치.", "",
                  "| 문항 | 세트 | 정답 | 카테고리 | n | 정답률 | 갈림 정도 | 시간 초과 | 응답시간 중앙값(ms) |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for it in items:
            lines.append(f"| {it['item_id'][:8]} | {it['set']} | {it['label']} | {it['category']} | {it['n']} | "
                         f"{fmt(it['acc'])} | {fmt(it['polarization'])} | {it['timeouts']} | {fmt(it['median_rt'], 0)} |")
        easy = [it for it in items if it["n"] and it["acc"] >= 0.9]
        fooled = [it for it in items if it["n"] and it["acc"] <= 0.2]
        lines += ["", f"- 너무 쉬운 문항(정답률 90% 이상): {len(easy)}개. 변별력이 없으니 다음 라운드에서 교체 후보입니다.",
                  f"- 대부분이 속은 문항(정답률 20% 이하): {len(fooled)}개. 무엇이 사람들을 속였는지 들여다볼 가치가 있습니다."]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export", type=Path, default=PRIVATE / "export.csv")
    ap.add_argument("--participants", type=Path, default=PRIVATE / "participants.csv")
    ap.add_argument("--exclude", default="", help="분석에서 뺄 토큰 (쉼표로 구분)")
    ap.add_argument("--min-valid", type=int, default=20, help="세션을 분석에 넣을 최소 유효 응답 수")
    ap.add_argument("--out", type=Path, default=PRIVATE / "report.md")
    args = ap.parse_args(argv)
    if not args.export.exists():
        print(f"{args.export} 가 없습니다. supabase/export.sql 결과를 CSV로 저장하세요.", file=sys.stderr)
        return 1
    rows = load_export(args.export)
    excluded = load_excluded(args.participants, args.exclude.split(","))
    report = analyze(rows, excluded, min_valid=args.min_valid)
    args.out.write_text(report, encoding="utf-8")
    print(report)
    print(f"저장: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
