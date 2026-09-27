# 微信最近聊天记录导出程序

本项目使用 [`wechatauto-replica`](https://pypi.org/project/wechatauto-replica/) 读取当前已登录的 Windows 微信 4.x 本地数据库，将指定时间范围内的聊天记录导出为便于人工阅读和程序处理的 JSON 文件。

默认导出最近 7×24 小时内的消息。每个联系人或群聊对应一个 JSON 文件；在时间范围内没有实际聊天消息的会话会被忽略。

> 本程序只读取本机微信数据并向项目的 `data` 目录写入导出结果，不会发送微信消息，也不会修改微信数据库。

## 1. 项目结构

```text
message_repo/
├─ script/
│  └─ extract_data.py       # 聊天记录导出脚本
├─ data/                    # 导出结果目录
│  ├─ 联系人名称.json
│  ├─ 群聊名称.json
│  └─ assets/               # 能够恢复或复制的本地媒体附件
└─ readme.md
```

程序运行后：

- 每个活跃联系人或群聊生成一个以其显示名称命名的 JSON 文件；
- Windows 文件名中的非法字符会被替换为下划线；
- 如果不同会话名称相同，文件名会自动附加会话标识，避免相互覆盖；
- 图片、语音、视频和文件等本地媒体会尽量恢复到 `data/assets/<会话名>/`；
- 网页链接、表情资源地址以及无法复制的本地媒体地址会记录在 JSON 的 `assets` 字段中。

## 2. 运行环境要求

### 2.1 操作系统与微信

- Windows 10 或 Windows 11，64 位；
- 微信 Windows 客户端 4.x，建议微信 4.1.12 或更高版本；
- 微信必须已经登录并保持运行；
- Python 与微信需要具有相容的进程权限：
  - 微信普通权限运行时，普通 PowerShell/Anaconda Prompt 通常即可；
  - 如果微信以管理员身份运行，运行本脚本的终端也需要以管理员身份启动；
- 不支持网页版微信、手机微信或 macOS/Linux 微信客户端。

### 2.2 Python 与 Conda

- 推荐使用 Miniconda 或 Anaconda；
- Python 3.9 或更高版本；
- 本项目已使用 Python 3.10 和 `wechatauto-replica 1.2.3` 验证。

### 2.3 磁盘空间

首次运行时，`wechatauto-replica` 会在系统临时目录中生成解密数据库缓存。缓存大小取决于微信历史数据量，可能达到数百 MB 或更多。

导出的 JSON 和成功恢复的附件保存在项目的 `data` 目录中。程序不会主动下载所有远程资源，因此实际占用空间通常小于微信原始数据目录。

## 3. 创建 Conda 环境

打开 PowerShell、Anaconda Prompt 或 Miniconda Prompt，执行：

```powershell
conda create -n wechat-export python=3.10 -y
conda activate wechat-export
python -m pip install --upgrade pip
python -m pip install "wechatauto-replica==1.2.3"
```

`wechatauto-replica` 会自动安装程序所需的主要依赖，包括：

- `cryptography`；
- `pywin32`；
- `psutil`；
- `uiautomation` 与 `comtypes`；
- `Pillow`；
- `zstandard`；
- `opencv-python`；
- `imageio-ffmpeg`。

检查安装是否成功：

```powershell
conda run -n wechat-export python -m pip show wechatauto-replica
```

应当能够看到包名和版本信息，例如：

```text
Name: wechatauto-replica
Version: 1.2.3
```

> 安装包名称为 `wechatauto-replica`，Python 中实际使用的导入名称是 `wechatauto`。

## 4. 运行程序

### 4.1 运行前准备

1. 启动 Windows 微信客户端；
2. 确认目标微信账号已登录；
3. 保持微信进程运行；
4. 在 PowerShell 中进入项目根目录。

例如：

```powershell
cd "C:\path\to\message_repo"
```

### 4.2 默认运行

在项目根目录执行：

```powershell
conda run -n wechat-export python script\extract_data.py
```

也可以先激活环境再执行：

```powershell
conda activate wechat-export
python script\extract_data.py
```

默认行为如下：

- 导出脚本启动时刻之前最近 7×24 小时的消息；
- 自动识别当前登录账号；
- 忽略该时间范围内没有实际消息的联系人和群聊；
- 将 JSON 写入项目根目录的 `data`；
- 尝试恢复或复制本地已有的媒体文件；
- 解密数据库缓存写入系统临时目录，而不是项目目录。

运行时会显示进度，例如：

```text
使用 wechatauto-replica 1.2.3
正在初始化当前登录微信的本地数据库读取器……
当前账号：示例用户 (wxid_example)
导出时间窗：2026-09-17T18:16:47+08:00 至 2026-09-24T18:16:47+08:00
七天内候选活跃会话：175
[1/175] 读取：项目讨论群
  已保存 42 条消息 -> ...\data\项目讨论群.json
导出完成：79 个会话，16773 条消息，输出目录：...\data
```

时间窗是以脚本启动时间为终点的滚动时间范围，不是按自然周一至周日计算。

## 5. 命令行参数

查看全部参数：

```powershell
conda run -n wechat-export python script\extract_data.py --help
```

支持的参数如下：

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `--days DAYS` | `7` | 导出最近多少天，可以使用小数，例如 `0.5` 表示最近 12 小时 |
| `--output PATH` | `data` | 指定输出目录 |
| `--workdir PATH` | 系统临时目录 | 指定解密数据库缓存目录 |
| `--account ACCOUNT` | 自动识别 | 多账号机器上显式指定微信账号目录名 |
| `--db-dir PATH` | 自动检测 | 显式指定微信数据根目录 |
| `--session-limit N` | `10000` | 最多检查多少个最近会话 |
| `--skip-media` | 关闭 | 不恢复本地媒体，只导出文本、消息元数据和网页 URL |

### 5.1 导出最近 3 天

```powershell
conda run -n wechat-export python script\extract_data.py --days 3
```

### 5.2 导出最近 12 小时

```powershell
conda run -n wechat-export python script\extract_data.py --days 0.5
```

### 5.3 指定输出目录

```powershell
conda run -n wechat-export python script\extract_data.py --output "D:\wechat_export"
```

### 5.4 只导出消息，不处理本地媒体

```powershell
conda run -n wechat-export python script\extract_data.py --skip-media
```

这种模式通常运行更快，并且仍会保留消息中可以解析出的网页 URL。

### 5.5 多账号或自动识别失败

如果电脑上存在多个微信账号目录，可以显式指定账号目录名：

```powershell
conda run -n wechat-export python script\extract_data.py `
  --db-dir "C:\Users\<用户名>\xwechat_files" `
  --account "wxid_example_abcd"
```

`--account` 应传入 `xwechat_files` 下包含 `db_storage` 的完整目录名，而不一定只是微信号本身。

## 6. 输出格式

### 6.1 每个会话 JSON 的顶层结构

每个 JSON 文件代表一个联系人或群聊，使用 UTF-8 编码和两空格缩进，便于人工阅读。

```json
{
  "schema_version": 1,
  "chat_name": "项目讨论群",
  "chat_username": "12345678901@chatroom",
  "chat_type": "group",
  "account": {
    "username": "wxid_current_user",
    "nickname": "当前用户"
  },
  "range": {
    "start": "2026-09-17T18:16:47+08:00",
    "end": "2026-09-24T18:16:47+08:00",
    "days": 7.0
  },
  "exported_at": "2026-09-24T18:18:02+08:00",
  "message_count": 2,
  "messages": []
}
```

主要字段：

| 字段 | 含义 |
|---|---|
| `schema_version` | 导出 JSON 的结构版本 |
| `chat_name` | 联系人备注/昵称或群聊名称 |
| `chat_username` | 微信内部会话标识；群聊通常以 `@chatroom` 结尾 |
| `chat_type` | `contact` 表示联系人，`group` 表示群聊 |
| `account` | 执行导出的当前微信账号信息 |
| `range` | 本次导出的时间范围 |
| `message_count` | 当前文件中的消息数量 |
| `messages` | 按时间升序排列的消息数组 |

### 6.2 文本消息示例

```json
{
  "time": "2026-09-24T10:30:15+08:00",
  "timestamp": 1790217015,
  "sender": "张三",
  "sender_username": "wxid_example_sender",
  "type": "文本",
  "type_code": 1,
  "content": "下午三点开会。",
  "assets": [],
  "message_id": {
    "server_id": 1234567890123456789,
    "sort_seq": 1790217015000,
    "local_id": 168
  }
}
```

- `time` 为带时区的 ISO 8601 时间，方便人工阅读；
- `timestamp` 为 Unix 秒级时间戳，方便程序排序和筛选；
- 当前账号发送的消息，其 `sender` 为 `我`；
- 群聊消息会尽量解析实际群成员的昵称和内部用户名；
- `message_id` 保存微信数据库中的消息标识，方便去重或增量处理。

### 6.3 网页链接或卡片消息示例

```json
{
  "time": "2026-09-24T11:20:00+08:00",
  "timestamp": 1790220000,
  "sender": "李四",
  "sender_username": "wxid_example_member",
  "type": "文件/链接/卡片",
  "type_code": 49,
  "content": "[链接/卡片] 示例文章 — 文章摘要",
  "assets": [
    {
      "kind": "url",
      "url": "https://example.com/article",
      "href": "https://example.com/article",
      "available": true
    }
  ],
  "message_id": {
    "server_id": 1234567890123456790,
    "sort_seq": 1790220000000,
    "local_id": 169
  },
  "details": {
    "title": "示例文章",
    "description": "文章摘要",
    "app_message_type": "5"
  }
}
```

### 6.4 已恢复本地附件示例

```json
{
  "type": "文件/链接/卡片",
  "content": "[文件] 实验报告.pdf",
  "assets": [
    {
      "kind": "file",
      "path": "C:\\path\\to\\message_repo\\data\\assets\\项目讨论群\\1790220300_123_170_实验报告.pdf",
      "relative_path": "assets/项目讨论群/1790220300_123_170_实验报告.pdf",
      "href": "file:///C:/path/to/message_repo/data/assets/%E9%A1%B9%E7%9B%AE%E8%AE%A8%E8%AE%BA%E7%BE%A4/1790220300_123_170_%E5%AE%9E%E9%AA%8C%E6%8A%A5%E5%91%8A.pdf",
      "available": true,
      "original_name": "实验报告.pdf"
    }
  ]
}
```

其中：

- `path` 是绝对系统路径；
- `relative_path` 是相对于输出目录的路径，适合移动数据目录后使用；
- `href` 是可作为超链接使用的 `file://` URI；
- `available` 表示生成 JSON 时该文件是否存在。

### 6.5 图片只有微信加密缓存时的示例

部分图片在本机只有微信加密缓存，且运行时没有可用图片密钥。这时程序不会阻塞等待，而是保存缓存地址并给出说明：

```json
{
  "type": "图片",
  "content": "[图片]",
  "assets": [
    {
      "kind": "wechat_encrypted_image_cache",
      "path": "C:\\Users\\user\\xwechat_files\\...\\Img\\abcdef_t.dat",
      "relative_path": null,
      "href": "file:///C:/Users/user/xwechat_files/.../Img/abcdef_t.dat",
      "available": true,
      "encrypted": true,
      "md5": "0123456789abcdef0123456789abcdef"
    }
  ],
  "media_warning": "未找到已缓存的图片密钥，已保留加密缓存地址"
}
```

`media_warning` 不表示整次导出失败，只表示该条消息没有恢复出可直接查看的完整明文媒体文件。

## 7. 消息类型处理

程序能够识别并结构化处理常见微信消息类型：

- 文本；
- 图片；
- 语音；
- 视频；
- 文件、网页链接和卡片；
- 动画表情；
- 位置；
- 音视频通话记录；
- 红包提示；
- 系统消息；
- 其它微信复合消息类型。

对于不能直接表示为普通文字的内容，程序会优先采用以下顺序保存信息：

1. 恢复后的本地文件；
2. 微信原始本地缓存路径；
3. 消息中携带的 HTTP/HTTPS URL；
4. 类型占位文字及 `media_warning`。

微信只会在本地保存已经下载过的部分媒体。原图、视频、文件或语音如果从未在客户端中下载、已经被微信清理，或对应密钥不在内存/缓存中，程序无法保证恢复其明文内容。

## 8. 自动化处理示例

### 8.1 使用 Python 读取全部文本消息

```python
import json
from pathlib import Path

for path in Path("data").glob("*.json"):
    with path.open("r", encoding="utf-8") as file:
        chat = json.load(file)

    for message in chat["messages"]:
        if message["type"] == "文本":
            print(
                chat["chat_name"],
                message["time"],
                message["sender"],
                message["content"],
            )
```

### 8.2 统计每个会话消息数

```python
import json
from pathlib import Path

statistics = []
for path in Path("data").glob("*.json"):
    with path.open("r", encoding="utf-8") as file:
        chat = json.load(file)
    statistics.append((chat["message_count"], chat["chat_name"]))

for count, name in sorted(statistics, reverse=True):
    print(f"{name}: {count}")
```

## 9. 常见问题

### 9.1 `未检测到 Weixin.exe，请先登录微信再运行`

请检查：

1. 微信 Windows 客户端是否正在运行；
2. 微信是否已经登录，而不是停留在登录二维码页面；
3. 是否使用了 64 位 Python；
4. 当前终端是否有权读取微信进程。

可以检查微信进程：

```powershell
Get-Process Weixin -ErrorAction SilentlyContinue
```

### 9.2 `Access denied`、`拒绝访问` 或无法读取进程内存

这通常是微信与 Python 权限级别不一致造成的。如果微信以管理员身份运行，请用管理员身份打开 PowerShell/Anaconda Prompt 后再次执行脚本。

不建议为解决此问题关闭系统安全软件或降低系统安全设置。

### 9.3 自动选择了错误的微信账号

使用 `--db-dir` 和 `--account` 显式指定账号目录。账号目录通常位于：

```text
C:\Users\<用户名>\xwechat_files\<账号目录名>\
```

### 9.4 为什么某些公众号/服务会话没有生成 JSON？

会话列表的更新时间并不一定意味着消息数据库中存在该时间范围内的可导出聊天消息。程序会再次按消息时间精确查询；没有实际消息的会话会按要求忽略。

### 9.5 为什么部分图片只有 `.dat` 地址？

微信 4.x 会加密保存图片缓存。只有在能够获得有效图片密钥时，程序才能恢复出 JPG、PNG、GIF 等格式；否则会保留 `.dat` 的系统路径和 `file://` URI。

### 9.6 为什么部分文件或视频没有本地路径？

如果附件没有在当前电脑下载过，或已被微信清理，本地文件将不存在。程序会保留消息中的网页 URL；如果两者都不存在，则使用 `media_warning` 说明原因。

### 9.7 导出结果是否包含撤回前的消息？

本程序读取当前本地数据库中仍然存在的内容，不保证恢复已经被微信彻底删除、清理或未曾落入本地数据库的消息。

## 10. 数据安全与隐私

导出内容可能包含私人聊天、联系人信息、群成员信息、文件系统路径和具有临时访问能力的资源 URL。请注意：

- 不要将整个 `data` 目录提交到公开 Git 仓库；
- 不要在未获得相关人员授权的情况下传播导出数据；
- 分享 JSON 前应检查并清理姓名、微信内部标识、文件路径和 URL；
- `data/assets` 中的文件应按敏感个人数据管理；
- 系统临时目录中的解密数据库缓存也可能包含敏感信息，应遵守本机数据管理要求。

本程序仅应用于用户有权访问和处理的数据，并应遵守适用的法律法规、组织政策及微信软件许可协议。
