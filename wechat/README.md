# 微信数据采集

```text
wechat/
├── README.md                                  # 说明 Windows 导出、v0.3.1 同步和验收命令
├── .gitignore                                  # 忽略本地数据、缓存、快照和临时请求
├── script/                                    # 存放导出、转换、上传和平台适配脚本
│   ├── __init__.py                             # 标记微信脚本包
│   ├── extract_data.py                         # 从 Windows 微信数据库导出会话和本地媒体
│   ├── to_recognition.py                       # 保留旧版 source-to-text v0.2 JSONL 转换
│   ├── v031.py                                 # 生成 v0.3.1 请求、上传附件、提交导入并解析回执
│   └── adapters/                              # 隔离实时 Windows 读取和快照读取
│       ├── __init__.py                         # 按平台创建数据后端
│       ├── base.py                             # 定义后端错误和平台检测
│       ├── windows.py                          # 延迟加载 wechatauto 的 Windows 后端
│       └── macos.py                            # 读取规范化 macOS JSON 快照
├── tests/                                     # 存放不依赖微信进程的离线验收测试
│   ├── __init__.py                             # 标记测试包
│   ├── test_extract_data.py                    # 校验导出辅助函数和参数
│   ├── test_adapters.py                        # 校验平台选择和快照后端
│   ├── test_to_recognition.py                  # 校验旧 v0.2 转换兼容性
│   └── test_v031.py                            # 校验 v0.3.1 附件、上传回执、队列和错误状态
├── data/                                       # 保存本机导出的会话、附件、JSONL 和待提交队列
├── snapshots/                                  # 保存被忽略的规范化快照输入
└── docs/                                       # 存放微信模块文档
    └── archive/                                # 保存带日期的历史说明
        └── 2026-09-27-wechat-readme.md         # 归档旧版导出说明，不作为当前规范
```

## 环境

Windows 10/11、64 位已登录微信 4.x 使用 `wechat-export` 环境和 `wechatauto-replica==1.2.3`；macOS 仅保留快照入口。

```powershell
conda activate wechat-export
python -m pip install "wechatauto-replica==1.2.3"
```

## Windows 导出

`extract_data.py` 只读当前登录微信数据库，将消息和能恢复的 JPG/PNG/PDF/DOCX 写入 `wechat/data/`，其他媒体保留类型和失败原因。

```powershell
conda run --no-capture-output -n wechat-export python wechat/script/extract_data.py --platform windows
conda run --no-capture-output -n wechat-export python wechat/script/extract_data.py --platform windows --days 3 --skip-media
conda run --no-capture-output -n wechat-export python wechat/script/extract_data.py --platform windows --chat "合成课程群" --chat "10000000000@chatroom"
```

输出消息保留 `time`、`message_id`、发送者、URL、明确 `context`、本机附件路径和 `media_warning`；文件 mtime 不作为原始发布时间。

## v0.3.1 请求

`v031.py` 将会话 JSON 转成 v0.3.1 请求，附件只携带 `asset_id`、文件名、MIME、大小、SHA-256、状态和后端 `file_ref`，绝不携带本机路径、file URI 或凭据。

离线生成 JSONL（不上传原件）：

```powershell
conda run --no-capture-output -n wechat-export python wechat/script/v031.py wechat/data/<会话名>.json --output wechat/data/requests-v031.jsonl
```

离线请求中的 `available` 只在模拟或真实上传回执校验通过后出现；未下载、解密失败、不支持、超限和未授权上传分别输出 `asset_missing`、`decrypt_failed`、`unsupported_type`、`limit_exceeded` 和 `upload_failed`。

只处理用户明确提供的回复/引用上下文，不把相邻消息自动拼接；多个链接保留在 `upstream_hints`，不安全或带签名的 URL 不作为 `source_url`。

## 授权同步

`--sync` 在用户提供后端地址和限定导入凭据后上传原件到 `/api/sources/wechat/assets`，核对回执后提交 `/api/sources/wechat/imports`，并把待发送请求和 `job_id` 保存到队列文件。

```powershell
$env:WECHAT_IMPORT_URL = "https://example.invalid"
$env:WECHAT_IMPORT_TOKEN = "<后端签发的限定凭据>"
conda run --no-capture-output -n wechat-export python wechat/script/v031.py wechat/data/<会话名>.json --sync --output wechat/data/requests-v031.jsonl --queue-file wechat/data/pending-v031.json
```

网络中断保留原 `request_id` 并有限重试；重复请求由服务端按账号、请求 ID 和规范化内容幂等处理，队列文件不保存凭据。

## 回执与错误

客户端支持 `GET /api/sources/wechat/imports/{job_id}` 查询 `queued/running/succeeded/partial_failed/failed` 及记录级进度，并把服务器 `{error:{code,message,retryable}}` 原样转换为可重试反馈。

## 验收

```powershell
conda run --no-capture-output -n wechat-export python -X utf8 -m unittest discover -s wechat/tests -t . -q
conda run --no-capture-output -n wechat-export python -m py_compile wechat/script/extract_data.py wechat/script/to_recognition.py wechat/script/v031.py wechat/script/adapters/__init__.py wechat/script/adapters/base.py wechat/script/adapters/macos.py wechat/script/adapters/windows.py
python contracts/build_v031_bundle.py --check
python contracts/check_v031_examples.py
conda run --no-capture-output -n wechat-export python -m unittest discover -s contracts/tests -p "test_*.py" -v
```

实时读取需要已登录微信；无微信进程时使用固定 JSON 和 `wechat/tests/test_v031.py` 的模拟上传端验收。
