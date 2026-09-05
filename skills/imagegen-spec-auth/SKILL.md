---
name: imagegen-spec-auth
description: "通过指定 auth JSON 发现 OpenAI 兼容 Image API 的可用模型，优先使用 gpt-image-2，不可用时安全回退到其他图片生成模型；仅在服务端明确允许时安全重试，避免重复扣款。"
---

# 使用指定 auth JSON 生成图片

仅当用户明确指定认证文件、自定义 Image API 或本技能时使用；普通图片生成仍走内置 `image_gen` / `imagegen` 技能。

## 固定约定

- 认证文件默认且唯一使用 `~/.config/ai/imagegen.auth.json`（展开为 `$HOME/.config/ai/imagegen.auth.json`）。结构至少包含：`key`（API key）和 `url`（OpenAI-compatible base URL）；`_type` 可忽略。
- 只在进程内读取 `key`，绝不打印、提交、写入 prompt、命令参数或错误日志。不要把密钥放进 URL。
- 执行前可检查文件权限；若 group/world 可读，只提示风险，不要擅自改权限（需要用户明确授权后再 `chmod 600`）。
- 将 `url` 规范化为 API base：末尾已有 `/v1` 就保留，否则追加 `/v1`；避免 `/v1/v1`。
- 生成前必须用同一 auth JSON 查询 `/v1/models`，只从返回结果中接受 `gpt-image-2` 和 `grok-imagine-image`，并严格按此顺序尝试。最终报告实际使用的模型，不要把模型列表当成生成成功证据。
- 单次逻辑请求最多执行 5 次图片生成调用（含首次）。仅在 `gpt-image-2` 不可用或在安全重试后仍不稳定时切换到 `grok-imagine-image`；达到上限后立即停止，不再发第 6 次请求。
- 图片生成请求必须关闭 SDK 自动重试（`max_retries=0`），并为每个模型尝试发送幂等键（`Idempotency-Key`）。只有明确的模型不存在、不支持，或其它非认证/非提示词的模型调用错误才无等待地切换候选；`429 insufficient_quota` / `credit_balance_exhausted`、认证失败和提示词错误应立即停止，避免无意义请求或重复扣费。
- 生成超时、连接中断、HTTP 408/409/429 或 5xx 的结果可能已经在服务端扣费。只有错误体明确包含 `retryable: true` 和不超过 300 秒的 `retry_after`，或 503 明确表示 dispatcher `No available channel`（请求尚未进入生成）时，脚本才会等待并用同一个幂等键安全重试；前者最多 1 次，后者受总计 5 次调用上限约束。安全重试仍失败才切换候选模型。没有安全重试提示时，不重复同一请求；若已达到 5 次或没有候选，提示保留 `request-id`。若要人工重试，只有在服务端支持幂等键且已核对请求状态时才使用同一个 `--request-id`，不要用新 ID。

## 推荐执行

优先使用本技能的脚本，它会处理代理差异：

```bash
uv run --no-project --with openai python \
  ~/.codex/skills/imagegen-spec-auth/scripts/generate_image.py \
  --prompt "<图片提示词>" \
  --out ~/.output/imagegen/image.png \
  --request-id "<本次逻辑请求 ID>"
```

脚本默认 `1024x1024`、`medium`、单张输出，默认保存到用户目录下的 `~/.output/imagegen/`，支持 `--size`、`--quality`、`--timeout`、`--request-id`、`--force` 和 `--auth`。不传 `--request-id` 时脚本自动生成并打印本次 ID；服务端明确允许时脚本会按同一幂等键重试 1 次，模型不稳定时在总计 5 次调用内自动 failover。若最终仍失败，先确认服务端幂等键状态，再决定是否复用该 ID。生成结果应保存到 `~/.output/imagegen/`，再用 `view_image` 做视觉检查。

## 响应与常见陷阱

- 先读取 `data[0].b64_json`；若为 `None`，检查 `data[0].url` 并立即下载。很多代理生成成功但只返回 URL，不能把 `b64_json=None` 当成生成失败。
- URL 下载不应携带 API key，除非服务明确要求且 URL 属于同一受信任端点；优先使用代理返回的签名 URL。
- `429 insufficient_quota` / `credit_balance_exhausted` 是额度问题，停止重试并提示更换有额度的凭据；不要循环重试。
- 生成接口可能耗时数分钟：设置合理超时（脚本默认 300 秒）。仅对服务端明确标记 `retryable: true` 且提供有限 `retry_after` 的错误，或 dispatcher 明确返回无可用通道的 503，使用同一幂等键安全重试；前者最多 1 次，后者与 failover 合计最多 5 次调用。其他超时后不要启动第二个请求，先查询服务端是否已完成，确认幂等键支持后才可复用同一个 `request-id`。
- `openai` SDK 缺失时使用 `uv run --no-project --with openai ...`；不要修改系统或本技能自带的 `image_gen.py`。

## 交付检查

确认文件存在且是有效 PNG/JPEG/WebP，报告绝对路径、实际模型、认证文件路径（不含密钥）和最终 prompt。生成成功、文件落盘、视觉检查通过三者缺一不可。
