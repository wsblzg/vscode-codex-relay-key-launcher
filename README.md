# vscode-codex-relay-key-launcher

这个项目提供一个 Windows 启动器，让不同 VSCode 窗口使用不同的 Codex key。它适合账号池里有多个 key 的场景：每个窗口启动时绑定一个独立 profile，避免 key 用完后必须关闭会话、重启 VSCode、手动改配置。

## 功能

- 图形界面可选择任意 `accounts.json` 路径。
- 支持勾选一个或多个账号，一次打开多个 VSCode 窗口。
- 每个账号使用独立 `CODEX_HOME`，不同窗口可以绑定不同 key。
- 可按 `provider` 共享 Codex 会话状态，减少多窗口之间的历史记录割裂。
- 支持多线程测试 key 状态，区分 `可用`、`仅 Claude Code 可用`、`401 不可用`、`额度或限速`、`网络失败` 等结果。
- 支持批量修改账号 `provider` 字段。
- 支持全选、只选择可用 key、永久删除选中 key。
- 生成 profile 时默认写入 Codex Full Access 配置。

## 安全说明

不要把真实账号池或生成后的 Codex profile 提交到仓库。项目根目录的 `.gitignore` 已排除常见敏感文件和本地构建目录，包括：

- `accounts.json`
- `auth.json`
- `config.toml`
- `.venv/`
- `build/`
- `dist/`
- `.codex-evolution/`
- `.learnings/`

核心思路：

- 每个账号一个独立 `CODEX_HOME`。
- 每个 VSCode 窗口一个独立 `--user-data-dir`。
- 窗口启动时就读取自己的 `auth.json` 和 `config.toml`，避免多个窗口争用 `C:\Users\DAS\.codex`。
- 可以按 `provider` 共享 Codex 会话状态，让同一供应商下的不同 key 继承会话记录。
- 每个 profile 默认写入 `sandbox_mode = "danger-full-access"`，进入 Codex 后默认是 Full Access。

## 常用命令

启动图形界面：

```powershell
uv venv
uv run python launch_ui.py
```

也可以直接双击：

```text
启动界面.cmd
```

已打包的 exe 位于：

```text
dist\CodexRelayKeyLauncher.exe
```

重新打包：

```powershell
uv run pyinstaller --clean --noconfirm CodexRelayKeyLauncher.spec
```

图形界面启动后，在“账号池”输入框选择或填写 `accounts.json` 路径，勾选一个或多个账号后点击“打开选中窗口”，每个账号会打开一个独立 `CODEX_HOME` 的 VSCode 窗口。

“供应商”输入框默认显示当前账号池的统一 `provider`。修改后点击“应用到全部”，会把 `accounts.json` 里所有账号的 `provider` 一次性改成该值。供应商名称会被规范成 ASCII 安全格式，只保留字母、数字、下划线、点和短横线。

## accounts.json 格式

`accounts.json` 可以放在任意目录，启动器只需要你在界面里选择这个文件。文件使用 UTF-8 JSON，顶层结构如下：

```json
{
  "preferred": "api-freemodel-dev-1",
  "accounts": [
    {
      "name": "api-freemodel-dev-1",
      "provider": "rightcode",
      "baseUrl": "https://api.example.com",
      "apiKey": "YOUR_API_KEY",
      "model": "gpt-5.5"
    }
  ]
}
```

字段说明：

- `preferred`：可选，默认优先使用的账号名，必须对应某个 `accounts[].name`。
- `accounts`：必填，账号数组。
- `name`：必填，账号显示名和 profile 目录名，建议只使用字母、数字、短横线和下划线。
- `provider`：可选，供应商名称。为空时默认使用 `rightcode`，会写入每个 profile 的 `config.toml`。
- `baseUrl`：必填，兼容 OpenAI Responses API 的接口根地址，不要在末尾写 `/responses`。
- `apiKey`：必填，对应该账号使用的 key。
- `model`：可选，测试 key 时使用的模型名；为空时使用启动器内置默认测试模型。

不要把包含真实 `apiKey` 的 `accounts.json` 上传到 GitHub。

默认勾选“同 provider 共享 Codex 会话状态”。这会保留每个账号自己的 `auth.json` 和 `config.toml`，但让同一个 `provider` 下的账号共享 `sessions`、`session_index.jsonl`、`history.jsonl`、`memories`、`rules`、`prompts`、`skills` 等会话状态。

表格第一列表头有全选复选框，会选中或取消选中当前筛选出来的账号。每行都有“测试”按钮，可以用该账号的 key 访问 `baseUrl/responses` 做一次轻量测试。顶部“测试选中”会用多线程批量测试已勾选账号；测试完成后点“选择可用”，会只保留状态为 `可用` 的账号勾选。状态列会显示 `可用`、`仅 Claude Code 可用`、`401 不可用`、`额度或限速`、`网络失败`、`超时` 等结果。

点击“删除选中”会先弹出确认框。确认后会从 `accounts.json` 永久删除选中的账号，并删除对应 `C:\Users\DAS\.codex-profiles\<account-name>\auth.json`，避免已生成 profile 里残留 key。

账号池中的 `provider` 字段用于启动器和图形界面展示，当前统一为 `rightcode`。Codex 实际生效的供应商配置写入每个 profile 的 `config.toml`：

```toml
model_provider = "rightcode"

[model_providers.rightcode]
name = "rightcode"
```

每个账号 profile 还会默认写入：

```toml
sandbox_mode = "danger-full-access"
approval_policy = "never"
```

这个沙盒权限不是共享状态，也不需要每次手动更新。它是写在每个账号自己的 `config.toml` 里的固定配置；启动器每次生成或刷新 profile 时都会统一保证这些字段存在。

不要再使用旧的：

```toml
[windows]
sandbox = "elevated"
```

这个旧字段会触发 Windows admin sandbox 更新路径，可能出现 `Couldn't update admin sandbox`。当前工具会移除这个旧字段，改用 Codex CLI 识别的 Full Access 配置。

列出账号池：

```powershell
.\Start-CodexRelayVSCode.ps1 -AccountsPath "D:\path\to\accounts.json" -List
```

用当前 preferred 账号打开 `D:\项目`：

```powershell
.\Start-CodexRelayVSCode.ps1 -AccountsPath "D:\path\to\accounts.json" -Workspace "D:\项目"
```

用当前 preferred 账号打开，并按 `provider` 共享会话状态：

```powershell
.\Start-CodexRelayVSCode.ps1 -AccountsPath "D:\path\to\accounts.json" -Workspace "D:\项目" -ShareStateByProvider
```

指定账号打开：

```powershell
.\Start-CodexRelayVSCode.ps1 -AccountsPath "D:\path\to\accounts.json" -Account api-freemodel-dev-17 -Workspace "D:\项目"
```

用 preferred 的下一个账号打开：

```powershell
.\Start-CodexRelayVSCode.ps1 -AccountsPath "D:\path\to\accounts.json" -Next -Workspace "D:\项目"
```

一次打开多个账号窗口：

```powershell
.\Start-CodexRelayVSCode.ps1 -AccountsPath "D:\path\to\accounts.json" -Accounts api-freemodel-dev-17,api-freemodel-dev-18,api-freemodel-dev-19 -Workspace "D:\项目"
```

查看已生成的独立 Codex profile：

```powershell
.\Show-CodexRelayProfiles.ps1
```

只预生成 profile，不打开 VSCode：

```powershell
.\Start-CodexRelayVSCode.ps1 -AccountsPath "D:\path\to\accounts.json" -Account api-freemodel-dev-17 -PrepareOnly
```

## 生成位置

账号 profile 默认生成在：

```text
C:\Users\DAS\.codex-profiles\<account-name>
```

VSCode user-data 默认生成在：

```text
C:\Users\DAS\AppData\Roaming\Code-CodexRelay\<account-name>
```

按 `provider` 共享的 Codex 状态默认生成在：

```text
C:\Users\DAS\.codex-provider-state\<provider>
```

每个账号 profile 都会有自己的：

```text
auth.json
config.toml
```

启用 `-ShareStateByProvider` 后，账号 profile 中的共享状态会链接到 provider 状态目录。首次启用时，如果账号 profile 里已经有本地状态，脚本会先移动到：

```text
.local-state-before-provider-share\<timestamp>
```

再建立共享链接。

## 注意

这个方案不是运行时热切 key。它是多 VSCode 窗口隔离：

- 窗口 A 使用 `api-freemodel-dev-17`
- 窗口 B 使用 `api-freemodel-dev-18`
- 窗口 C 使用 `api-freemodel-dev-19`

每个窗口启动时读取自己的 `CODEX_HOME`。如果启用了 `-ShareStateByProvider`，同一个 `provider` 下的窗口会共享 Codex 会话状态；如果不启用，则仍是完全独立的 profile 状态。

默认不隔离 VSCode extensions 目录，这样能复用你已经安装的 OpenAI Codex 插件。如果你需要每个窗口也隔离扩展目录，可以加：

```powershell
.\Start-CodexRelayVSCode.ps1 -AccountsPath "D:\path\to\accounts.json" -Account api-freemodel-dev-17 -UseSeparateExtensionsDir
```

## 测试

运行 Python 单元测试：

```powershell
uv run python -m unittest tests.test_key_status
```

## 推荐上传内容

上传到 GitHub 时建议保留源码、测试、说明文档和打包 spec：

- `launch_ui.py`
- `Start-CodexRelayVSCode.ps1`
- `Show-CodexRelayProfiles.ps1`
- `CodexRelayKeyLauncher.spec`
- `pyproject.toml`
- `uv.lock`
- `tests/`
- `README.md`
- `.gitignore`
