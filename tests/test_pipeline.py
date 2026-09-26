import io
import json
import shutil

import pytest
from PIL import Image, ImageCms

from pipeline import build_sets, make_placeholders, normalize, participants
from pipeline.check_leak import check
from pipeline.check_shortcuts import cliffs_delta, loo_logistic_accuracy
from pipeline.common import encode_jpeg, normalize_image


def test_exif_rotation_is_applied_before_square_crop():
    # 저장된 픽셀: 왼쪽 빨강, 오른쪽 파랑. Orientation 6 = 시계 방향 90도 돌려서 봐야 함.
    # 올바르게 돌리면 위가 빨강, 아래가 파랑인 세로 사진이 된다.
    img = Image.new("RGB", (300, 200), (0, 0, 255))
    img.paste((255, 0, 0), (0, 0, 150, 200))
    exif = Image.Exif()
    exif[0x0112] = 6
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes(), quality=95)
    out, upscaled = normalize_image(Image.open(io.BytesIO(buf.getvalue())), size=256)
    assert out.size == (256, 256)
    top, bottom = out.getpixel((128, 10)), out.getpixel((128, 245))
    assert top[0] > 200 and top[2] < 60
    assert bottom[2] > 200 and bottom[0] < 60
    assert upscaled is True


def test_published_jpeg_has_no_metadata():
    img = Image.new("RGB", (1200, 900), (120, 130, 140))
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes(), icc_profile=icc)
    out, _ = normalize_image(Image.open(io.BytesIO(buf.getvalue())))
    published = Image.open(io.BytesIO(encode_jpeg(out)))
    assert len(published.getexif()) == 0
    assert not published.info.get("icc_profile")
    assert published.size == (768, 768)


def test_cliffs_delta_direction():
    assert cliffs_delta([3, 4], [1, 2]) == 1.0
    assert cliffs_delta([1, 2], [3, 4]) == -1.0
    assert cliffs_delta([1, 3], [2, 2]) == 0.0


def test_loo_classifier_flags_separable_groups_only():
    import numpy as np
    rng = np.random.default_rng(0)
    y = np.array([1] * 20 + [0] * 20)
    separable = np.c_[y * 3 + rng.normal(0, 0.5, 40), rng.normal(0, 1, 40)]
    noise = rng.normal(0, 1, (40, 2))
    assert loo_logistic_accuracy(separable, y) > 0.9
    assert loo_logistic_accuracy(noise, y) < 0.7


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("build")
    raw, norm, web, priv = root / "raw", root / "norm", root / "web", root / "private"
    assert make_placeholders.main(["--out", str(raw), "--per-category", "2"]) == 0   # 5종 x 2 = 10장씩
    assert normalize.main(["--raw", str(raw), "--out", str(norm), "--size", "256"]) == 0
    assert build_sets.main(["--normalized", str(norm), "--per-class", "10", "--web", str(web),
                            "--private", str(priv), "--exclude", str(root / "none.txt")]) == 0
    return {"web": web, "private": priv, "norm": norm}


def test_sets_are_balanced_by_label_and_half(built):
    rows = json.loads((built["private"] / "manifest_build.json").read_text())
    for s in ("A", "B"):
        in_set = [r for r in rows if r["set"] == s]
        assert sum(r["label"] == "real" for r in in_set) == 5
        assert sum(r["label"] == "ai" for r in in_set) == 5
        for lab in ("real", "ai"):
            halves = [r["half"] for r in in_set if r["label"] == lab]
            assert abs(halves.count(1) - halves.count(2)) <= 1
    practice = [r for r in rows if r["set"] == "P"]
    assert len(practice) == 3
    assert {r["label"] for r in practice} == {"real", "ai"}


def test_public_manifest_carries_no_answers(built):
    items = json.loads((built["web"] / "items.json").read_text())
    assert len(items) == 23
    assert all(set(it) == {"id", "set", "file"} for it in items)
    seed = (built["private"] / "seed_items.sql").read_text()
    n_ai, n_real = seed.count("'ai'"), seed.count("'real'")
    assert n_ai + n_real == 23 and min(n_ai, n_real) >= 11   # 본 문항 10+10, 연습 3개에 양쪽 포함


def test_leak_check_catches_label_field_and_telltale_filename_and_exif(built, tmp_path):
    base = tmp_path / "web"
    shutil.copytree(built["web"], base)
    manifest = built["private"] / "manifest_build.json"
    assert check(base, manifest, size=256) == []

    items = json.loads((base / "items.json").read_text())
    items[0]["label"] = "ai"
    (base / "items.json").write_text(json.dumps(items))
    assert any("허용되지 않은 필드" in p for p in check(base, manifest, size=256))
    items[0].pop("label")
    (base / "items.json").write_text(json.dumps(items))

    (base / "img" / "real_001.jpg").write_bytes((base / items[0]["file"]).read_bytes())
    assert any("real_001.jpg" in p for p in check(base, manifest, size=256))
    (base / "img" / "real_001.jpg").unlink()

    target = base / items[1]["file"]
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"
    Image.open(target).save(target, format="JPEG", exif=exif.tobytes())
    assert any("EXIF" in p for p in check(base, manifest, size=256))


def test_participant_links_alternate_groups_and_are_not_overwritten(tmp_path):
    args = ["--n", "4", "--base-url", "https://x.example", "--private", str(tmp_path)]
    assert participants.main(args) == 0
    lines = (tmp_path / "participants.csv").read_text().splitlines()[1:]
    groups = [ln.split(",")[2] for ln in lines]
    assert groups == ["AB", "BA", "AB", "BA"]
    assert all("https://x.example/?p=" in ln for ln in lines)
    assert participants.main(args) == 1                      # 덮어쓰기 거부
    before = (tmp_path / "participants.csv").read_text()
    assert participants.main(["--n", "1", "--base-url", "https://x.example", "--private", str(tmp_path), "--add"]) == 0
    after = (tmp_path / "participants.csv").read_text().splitlines()
    assert after[:5] == before.splitlines()                  # 이미 보낸 링크는 그대로
    assert after[5].split(",")[2] == "AB"                    # 5번째 참가자도 번갈아 배정


def test_rebuilding_real_sets_requires_force(built, tmp_path):
    priv = tmp_path / "private"
    shutil.copytree(built["private"], priv)
    args = ["--normalized", str(built["norm"]), "--per-class", "10", "--web", str(tmp_path / "web"),
            "--private", str(priv), "--exclude", str(tmp_path / "none.txt")]
    before = (priv / "manifest_build.json").read_text()
    assert build_sets.main(args) == 1
    assert (priv / "manifest_build.json").read_text() == before
    assert build_sets.main(args + ["--force"]) == 0
    assert (priv / "manifest_build.json").read_text() != before


def _marked_ai_image(w, h, band_from_bottom=(95, 160), band_from_right=(100, 160)):
    """Gemini처럼 오른쪽 아래에 표시(순수 자홍색)가 찍힌 이미지. 실측 워터마크는 아래 95~150px."""
    img = Image.new("RGB", (w, h), (90, 110, 130))
    x0, x1 = w - band_from_right[1], w - band_from_right[0]
    y0, y1 = h - band_from_bottom[1], h - band_from_bottom[0]
    img.paste((255, 0, 255), (x0, y0, x1, y1))
    return img


def _has_magenta(img):
    import numpy as np
    a = np.asarray(img)
    return bool(((a[..., 0] > 200) & (a[..., 1] < 80) & (a[..., 2] > 200)).any())


def test_gemini_portrait_watermark_is_cropped_away_with_margin():
    out, _ = normalize_image(_marked_ai_image(896, 1200))
    assert not _has_magenta(out)


def test_same_crop_rule_for_real_photos_keeps_a_top_mark():
    # 크롭 규칙은 실물과 AI에 똑같이 적용된다. 위쪽 끝에 가까운 표시는 남아야 한다(위를 덜 자른다).
    img = Image.new("RGB", (3024, 4032), (90, 110, 130))
    img.paste((255, 0, 255), (1400, 470, 1600, 520))       # 위에서 470~520px
    out, _ = normalize_image(img)
    assert _has_magenta(out)


def test_normalize_warns_when_ai_image_may_keep_watermark(tmp_path, capsys):
    raw = tmp_path / "raw"
    (raw / "ai" / "food").mkdir(parents=True)
    (raw / "real" / "food").mkdir(parents=True)
    _marked_ai_image(1024, 1024).save(raw / "ai" / "food" / "square.png")
    _marked_ai_image(896, 1200).save(raw / "ai" / "food" / "portrait.png")
    Image.new("RGB", (1024, 1024), (1, 2, 3)).save(raw / "real" / "food" / "real_square.jpg")
    assert normalize.main(["--raw", str(raw), "--out", str(tmp_path / "norm")]) == 0
    out = capsys.readouterr().out
    warning = out[out.index("워터마크"):]
    assert "square.png" in warning
    assert "portrait.png" not in warning
    assert "real_square.jpg" not in warning
