# 阶段 B：可替换 Agent 调用层与离线入口

关联 [TXT-02 / #4](https://github.com/Craneplayz/niuma-manager/issues/4)。阶段 A 的 [PR #11](https://github.com/Craneplayz/niuma-manager/pull/11) 尚未合并时，本分支暂以其提交为基础；本阶段不代表真实模型已接通。

阶段 B 已随 PR #12 合并。新增的 skill、草稿处理与日期代码见[阶段 C 文件说明](text-stage-c-extraction.md)；下面保留原有骨架的使用方式。

## 文件用途速查

以下路径均相对于仓库根目录。第一次阅读建议先看本文件，再看输入输出约定，最后看 `pipeline.py`。

| 文件 | 用途 |
| --- | --- |
| `ddl_text/__init__.py` | 标记文本模块为 Python 包。 |
| `ddl_text/__main__.py` | 命令行入口：读取文件或标准输入，选择演示、配置与输出格式。 |
| `ddl_text/config.py` | 集中定义和检查配置，例如超时时间、重试次数及预留模型参数。 |
| `ddl_text/adapters.py` | 定义 Agent 调用接口、离线样例回放和未接入模型时的明确报错。后续真实模型从这里的接口接入。 |
| `ddl_text/contracts.py` | 补齐输入的未知字段，检查输入格式及候选任务的结构、来源和证据。 |
| `ddl_text/pipeline.py` | 整体处理流程：逐条处理、决定是否重试、汇总任务与失败结果。 |
| `ddl_text/runner.py` | 在独立子进程中调用适配器，处理超时、进程回收及安全的错误返回。 |
| `ddl_text/render.py` | 将内部结果转换为中文说明，包括任务、日期、来源和待核实原因。 |
| `tests/__init__.py` | 标记测试目录为 Python 包。 |
| `tests/fake_adapters.py` | 测试用适配器，用于模拟失败、超时等情况，不是真实模型。 |
| `tests/test_pipeline.py` | 自动测试批量处理、重试、超时、格式校验和命令行入口等行为。 |
| `scripts/check_text_contract_examples.py` | 检查阶段 A 的合成输入和期望输出是否符合样例约定。 |
| `docs/examples/text-contract-v0.1.json` | 合成输入及人工期望结果，供演示和测试使用。 |
| `docs/examples/text-runner-config.json` | 配置文件示例。 |
| `docs/text-contract-v0.1.md` | 各个输入输出字段、日期和不确定性的详细约定。 |
| `.gitignore` | 排除缓存、环境文件和本地私人测试数据，避免误提交。 |
| `README.md` | 项目入口和其他需求、计划、交付文档的导航。 |

简单理解调用流程：命令行入口 → `pipeline` 汇总处理 → `runner` 调用 `adapter` → 检查结果 → 输出 JSON 或由 `render` 转为中文文本。

## 运行方式

仓库根目录执行，要求 Python 3.9 或更新版本。当前机器以 Windows / Python 3.9.12 验证。仅使用标准库，无需安装第三方依赖或配置密钥。本选择用于独立文本模块，不要求团队其他部分采用 Python；传输协议、运行服务器仍可调整。

```text
python -m ddl_text --demo absolute
python -m ddl_text --demo multiple --format json
python -m ddl_text --demo no-date
python -m ddl_text --demo partial-batch
python -m ddl_text --demo agent-failure
python -m ddl_text --demo multiple --config docs/examples/text-runner-config.json --audit
```

`--demo` 使用阶段 A 的合成输入和人工期望结果，明确显示“离线样例回放（非模型识别）”。这验证处理通路，不验证自然语言识别能力。unknown case_id 会报参数错误，不静默换用其他样例。

普通输入支持 UTF-8 JSON 文件或标准输入：

```text
python -m ddl_text --input local-data/request.json
python -m ddl_text --input - --format json
python -m ddl_text --input local-data/request.json --mode real
```

不提供回放或真实适配器时，有效非空输入会明确返回 agent_unavailable；不会假装识别成功。最小请求如下，未提供元数据将补成明确的未知值：

```json
{
  "contract_version": "0.1",
  "request_id": "local-request-1",
  "records": [{"input_id": "local:1", "text": "请提交示例报告。"}]
}
```

若要回放自己的合成数据，使用 `--replay 文件路径`，文件结构与 `docs/examples/text-contract-v0.1.json` 相同。请求的规范化记录必须与回放原始记录完全匹配；只复用 ID、正文或元数据不同的记录不会返回旧答案。真实数据留在被忽略的 local-data 目录或仓库之外；不要把自己的私人数据写入演示 fixture。

当前默认输出 JSON，版本跟随请求（本页历史样例为 v0.1，前端使用 v0.2）；运行模式写入标准错误，便于调用方解析。需要人工阅读时加 `--format text`。成功/无任务退出码为 0，处理失败/部分成功为 1，文件、JSON 或配置错误为 2。语法损坏的 JSON 无法恢复请求标识，故只输出安全的命令行错误，不伪造契约结果。新增 HTTP 入口见[前端对接说明](text-frontend-integration.md)。

## 可替换边界

`AgentAdapter.extract(record, settings, attempt)` 返回候选任务列表，或抛出带类别的 `AgentError`。attempt 从 0 开始。处理器只依赖这个接口，不依赖回放文件、DeepSeek SDK、HTTP 框架或 Codex 配置。适配器必须是可被 multiprocessing spawn 导入和序列化的可信 Python 对象，不能定义为局部函数或 lambda。

业务调用入口为 `ddl_text.pipeline.process_request(request, adapter, settings)`；返回结构化对象。文本呈现由 `ddl_text.render.render_text` 独立完成。输入请求不被修改，调用之间不保存上下文。调用方必须先按用户授权范围隔离输入；接口本身不提供账号认证或租户鉴权。

在独立脚本中调用需放在 `if __name__ == "__main__":` 保护块内，以支持 Windows 的 spawn。入口模块已处理此要求。每个进程只收到一条输入及其提供的上下文；不要把其他用户信息放进适配器实例字段。

配置项集中在 `Settings`，示例见 [text-runner-config.json](examples/text-runner-config.json)：

- mode：offline/real，命令行必须显式选择，配置文件不能覆盖。
- model、endpoint：保留给未来实际适配器，不默认填写未经验证的型号或服务地址。
- credential_env：未来凭据环境变量的名称，不是密钥值。当前真实适配器尚未实现，不读取凭据或发送网络请求。
- timeout_seconds：每次尝试的时间预算，包含启动子进程时间；有限正数，最多 300 秒，默认 5 秒。
- max_retries：失败后的额外尝试次数，0–2，默认 0；总尝试次数至多 3。不可重试错误立即停止。

即使填写 model/endpoint，real 模式仍报不可用；配置存在不等于协议兼容。后续 #9 验证可信环境及真实协议后再替换适配器，不允许回退外部服务处理私人数据。

## 失败隔离与限制

- 每条输入顺序处理，每次调用使用独立子进程；真实超时会终止并回收该进程，继续后续记录。清理通常很快，但预算外最多保留两次 1 秒的回收等待；进程创建本身受操作系统调度影响，不能承诺硬实时。
- 空白正文/错误元数据为 invalid_input；非法请求、重复输入 ID 或超过 100 条为请求级失败。正文和每条上下文最多 200000 字符，上下文最多 100 条。
- 结构损坏、来源伪造、证据不匹配或重复候选 ID 导致整条记录失败；不把丢掉的候选伪装成“没有任务”。本次增加的是调用边界必要检查，完整语义校验仍属 #7。
- 子进程返回最多 1 MiB JSON；异常信息与子进程的普通输出不会被转发。可信适配器必须遵守协议；这不是恶意代码沙箱，也不保证终止未来适配器自行启动的外部进程树，实际 Codex 进程适配需要另行处理生命周期。
- 默认无审计日志；`--audit` 只输出模式、版本、结果类型、尝试次数及耗时，不输出输入正文、上下文、来源 URL、配置值或异常堆栈。
- 结果与用户主动要求的文本输出会包含必要证据和任务内容，应交还授权调用者；“不记录私人日志”不等于抹掉用户需要核对的结果。
- 没有 HTML 执行/抓取、附件解析、任务持久化、调度器或最终清单增删改。

## 检查与演示

```text
python -m unittest discover -s tests -v
python scripts/check_text_contract_examples.py
git diff --check
```

2026-09-23 在 Windows / Python 3.9.12 上，24 项自动检查全部通过，另通过阶段 A 的 17 个样例一致性检查。测试覆盖契约样例离线回放、适配器替换、真实超时后的进程回收与后续处理、有限重试、配置错误、损坏结果、跨请求输入不变、私有异常内容不泄露，以及文件/标准输入/文本/JSON 入口。离线回放测试在子进程中禁止创建网络 socket。没有真实模型准确率或跨操作系统已验证的承诺。

用于演示的 timeout 样例是错误回放；测试中的 `TimeoutFirstAdapter` 才会真正阻塞并触发终止。两类验证结果必须区分。

阶段 C 实现识别规则与 skills，阶段 D 完成实际链路和更全面的语义校验；本阶段可以让队友无密钥运行合成示例，并对照状态与错误约定开始对接。
