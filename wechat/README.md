# 微信数据采集

```text
wechat/
├── README.md                                  # 说明平台差异、快照格式、运行入口和验收命令
├── .gitignore                                  # 忽略导出结果、快照、缓存和本地临时文件
├── script/                                    # 存放导出、转换和平台适配脚本
│   ├── __init__.py                             # 标记脚本包并支持模块化导入
│   ├── extract_data.py                         # 统一命令行入口和 v1 会话 JSON 写出
│   ├── to_recognition.py                       # 把会话 JSON 转成 source-to-text v0.2 JSONL
│   └── adapters/                              # 隔离 Windows 实时读取和 macOS 快照读取
│       ├── __init__.py                         # 按平台选择数据后端
│       ├── base.py                             # 定义平台检测和后端错误
│       ├── windows.py                          # 延迟加载 wechatauto 的 Windows 实时后端
│       └── macos.py                            # 读取规范化 macOS JSON 快照的后端
├── tests/                                     # 存放不依赖微信进程的离线验收测试
│   ├── __init__.py                             # 标记测试包并支持 unittest 发现
│   ├── test_extract_data.py                    # 校验导出脚本的纯函数和参数
│   ├── test_adapters.py                        # 校验平台选择和 macOS 快照导出
│   └── test_to_recognition.py                  # 校验 v0.2 请求转换
├── data/                                       # 保存运行生成的会话 JSON 和本地附件
├── snapshots/                                  # 保存本机快照输入，已加入忽略规则
└── docs/                                      # 存放历史文档
    └── archive/                               # 存放带日期的旧版文档
        └── 2026-09-27-wechat-readme.md        # 归档旧版说明，不作为当前规范
```

## 安装

两种平台都使用 `wechat-export` 环境；Windows 需要额外安装 `wechatauto-replica`，macOS 只运行快照处理，不安装 Windows 专用依赖。

```powershell
conda create -n wechat-export python=3.10 -y
conda run -n wechat-export python -m pip install --upgrade pip
```

Windows 安装实时读取依赖：

```powershell
conda run -n wechat-export python -m pip install "wechatauto-replica==1.2.3"
```

## Windows 实时导出

Windows 10/11、64 位微信 4.x 和已登录的微信进程是实时读取条件；数据库、密钥、媒体恢复由 `wechatauto-replica` 提供。

```powershell
conda run -n wechat-export python wechat/script/extract_data.py --platform windows
conda run -n wechat-export python wechat/script/extract_data.py --platform windows --days 3 --skip-media
```

`--output` 设置输出目录，`--workdir` 设置解密缓存目录，`--account` 和 `--db-dir` 指定多账号或非默认微信目录，`--session-limit` 限制会话数。

## macOS 快照导出

macOS 路径不读取微信进程内存或私有数据库；它接收一个 JSON 文件或目录，快照必须包含 `messages` 数组，字段格式与本脚本输出的会话 JSON 相同。

快照文件可以是单个会话对象、会话对象数组，或包含 `account` 与 `chats` 数组的对象；个人快照建议放在被忽略的 `wechat/snapshots/`。

```powershell
conda run -n wechat-export python wechat/script/extract_data.py --platform macos --snapshot wechat/snapshots/export.json
conda run -n wechat-export python wechat/script/extract_data.py --snapshot wechat/snapshots/
```

macOS 的快照生成器可以独立替换，只要继续提供上述 JSON 输入，上传、时间过滤、文件命名和识别转换无需修改。

## 输出和识别

- 每个时间窗内有消息的联系人或群聊生成一个 UTF-8、两空格缩进的 `<会话名>.json`。
- JSON 顶层字段固定为 `schema_version`、`chat_name`、`chat_username`、`chat_type`、`account`、`range`、`exported_at`、`message_count` 和 `messages`。
- Windows 恢复的图片、语音、视频和文件保存到 `data/assets/<会话名>/`；macOS 快照中的 `assets` 字段原样保留。
- `conda run -n wechat-export python wechat/script/to_recognition.py wechat/data/<会话名>.json > wechat/data/requests.jsonl` 只转换文字消息，每行一个 source-to-text v0.2 请求。

## 验收

```powershell
conda run --no-capture-output -n wechat-export python -X utf8 -m unittest discover -s wechat/tests -t . -q
conda run --no-capture-output -n wechat-export python -m py_compile wechat/script/extract_data.py wechat/script/to_recognition.py wechat/script/adapters/__init__.py wechat/script/adapters/base.py wechat/script/adapters/macos.py wechat/script/adapters/windows.py
```

实时 Windows 导出需要已登录微信；没有微信进程时使用 macOS 快照或执行上述离线验收命令。
