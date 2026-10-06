# v0.3.1 图片识别入口

本入口实现 #94 的静态 JPG/PNG 读取与识别；不提供上传、账号鉴权、任务队列或前端入口，PDF/DOCX 返回 `reader_unsupported`，现有 v0.2 路由保持不变。

## 安装与调用

在仓库根目录用 Python 3.10+ 执行 `python -m pip install -r ddl_text/media_requirements.txt`；请求和结果只使用 [共享 v0.3.1 契约](../../contracts/docs/text-attachments-v0.3.1.md)。

```python
from pathlib import Path
from ddl_text.chat_client import ChatClient
from ddl_text.codex_client import live_options
from ddl_text.config import Settings
from ddl_text.media_pipeline import process_media_request

options = live_options(Path('.env'))  # 读取原有模型配置，不把密钥放进请求 JSON
settings = Settings(mode='real', model=options['model'], endpoint=options['endpoint'],
                    timeout_seconds=45, max_retries=0)
result = process_media_request(request, account_bound_loader,
                               ChatClient(options['key']), settings)
```

`account_bound_loader(file_ref, *, max_bytes, timeout_seconds) -> bytes` 是 #96 提供的可信函数：从已认证账号绑定的存储读取，逐次检查归属、撤销及有效期，遵守字节和读取时限；不存在、他人或已撤销引用统一抛 `FileNotFoundError`。识别器不会读取 JSON 中的本机路径或 URL，也不会替后端证明账号隔离。此函数在调用者进程执行，远程存储须实现自己的 I/O 超时；不可传入无界下载器。

调用入口应放在非 daemon 的作业工作进程或后端线程中，Windows 脚本使用 `if __name__ == '__main__'` 保护；解码和模型调用使用会回收的 spawn 子进程，避免挂起一个任务拖住其后所有任务。

- `MediaInputError` 为安全的 `invalid_request/limit_exceeded` 拒绝码；`image_reader.MetadataMismatch` 为 `asset_metadata_mismatch`，#96 应映射为 400，不能当作无任务；业务后端应在接收时先校验归属与回执，识别侧再次核对真实字节。
- 模型只接收重新编码的 PNG 像素和必要消息上下文，不接收 file_ref、存储路径或上传凭据；EXIF 方向得到应用，EXIF/GPS/PNG 注释被去除，不缩图或裁图。
- 默认每原件 20 MiB、1600 万像素、重新编码后 8 MiB；每次模型输入含规则及 base64 共 16 MiB，解码 10 秒、记录 70 秒、批次 180 秒，可通过 `ImageLimits/MediaLimits` 调整；网络调用时间取 Settings 与剩余预算较小值。超过上限明确报错，不悄悄截断。
- 每记录一次模型调用，把本次可读取图片及文字一起分析，保留跨图片的要求与日期联系；不缓存不同消息的语义结果、不自动重试；大批任务的队列并发、幂等和失败重试归 #96。潜在错误数量超过输出契约上限时在调用前拒绝；结果合计超过 2 MB 时将新增超限记录报错，保留此前成功候选；若连错误与覆盖信息也超限，则整体返回 `limit_exceeded`，请拆分批次。
- 已有文字块模式沿用输入的覆盖、质量和逐字证据校验；受支持的是提供完整元数据的文字块，不会在此入口额外执行 OCR。

## 输出和局限

读取结果由程序填写，模型只能补充可读性疑点：`adapter_confirmed/complete` 代表整张静态图已成功解码且全部像素送到正常结束的模型调用，不能证明模型逐字读对、读全或零漏报。动态 PNG 拒绝，不只读取第一帧。

图片证据只能为 `attachment_file/model_reported`，候选强制待审核；未知页码/位置不补造，文字与上下文引文用原文核验，来源地址从输入复制。质量不明且无任务返回失败，不能 `no_tasks`；部分附件失败仍保留其他可靠候选，缺失内容可能含期限时 `not_provided` 改为 `unknown`。

任务、时间及旧图转发判断由 [图片规则](../skills/image-task-extraction/SKILL.md) 指导，结构/日期/时区/证据由共享校验器复核；本步未实现独立图片召回率评测，也不自动推断用户是否做完作业。

后端接入前须完成 #103 的 v0.3.1 迁移、真实鉴权/文件生命周期、结果持久化及重试保留规则；本入口不替代 `check_retry_result`，不能用一次重新生成的结果覆盖此前成功候选和用户操作。模型需具备图片能力，不会自动切换供应商。

## 自检

```powershell
python -m unittest ddl_text.tests.test_media_pipeline ddl_text.tests.test_media_transport -v
python -m unittest discover -s ddl_text/tests -t . -q
python contracts/check_v031_examples.py
# 显式付费测试：只生成非私人图片，输出路径必须不存在
python -m ddl_text.tests.run_media_smoke --live --output local-data/media-smoke.json
```

可查看 [五组真实模型合成测试的输入、草稿和完整结果](archive/2026-10-06-issue94-images.json)，包括明确时间与催促语、省略年份、无期限、无行动及跨图片关联；这些样例不能替代真实微信联调。

传输格式与像素限制处理参考 [七牛请求文档](https://developer.qiniu.com/aitokenapi/13379/real-time-ai-interface-api) 和 [Pillow 安全说明](https://pillow.readthedocs.io/en/stable/handbook/security.html)；以实际选用模型的合成实测为准，不能据通用 API 文档声称所有模型都支持文件。
