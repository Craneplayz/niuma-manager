# 微信数据采集

```text
wechat/
├── README.md                         # 说明环境、运行入口、输出格式和验收命令
├── .gitignore                        # 忽略微信导出数据、缓存和 Python 字节码
├── script/
│   ├── __init__.py                   # 标记脚本包并支持模块化测试导入
│   └── extract_data.py               # 从当前登录的 Windows 微信读取并导出聊天记录
├── tests/
│   ├── __init__.py                   # 标记测试包并支持 unittest 发现
│   └── test_extract_data.py          # 在无微信和无外部服务时校验导出脚本的纯函数
├── data/                             # 保存运行生成的会话 JSON 和本地附件
└── docs/
    └── archive/
        └── 2026-09-27-wechat-readme.md # 归档的旧版模块说明
```

## 运行环境

- Windows 10/11、64 位微信 4.x、Python 3.10+ 和已登录的微信进程是运行条件。
- `wechatauto-replica==1.2.3` 提供数据库读取、媒体恢复和 Windows 进程访问能力。

## 安装

```powershell
conda create -n wechat-export python=3.10 -y
conda run -n wechat-export python -m pip install --upgrade pip
conda run -n wechat-export python -m pip install "wechatauto-replica==1.2.3"
```

## 执行

在仓库根目录运行 `conda run -n wechat-export python wechat/script/extract_data.py`，结果写入 `wechat/data/`。

```powershell
conda run -n wechat-export python wechat/script/extract_data.py --help
conda run -n wechat-export python wechat/script/extract_data.py --days 3
conda run -n wechat-export python wechat/script/extract_data.py --skip-media
```

`--days` 设置滚动时间窗，`--output` 设置输出目录，`--workdir` 设置解密缓存目录，`--account` 和 `--db-dir` 用于多账号或非默认微信数据目录，`--session-limit` 限制会话数，`--skip-media` 跳过本地附件恢复。

脚本只读取本机微信数据库并复制已存在的附件，不发送消息、不操作微信界面、不修改微信数据库。

## 输出

- 每个时间窗内有实际消息的联系人或群聊生成一个 UTF-8、两空格缩进的 `<会话名>.json`。
- JSON 顶层字段固定为 `schema_version`、`chat_name`、`chat_username`、`chat_type`、`account`、`range`、`exported_at`、`message_count` 和 `messages`。
- `messages` 按时间升序保存发送者、类型、正文、资源列表、消息标识和可选媒体警告。
- 已恢复的图片、语音、视频和文件保存到 `data/assets/<会话名>/`，无法恢复的资源保留缓存路径或 URL。

## 验收

```powershell
python -m unittest discover -s wechat/tests -t . -q
python -m py_compile wechat/script/extract_data.py wechat/tests/test_extract_data.py
```

真实导出需要已登录的 Windows 微信；没有微信进程时只运行上面的离线验收命令。
