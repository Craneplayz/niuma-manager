"""Codex CLI transport; credentials stay in the child environment, never argv."""

import json
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .adapters import AgentError
from .draft_schema import draft_schema


ROOT = Path(__file__).resolve().parents[1]
ENV_KEYS = {"DEEPSEEK_API_KEY", "QINIU_API_KEY", "DDL_PROVIDER", "DDL_MODEL", "DDL_ENDPOINT", "DDL_CODEX_BIN"}
# The frontend backend has its own shell configuration. These sibling keys may
# coexist in a merged .env example but are never provider settings or prompts.
SIBLING_ENV_KEYS = {"HARNESS_URL", "HARNESS_TOKEN", "TEXT_HARNESS_URL", "TEXT_HARNESS_TOKEN", "NIUMA_DB"}


def load_env(path):
    """Small literal dotenv subset: no expansion, execution or environment writes."""
    values = {}
    seen = set()
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or key not in ENV_KEYS | SIBLING_ENV_KEYS or key in seen:
            raise ValueError("invalid dotenv")
        seen.add(key)
        if value[:1] in {"'", '"'}:
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError("invalid dotenv")
            value = value[1:-1]
        if key in ENV_KEYS:
            values[key] = value
    return values


def live_options(env_path):
    values = load_env(env_path)
    # Explicit environment values take precedence, including an empty value.
    values.update({k: os.environ[k] for k in ENV_KEYS if k in os.environ})
    provider = values.get("DDL_PROVIDER") or "deepseek"
    if provider not in {"deepseek", "qiniu"}:
        raise ValueError("unsupported provider")
    qiniu = provider == "qiniu"
    credential_env = "QINIU_API_KEY" if qiniu else "DEEPSEEK_API_KEY"
    endpoint = values.get("DDL_ENDPOINT") or ("https://api.qnaigc.com/v1" if qiniu else "https://api.deepseek.com")
    validate_endpoint(endpoint)
    validate_provider_endpoint(provider, endpoint)
    return {
        "provider": provider,
        "credential_env": credential_env,
        "key": values.get(credential_env, ""),
        "model": values.get("DDL_MODEL") or ("deepseek/deepseek-v4.1-flash" if qiniu else "deepseek-flash"),
        "endpoint": endpoint,
        "executable": values.get("DDL_CODEX_BIN") or "codex",
    }


def validate_provider_endpoint(provider, endpoint):
    # Catch stale first-party URLs when switching providers, before reading a key.
    host = urlsplit(endpoint).hostname
    if (provider == "qiniu" and host == "api.deepseek.com") or (provider == "deepseek" and host == "api.qnaigc.com"):
        raise ValueError("provider and endpoint disagree")
    if provider == "qiniu" and urlsplit(endpoint).path.rstrip("/") != "/v1":
        raise ValueError("Qiniu chat endpoint must end in /v1")


def validate_endpoint(endpoint):
    url = urlsplit(endpoint)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("endpoint must be a credential-free HTTPS URL")


def find_codex(executable):
    found = shutil.which(executable)
    if not found or (os.name == "nt" and Path(found).suffix.lower() != ".exe"):
        raise ValueError("Codex executable not found; Windows requires codex.exe")
    return str(Path(found).resolve())


def build_config(instruction_file, model, endpoint):
    # JSON string literals are also TOML basic strings; this is not shell text.
    q = lambda value: json.dumps(str(value), ensure_ascii=False)
    return "\n".join([
        "model = " + q(model), 'model_provider = "ddl_deepseek"',
        'model_reasoning_effort = "low"', 'model_reasoning_summary = "none"',
        "model_instructions_file = " + q(instruction_file),
        'approval_policy = "never"', 'sandbox_mode = "read-only"',
        'web_search = "disabled"', 'project_doc_max_bytes = 0',
        '[history]', 'persistence = "none"',
        '[analytics]', 'enabled = false', '[feedback]', 'enabled = false',
        '[tools.experimental_request_user_input]', 'enabled = false',
        '[features]', *[name + " = false" for name in (
            "shell_tool", "unified_exec", "shell_snapshot", "apply_patch_freeform",
            "multi_agent", "apps", "plugins", "remote_plugin", "hooks", "memories",
            "view_image", "browser_use", "computer_use", "skill_mcp_dependency_install",
            "goals", "default_mode_request_user_input", "collaboration_modes")],
        '[model_providers.ddl_deepseek]', 'name = "DDL DeepSeek"',
        'base_url = ' + q(endpoint), 'wire_api = "responses"',
        'env_key = "DEEPSEEK_API_KEY"', 'requires_openai_auth = false',
        'request_max_retries = 0', 'stream_max_retries = 0',
        'stream_idle_timeout_ms = 60000',
    ]) + "\n"


def stop_process(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    else:
        os.killpg(process.pid, signal.SIGKILL)
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)


class CodexClient:
    def __init__(self, executable, key):
        self.executable = executable
        self._key = key

    def generate(self, instructions, record_json, settings, attempt):
        if not self._key.strip():
            raise AgentError("agent_unavailable", False)
        # Fresh home and work directory exclude personal plugins, history and repo data.
        with tempfile.TemporaryDirectory(prefix="ddl-codex-") as directory:
            root = Path(directory)
            home, work = root / "home", root / "work"
            home.mkdir()
            work.mkdir()
            instruction_file, output = root / "instructions.md", root / "answer.json"
            schema_file = root / "draft-schema.json"
            instruction_file.write_text(instructions, encoding="utf-8")
            schema_file.write_text(json.dumps(draft_schema(json.loads(record_json)), ensure_ascii=False), encoding="utf-8")
            (home / "config.toml").write_text(build_config(instruction_file, settings.model, settings.endpoint), encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if k.upper() in {
                "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE",
                "LOCALAPPDATA", "APPDATA", "COMSPEC", "PATHEXT", "LANG", "SSL_CERT_FILE", "SSL_CERT_DIR"}}
            env.update(CODEX_HOME=str(home), DEEPSEEK_API_KEY=self._key, RUST_LOG="off")
            command = [self.executable, "exec", "--ephemeral", "--skip-git-repo-check",
                       "--color", "never", "--cd", str(work), "--output-last-message", str(output),
                       "--output-schema", str(schema_file), "-"]
            flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
            process = None
            try:
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, env=env, cwd=work, **flags)
                process.communicate(record_json.encode("utf-8"), timeout=settings.timeout_seconds)
                if process.returncode != 0:
                    raise AgentError("agent_unavailable", False)
                if not output.is_file() or output.stat().st_size > 1024 * 1024:
                    raise AgentError("invalid_agent_output", False)
                return output.read_text(encoding="utf-8")
            except subprocess.TimeoutExpired:
                raise AgentError("agent_timeout", True) from None
            except (OSError, UnicodeError):
                raise AgentError("agent_unavailable", False) from None
            finally:
                if process is not None:
                    stop_process(process)


def preflight(options):
    executable = find_codex(options["executable"])
    try:
        result = subprocess.run([executable, "exec", "--help"], capture_output=True,
                                timeout=10, encoding="utf-8", errors="replace",
                                **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
    except subprocess.TimeoutExpired:
        raise ValueError("Codex CLI check timed out") from None
    if result.returncode or not all(flag in result.stdout for flag in
                                    ("--ephemeral", "--output-last-message", "--skip-git-repo-check", "--output-schema")):
        raise ValueError("Codex CLI version is incompatible")
    return executable
