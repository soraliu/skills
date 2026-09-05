#!/usr/bin/env python3
"""使用指定 auth JSON 发现图片模型并兼容 base64 或 URL 图片响应。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import uuid


DEFAULT_AUTH = "~/.config/ai/imagegen.auth.json"
DEFAULT_OUT = "~/.output/imagegen/image.png"
MAX_REQUEST_ID_LENGTH = 96
MAX_PROVIDER_RETRIES = 1
MAX_CAPACITY_RETRIES = 4
MAX_RETRY_AFTER_SECONDS = 300.0
MAX_GENERATION_ATTEMPTS = 5
CAPACITY_RETRY_DELAY_SECONDS = 5.0

# 只允许这两个候选，顺序也是 failover 顺序。
IMAGE_MODEL_ORDER = (
    "gpt-image-2",
    "grok-imagine-image",
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
    try:
        return IMAGE_MODEL_ORDER.index(key)
    except ValueError:
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


def is_fatal_generation_error(exc: Exception, message: str) -> bool:
    """认证、权限或提示词错误不会因换模型而恢复。"""
    status_code = getattr(exc, "status_code", None)
    if status_code in {400, 401, 403}:
        return any(
            marker in message.casefold()
            for marker in (
                "authentication",
                "unauthorized",
                "invalid api key",
                "permission",
                "forbidden",
                "content policy",
                "invalid prompt",
                "prompt is required",
            )
        ) or status_code in {401, 403}
    lowered = message.casefold()
    return any(
        marker in lowered
        for marker in ("invalid api key", "authentication failed", "content policy", "invalid prompt")
    )


def is_ambiguous_generation_error(exc: Exception) -> bool:
    """请求可能已在服务端执行；此类错误禁止自动重试和候选回退。"""
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and (status_code in {408, 409, 429} or status_code >= 500):
        return True
    name = type(exc).__name__.casefold()
    message = str(exc).casefold()
    return any(
        marker in name or marker in message
        for marker in ("timeout", "timed out", "connectionerror", "connection error", "readerror")
    )


def is_capacity_unavailable_error(exc: Exception, message: str) -> bool:
    """503 分发器无通道表示请求尚未进入模型生成，可安全复用幂等键重试。"""
    status_code = getattr(exc, "status_code", None)
    lowered = message.casefold()
    return status_code == 503 and any(
        marker in lowered
        for marker in ("no available channel", "no available channels", "capacity unavailable")
    )


def remote_request_id(exc: Exception) -> str | None:
    value = getattr(exc, "request_id", None)
    if isinstance(value, str) and value:
        return value
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    value = headers.get("x-request-id") if headers is not None else None
    return value if isinstance(value, str) and value else None


def error_summary(exc: Exception, secret: str = "") -> str:
    """提取短错误摘要，避免把代理完整响应或签名 URL 写入日志。"""
    status_code = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    payloads = [body]
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        payloads.append(body["error"])
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        detail = next(
            (
                payload.get(field)
                for field in ("detail", "message", "title", "error_name", "code", "type")
                if isinstance(payload.get(field), str) and payload.get(field)
            ),
            None,
        )
        if detail:
            prefix = f"HTTP {status_code}: " if isinstance(status_code, int) else ""
            return f"{prefix}{detail}"[:500]
    return redact(str(exc), secret).replace("\n", " ")[:500]


def make_request_id(value: str | None) -> str:
    if value is None:
        return uuid.uuid4().hex
    request_id = value.strip()
    if not re.fullmatch(rf"[A-Za-z0-9._:-]{{1,{MAX_REQUEST_ID_LENGTH}}}", request_id):
        fail(
            f"request-id 只能包含 ASCII 字母、数字、点、下划线、冒号或连字符，"
            f"长度不超过 {MAX_REQUEST_ID_LENGTH}"
        )
    return request_id


def attempt_idempotency_key(request_id: str, model: str) -> str:
    suffix = hashlib.sha256(model.encode("utf-8")).hexdigest()[:16]
    return f"{request_id}:{suffix}"


def unknown_generation_message(
    model: str, request_id: str, reason: str, exc: Exception | None = None
) -> str:
    provider_id = remote_request_id(exc) if exc is not None else None
    provider_note = f", provider_request_id={provider_id}" if provider_id else ""
    return (
        f"图片生成结果未知（model={model}, request_id={request_id}{provider_note}）：{reason}；"
        "已停止本次逻辑请求的继续尝试。请先向服务端查询该幂等键，再决定是否用同一 request-id 重试"
    )


def provider_retry_delay(exc: Exception) -> float | None:
    """只接受服务端明确声明 retryable=true 且给出有限等待时间的重试。"""
    body = getattr(exc, "body", None)
    bodies = [body]
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        bodies.append(body["error"])
    headers = getattr(getattr(exc, "response", None), "headers", None)
    for candidate in bodies:
        if not isinstance(candidate, dict) or candidate.get("retryable") is not True:
            continue
        value = candidate.get("retry_after")
        if value is None and headers is not None:
            value = headers.get("retry-after")
        try:
            delay = float(value)
        except (TypeError, ValueError):
            return None
        if math.isfinite(delay) and 0 <= delay <= MAX_RETRY_AFTER_SECONDS:
            return delay
    return None


def safe_retry_delay(exc: Exception, message: str) -> float | None:
    delay = provider_retry_delay(exc)
    if delay is not None:
        return delay
    if is_capacity_unavailable_error(exc, message):
        return CAPACITY_RETRY_DELAY_SECONDS
    return None


def generation_kwargs(
    model: str, args: argparse.Namespace, idempotency_key: str
) -> dict[str, object]:
    """只发送目标模型支持的常用参数，避免 fallback 被 gpt 专属参数阻断。"""
    kwargs: dict[str, object] = {
        "model": model,
        "prompt": args.prompt,
        "n": 1,
        "size": args.size,
        "extra_headers": {"Idempotency-Key": idempotency_key},
    }
    key = model_key(model)
    if key.startswith("gpt-image"):
        kwargs.update(quality=args.quality, output_format="png")
    return kwargs


def list_image_models(client: object, secret: str, request_id: str) -> list[str]:
    try:
        response = client.models.list()
    except Exception as exc:
        raw_message = redact(str(exc), secret)
        message = error_summary(exc, secret)
        if is_quota_error(raw_message):
            fail("API 额度不足（insufficient_quota）；请更换有额度的 auth JSON，不要重试")
        if is_ambiguous_generation_error(exc):
            fail(f"模型发现请求超时或连接不确定（request_id={request_id}）；未发起图片生成，请勿自动重试")
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
            raise ValueError("不支持非 base64 data URL")
        return base64.b64decode(payload)

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("图片 URL 不是可下载的 HTTP(S) URL")
    request = Request(url, headers={"User-Agent": "codex-imagegen-auth"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except Exception as exc:
        # 不把签名 URL（可能含敏感查询参数）写入错误日志。
        raise RuntimeError(f"下载代理返回的图片 URL 失败: {type(exc).__name__}") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="通过 auth JSON 发现模型并生成图片")
    parser.add_argument("--prompt", required=True, help="图片提示词")
    parser.add_argument("--auth", default=DEFAULT_AUTH, help=f"auth JSON 路径（默认 {DEFAULT_AUTH}）")
    parser.add_argument("--out", default=DEFAULT_OUT, help=f"输出文件（默认 {DEFAULT_OUT}）")
    parser.add_argument("--size", default="1024x1024")
    parser.add_argument("--quality", choices=("low", "medium", "high", "auto"), default="medium")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument(
        "--request-id",
        help="逻辑请求 ID；超时后仅在服务端支持幂等键时用同一 ID 查询/重试",
    )
    parser.add_argument("--force", action="store_true", help="允许覆盖已有输出")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.prompt.strip():
        fail("prompt 不能为空")
    if args.timeout <= 0:
        fail("timeout 必须大于 0")

    key, base_url = load_auth(args.auth)
    request_id = make_request_id(args.request_id)
    output = Path(args.out).expanduser()
    if output.exists() and not args.force:
        fail(f"输出已存在: {output}（需要覆盖时加 --force）")
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        from openai import OpenAI
    except ImportError:
        fail("缺少 openai SDK；请使用 uv run --no-project --with openai python ...", 2)

    client = OpenAI(api_key=key, base_url=base_url, timeout=args.timeout, max_retries=0)
    print(f"Request ID: {request_id}", file=sys.stderr)
    models = list_image_models(client, key, request_id)

    result = None
    selected_model = None
    unavailable_errors: list[str] = []
    generation_attempts = 0
    for model_index, model in enumerate(models):
        if generation_attempts >= MAX_GENERATION_ATTEMPTS:
            break
        has_next_model = model_index + 1 < len(models)
        idempotency_key = attempt_idempotency_key(request_id, model)
        retry_count = 0
        while True:
            if generation_attempts >= MAX_GENERATION_ATTEMPTS:
                break
            generation_attempts += 1
            print(f"Calling {model} via {urlparse(base_url).netloc or base_url} ...", file=sys.stderr)
            try:
                result = client.images.generate(
                    **generation_kwargs(model, args, idempotency_key)
                )
            except Exception as exc:
                raw_message = redact(str(exc), key)
                message = error_summary(exc, key)
                error_text = f"{raw_message} {message}"
                if is_quota_error(error_text):
                    fail("API 额度不足（insufficient_quota）；请更换有额度的 auth JSON，不要重试")
                if is_fatal_generation_error(exc, error_text):
                    fail(f"Image API 调用失败（{model}）: {message}")
                ambiguous = is_ambiguous_generation_error(exc)
                capacity_error = is_capacity_unavailable_error(exc, message)
                if not ambiguous and not capacity_error and is_model_unavailable_error(error_text):
                    unavailable_errors.append(f"{model}: {message}")
                    print(f"Model {model} unavailable; trying next candidate.", file=sys.stderr)
                    break
                if generation_attempts >= MAX_GENERATION_ATTEMPTS:
                    fail(f"已达到最多 {MAX_GENERATION_ATTEMPTS} 次图片生成尝试（最后模型：{model}）")
                delay = safe_retry_delay(exc, message)
                retry_limit = MAX_PROVIDER_RETRIES
                if capacity_error:
                    # 有后续模型时尽快 failover；最后一个模型可用完剩余总次数等待容量恢复。
                    retry_limit = 1 if has_next_model else MAX_CAPACITY_RETRIES
                if delay is not None and retry_count < retry_limit:
                    retry_count += 1
                    print(
                        f"Provider marked {model} retryable; waiting {delay:g}s and reusing the same idempotency key.",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                    continue
                if retry_count:
                    if has_next_model:
                        print(
                            f"Model {model} remained unstable after safe retries; failing over to the next image model.",
                            file=sys.stderr,
                        )
                        break
                    fail(unknown_generation_message(model, request_id, message, exc))
                if ambiguous:
                    fail(unknown_generation_message(model, request_id, message, exc))
                if has_next_model and generation_attempts < MAX_GENERATION_ATTEMPTS:
                    print(
                        f"Model {model} failed; failing over to the next image model.",
                        file=sys.stderr,
                    )
                    break
                fail(f"Image API 调用失败（{model}）: {message}")
            else:
                selected_model = model
                break
        if result is not None:
            break

    if result is None:
        details = "; ".join(unavailable_errors)
        if generation_attempts >= MAX_GENERATION_ATTEMPTS:
            fail(f"已达到最多 {MAX_GENERATION_ATTEMPTS} 次图片生成尝试，未得到可用结果")
        fail(f"所有可用图片模型均不可用{f': {details}' if details else ''}")
    if not getattr(result, "data", None):
        fail(unknown_generation_message(selected_model or "unknown", request_id, "Image API 返回空 data"))
    item = result.data[0]
    b64_json = item_field(item, "b64_json")
    image_url = item_field(item, "url")
    if isinstance(b64_json, str) and b64_json:
        try:
            raw = base64.b64decode(b64_json, validate=True)
        except Exception as exc:
            fail(unknown_generation_message(selected_model or "unknown", request_id, "b64_json 解码失败", exc))
    elif isinstance(image_url, str) and image_url:
        try:
            raw = download_image(image_url, args.timeout)
        except Exception as exc:
            fail(unknown_generation_message(selected_model or "unknown", request_id, str(exc), exc))
    else:
        fail(unknown_generation_message(selected_model or "unknown", request_id, "响应既没有 b64_json 也没有 url"))

    if not raw:
        fail(unknown_generation_message(selected_model or "unknown", request_id, "下载到空图片"))
    temporary = tempfile.NamedTemporaryFile(
        dir=output.parent, prefix=f".{output.name}.", suffix=".tmp", delete=False
    )
    temporary_path = Path(temporary.name)
    try:
        with temporary:
            temporary.write(raw)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, output)
    finally:
        temporary_path.unlink(missing_ok=True)
    print(f"Wrote {output.resolve()} (model={selected_model}, attempts={generation_attempts})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
