---
name: imagegen-spec-auth
description: "通过指定 auth JSON 发现 OpenAI 兼容 Image API 的可用模型，优先使用 gpt-image-2，不可用时回退到其他图片生成模型；处理 /v1 端点、URL/base64 响应、超时和配额错误。"
---

# 使用指定 auth JSON 生成图片

仅当用户明确指定认证文件、自定义 Image API 或本技能时使用；普通图片生成仍走内置 `image_gen` / `imagegen` 技能。

## 固定约定

- 认证文件默认且唯一使用 `~/.config/ai/imagegen.auth.json`（展开为 `$HOME/.config/ai/imagegen.auth.json`）。结构至少包含：`key`（API key）和 `url`（OpenAI-compatible base URL）；`_type` 可忽略。
- 只在进程内读取 `key`，绝不打印、提交、写入 prompt、命令参数或错误日志。不要把密钥放进 URL。
- 执行前可检查文件权限；若 group/world 可读，只提示风险，不要擅自改权限（需要用户明确授权后再 `chmod 600`）。
- 将 `url` 规范化为 API base：末尾已有 `/v1` 就保留，否则追加 `/v1`；避免 `/v1/v1`。
- 生成前必须用同一 auth JSON 查询 `/v1/models`，从返回的可用模型中选择图片生成模型；精确匹配 `gpt-image-2` 时优先使用它，否则按脚本的图片模型识别与排序规则尝试其他候选。最终报告实际使用的模型，不要把模型列表当成生成成功证据。
- 只有明确的模型不存在、不支持或参数不兼容错误才继续下一个候选；`429 insufficient_quota` / `credit_balance_exhausted`、认证失败、网络错误和提示词错误应立即停止，避免重复请求或重复扣费。

## 推荐执行

优先使用本技能的脚本，它会处理代理差异：

```bash
uv run --no-project --with openai python \
  ~/.codex/skills/imagegen-spec-auth/scripts/generate_image.py \
  --prompt "<图片提示词>" \
  --out output/imagegen/image.png
```

脚本默认 `1024x1024`、`medium`、单张输出，支持 `--size`、`--quality`、`--timeout`、`--force` 和 `--auth`。生成结果应保存到项目的 `output/imagegen/`，再用 `view_image` 做视觉检查。

## 响应与常见陷阱

- 先读取 `data[0].b64_json`；若为 `None`，检查 `data[0].url` 并立即下载。很多代理生成成功但只返回 URL，不能把 `b64_json=None` 当成生成失败。
- URL 下载不应携带 API key，除非服务明确要求且 URL 属于同一受信任端点；优先使用代理返回的签名 URL。
- `429 insufficient_quota` / `credit_balance_exhausted` 是额度问题，停止重试并提示更换有额度的凭据；不要循环重试。
- 生成接口可能耗时数分钟：设置合理超时（脚本默认 300 秒），同一 prompt 未结束前不要再启动第二个请求，避免重复扣费和悬挂进程。
- `openai` SDK 缺失时使用 `uv run --no-project --with openai ...`；不要修改系统或本技能自带的 `image_gen.py`。

## 交付检查

确认文件存在且是有效 PNG/JPEG/WebP，报告绝对路径、实际模型、认证文件路径（不含密钥）和最终 prompt。生成成功、文件落盘、视觉检查通过三者缺一不可。
