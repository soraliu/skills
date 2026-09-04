#!/usr/bin/env python3
"""使用指定 auth JSON 发现图片模型并兼容 base64 或 URL 图片响应。"""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_AUTH = "~/.config/ai/imagegen.auth.json"
DEFAULT_OUT = "output/imagegen/image.png"

# 排序值越小越优先；其余带有明确图片生成标识的模型排在已知模型之后。
KNOWN_IMAGE_MODELS = (
    "gpt-image-2",
    "gpt-image-1",
    "gpt-image-1-mini",
    "dall-e-3",
    "dall-e-2",
)
IMAGE_MODEL_HINTS = (
    "gpt-image",
    "dall-e",
    "imagen",
    "stable-diffusion",
    "stable_diffusion",
    "sdxl",
    "flux",
    "ideogram",
    "midjourney",
    "nano-banana",
    "image-generation",
    "text-to-image",
)
NON_GENERATIVE_HINTS = (
    "embedding",
    "vision",
    "caption",
    "ocr",
    "moderation",
    "upscale",
    "inpaint",
    "edit",
)


def fail(message: str, code: int = 1) -> "NoReturn":
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def load_auth(path_text: str) -> tuple[str, str]:
    path = Path(os.path.expanduser(path_text))
    if not path.is_file():
        fail(f"auth 文件不存在: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"无法读取 auth 文件 {path}: {exc}")
    if not isinstance(data, dict):
        fail("auth 文件顶层必须是 JSON object")

    key = data.get("key")
    url = data.get("url")
    if not isinstance(key, str) or not key.strip():
        fail("auth 文件缺少非空字符串字段 key")
    if not isinstance(url, str) or not url.strip():
        fail("auth 文件缺少非空字符串字段 url")

    base_url = url.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    return key, base_url


def item_field(item: object, name: str) -> object:
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def model_key(model: str) -> str:
    """去掉 provider 前缀和版本后缀，便于识别模型能力。"""
    key = model.strip().casefold().rsplit("/", 1)[-1]
    return re.split(r"[:@]", key, maxsplit=1)[0]


def image_model_rank(model: str) -> int | None:
    key = model_key(model)
    for rank, known in enumerate(KNOWN_IMAGE_MODELS):
        if key == known or key.startswith(f"{known}-"):
            return rank
    if any(hint in key for hint in NON_GENERATIVE_HINTS):
        return None
    if any(hint in key for hint in IMAGE_MODEL_HINTS):
        return 100
    # 一些兼容代理把生成模型命名为 *-image-preview；排除视觉/嵌入模型后再接纳。
    if "image" in key:
        return 100
    return None


def select_image_models(items: object) -> list[str]:
    """从 /v1/models 返回值中提取并按优先级排序图片生成模型。"""
    if isinstance(items, dict):
        items = items.get("data", [])
    try:
        candidates = list(items or [])
    except TypeError:
        return []

    models: dict[str, tuple[int, str]] = {}
    for item in candidates:
        value = item_field(item, "id")
        if not isinstance(value, str) or not value.strip():
            continue
        model = value.strip()
        rank = image_model_rank(model)
        if rank is not None:
            models.setdefault(model.casefold(), (rank, model))
    return [model for _, model in sorted(models.values(), key=lambda pair: (pair[0], pair[1].casefold()))]


def redact(message: str, secret: str) -> str:
    return message.replace(secret, "[REDACTED]") if secret else message


def is_quota_error(message: str) -> bool:
    lowered = message.casefold()
    return "insufficient_quota" in lowered or "credit_balance_exhausted" in lowered


def is_model_unavailable_error(message: str) -> bool:
    lowered = message.casefold()
    return any(
        marker in lowered
        for marker in (
            "model_not_found",
            "model not found",
            "does not exist",
            "unknown model",
            "invalid model",
            "unsupported model",
            "model is not supported",
            "model is not available",
            "model unavailable",
        )
    )


def generation_kwargs(model: str, args: argparse.Namespace) -> dict[str, object]:
    """只发送目标模型支持的常用参数，避免 fallback 被 gpt 专属参数阻断。"""
    kwargs: dict[str, object] = {"model": model, "prompt": args.prompt, "n": 1, "size": args.size}
    key = model_key(model)
    if key.startswith("gpt-image"):
        kwargs.update(quality=args.quality, output_format="png")
    elif key == "dall-e-3":
        kwargs["quality"] = "hd" if args.quality == "high" else "standard"
    return kwargs


def list_image_models(client: object, secret: str) -> list[str]:
    try:
        response = client.models.list()
    except Exception as exc:
        message = redact(str(exc), secret)
        if is_quota_error(message):
            fail("API 额度不足（insufficient_quota）；请更换有额度的 auth JSON，不要重试")
        fail(f"无法查询可用模型: {message}")
    models = select_image_models(getattr(response, "data", response))
    if not models:
        fail("/v1/models 未返回可用的图片生成模型")
    print(f"Available image models: {', '.join(models)}", file=sys.stderr)
    return models


def download_image(url: str, timeout: float) -> bytes:
    if url.startswith("data:"):
        header, payload = url.split(",", 1)
        if ";base64" not in header:
            fail("不支持非 base64 data URL")
        return base64.b64decode(payload)

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        fail("图片 URL 不是可下载的 HTTP(S) URL")
    request = Request(url, headers={"User-Agent": "codex-imagegen-auth"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except Exception as exc:
        fail(f"下载代理返回的图片 URL 失败: {exc}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="通过 auth JSON 发现模型并生成图片")
    parser.add_argument("--prompt", required=True, help="图片提示词")
    parser.add_argument("--auth", default=DEFAULT_AUTH, help=f"auth JSON 路径（默认 {DEFAULT_AUTH}）")
    parser.add_argument("--out", default=DEFAULT_OUT, help=f"输出文件（默认 {DEFAULT_OUT}）")
    parser.add_argument("--size", default="1024x1024")
    parser.add_argument("--quality", choices=("low", "medium", "high", "auto"), default="medium")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--force", action="store_true", help="允许覆盖已有输出")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.prompt.strip():
        fail("prompt 不能为空")
    if args.timeout <= 0:
        fail("timeout 必须大于 0")

    key, base_url = load_auth(args.auth)
    try:
        from openai import OpenAI
    except ImportError:
        fail("缺少 openai SDK；请使用 uv run --no-project --with openai python ...", 2)

    client = OpenAI(api_key=key, base_url=base_url, timeout=args.timeout, max_retries=0)
    models = list_image_models(client, key)
    output = Path(args.out).expanduser()
    if output.exists() and not args.force:
        fail(f"输出已存在: {output}（需要覆盖时加 --force）")
    output.parent.mkdir(parents=True, exist_ok=True)

    result = None
    selected_model = None
    unavailable_errors: list[str] = []
    for model in models:
        print(f"Calling {model} via {urlparse(base_url).netloc or base_url} ...", file=sys.stderr)
        try:
            result = client.images.generate(**generation_kwargs(model, args))
        except Exception as exc:
            message = redact(str(exc), key)
            if is_quota_error(message):
                fail("API 额度不足（insufficient_quota）；请更换有额度的 auth JSON，不要重试")
            if is_model_unavailable_error(message):
                unavailable_errors.append(f"{model}: {message}")
                print(f"Model {model} unavailable; trying next candidate.", file=sys.stderr)
                continue
            fail(f"Image API 调用失败（{model}）: {message}")
        selected_model = model
        break

    if result is None:
        details = "; ".join(unavailable_errors)
        fail(f"所有可用图片模型均不可用{f': {details}' if details else ''}")
    if not getattr(result, "data", None):
        fail(f"Image API 返回空 data（{selected_model}）")
    item = result.data[0]
    b64_json = item_field(item, "b64_json")
    image_url = item_field(item, "url")
    if isinstance(b64_json, str) and b64_json:
        try:
            raw = base64.b64decode(b64_json, validate=True)
        except Exception as exc:
            fail(f"b64_json 解码失败: {exc}")
    elif isinstance(image_url, str) and image_url:
        raw = download_image(image_url, args.timeout)
    else:
        fail("响应既没有 b64_json 也没有 url")

    if not raw:
        fail("下载到空图片")
    output.write_bytes(raw)
    print(f"Wrote {output.resolve()} (model={selected_model})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
