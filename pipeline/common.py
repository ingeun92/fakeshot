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

SIZE = 1024            # 정사각형 한 변 (px)
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


def normalize_image(img: Image.Image, size: int = SIZE) -> tuple[Image.Image, bool]:
    """회전 보정 → sRGB → 가운데 정사각형 크롭 → 리사이즈. (결과, 업스케일 여부)"""
    img = ImageOps.exif_transpose(img)
    img = to_srgb_rgb(img)
    w, h = img.size
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
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
