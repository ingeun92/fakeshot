"""파이프라인 공통 설정과 이미지 처리 함수."""
from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageOps

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / "private"
WEB = ROOT / "web"

SIZE = 768             # 정사각형 한 변 (px). Gemini 출력(896x1200)을 확대하지 않고, 실물과 AI를 모두 축소 처리하는 크기
# 정사각형을 가운데보다 살짝 위(가로 사진은 왼쪽)에서 잘라 오른쪽 아래를 더 버린다.
# Gemini 워터마크가 오른쪽 아래 약 95~150px에 있어, 0.5면 여유가 2px뿐이다. 모든 이미지에 똑같이 적용한다.
CROP_ANCHOR = 0.45
WATERMARK_MARGIN_PX = 160   # 오른쪽 아래를 이만큼 이상 잘라내야 워터마크가 안전하게 사라진다
JPEG_QUALITY = 85
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".avif", ".tif", ".tiff"}

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover
    pass

_SRGB = ImageCms.createProfile("sRGB")


def to_srgb_rgb(img: Image.Image) -> Image.Image:
    """임베디드 색 프로파일이 있으면 sRGB로 변환하고, 투명 영역은 흰색으로 채운다."""
    icc = img.info.get("icc_profile")
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            base = img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB")
            mode = base.mode
            img = ImageCms.profileToProfile(base, src, _SRGB, outputMode=mode)
        except (ImageCms.PyCMSError, OSError):
            pass
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, rgba)
    return img.convert("RGB")


def crop_box(w: int, h: int) -> tuple[int, int, int, int]:
    side = min(w, h)
    left, top = int((w - side) * CROP_ANCHOR), int((h - side) * CROP_ANCHOR)
    return (left, top, left + side, top + side)


def trimmed_bottom_right(w: int, h: int) -> int:
    """정사각형 크롭으로 오른쪽 아래에서 잘려 나가는 폭 (아래와 오른쪽 중 큰 쪽)."""
    _, _, right, bottom = crop_box(w, h)
    return max(h - bottom, w - right)


def normalize_image(img: Image.Image, size: int = SIZE) -> tuple[Image.Image, bool]:
    """회전 보정 → sRGB → 가운데 정사각형 크롭 → 리사이즈. (결과, 업스케일 여부)"""
    img = ImageOps.exif_transpose(img)
    img = to_srgb_rgb(img)
    img = img.crop(crop_box(*img.size))
    side = img.width
    upscaled = side < size
    img = img.resize((size, size), Image.Resampling.LANCZOS)
    # 메타데이터를 확실히 떼어 내기 위해 픽셀만 새 이미지로 옮긴다
    clean = Image.new("RGB", img.size)
    clean.paste(img)
    return clean, upscaled


def encode_jpeg(img: Image.Image) -> bytes:
    """배포용 인코딩. 모든 이미지가 같은 설정으로 한 번만 압축된다."""
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, subsampling="4:2:0",
             optimize=False, progressive=False)
    return buf.getvalue()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def as_array(img: Image.Image) -> np.ndarray:
    return np.asarray(img, dtype=np.float64)


def load_exclusions(path: Path, manifest: list[dict]) -> tuple[set[str], list[str]]:
    """exclude.txt를 읽어 제외할 key 집합과, 어디에도 맞지 않은 줄 목록을 돌려준다.

    한 줄에 하나씩 정규화 key(main-ai-food-0012) 또는 원본 경로(raw/ai/food/x.png)를 적는다.
    key는 사진을 추가하면 번호가 밀리므로 원본 경로로 적는 편이 안전하다.
    """
    if not path.exists():
        return set(), []
    by_source = {m["source"]: m["key"] for m in manifest}
    keys_known = {m["key"] for m in manifest}
    keys, unmatched = set(), []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line in keys_known:
            keys.add(line)
        elif line in by_source:
            keys.add(by_source[line])
        else:
            unmatched.append(line)
    return keys, unmatched
