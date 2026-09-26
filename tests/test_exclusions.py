import json

import numpy as np
from PIL import Image

from pipeline import suggest_exclusions
from pipeline.common import load_exclusions

MANIFEST = [
    {"key": "main-real-food-0000", "source": "raw/real/food/a.jpg"},
    {"key": "main-ai-food-0001", "source": "raw/ai/food/b.png"},
    {"key": "main-ai-food-0002", "source": "raw/ai/food/c.png"},
]


def test_exclusions_match_by_key_or_source_path(tmp_path):
    f = tmp_path / "exclude.txt"
    f.write_text("# 주석\nmain-real-food-0000\nraw/ai/food/c.png\nraw/ai/food/없는파일.png\n\n")
    keys, unmatched = load_exclusions(f, MANIFEST)
    assert keys == {"main-real-food-0000", "main-ai-food-0002"}
    assert unmatched == ["raw/ai/food/없는파일.png"]


def test_missing_exclusion_file_means_nothing_excluded(tmp_path):
    assert load_exclusions(tmp_path / "none.txt", MANIFEST) == (set(), [])


def _fake_normalized(root, bright_ai):
    """카테고리 2개, 실물·AI 각 3장. bright_ai에 든 AI 이미지만 매우 밝다."""
    rng = np.random.default_rng(0)
    manifest = []
    i = 0
    for cat in ("a", "b"):
        for label in ("real", "ai"):
            for n in range(3):
                key = f"main-{label}-{cat}-{i:04d}"
                level = 230 if (label, cat, n) in bright_ai else 120
                arr = np.clip(rng.normal(level, 12, (64, 64, 3)), 0, 255).astype(np.uint8)
                Image.fromarray(arr).save(root / f"{key}.png")
                manifest.append({"key": key, "pool": "main", "label": label, "category": cat,
                                 "source": f"raw/{label}/{cat}/{n}.png"})
                i += 1
    (root / "manifest.json").write_text(json.dumps(manifest))
    return manifest


def test_suggestion_drops_the_outlier_ai_image_in_each_category(tmp_path, capsys):
    bright = {("ai", "a", 1), ("ai", "b", 2)}
    _fake_normalized(tmp_path, bright)
    out = tmp_path / "exclude.txt"
    assert suggest_exclusions.main(["--normalized", str(tmp_path), "--keep-per-category", "2",
                                    "--write", str(out)]) == 0
    lines = [ln for ln in out.read_text().splitlines() if ln and not ln.startswith("#")]
    assert sorted(ln for ln in lines if ln.startswith("raw/ai/")) == ["raw/ai/a/1.png", "raw/ai/b/2.png"]
    real = [ln for ln in lines if ln.startswith("raw/real/")]
    assert sorted(ln.split("/")[2] for ln in real) == ["a", "b"]   # 카테고리마다 실물 1장씩


def test_suggestion_does_not_overwrite_existing_exclusions(tmp_path):
    _fake_normalized(tmp_path, set())
    out = tmp_path / "exclude.txt"
    out.write_text("raw/ai/a/0.png\n")
    assert suggest_exclusions.main(["--normalized", str(tmp_path), "--keep-per-category", "2",
                                    "--write", str(out)]) == 1
    assert out.read_text() == "raw/ai/a/0.png\n"
