"""Run B2B production API health checks and report results to Slack.

Flow: generate token -> call every API in apis.yaml -> post a summary to Slack.
Secrets are never stored in apis.yaml; it references them as ${ENV_VAR}.
The generated token is referenced as {{token}}.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

CONFIG_PATH = Path(__file__).parent / "apis.yaml"
RESPONSES_DIR = Path(__file__).parent / "responses"
IST = timezone(timedelta(hours=5, minutes=30))
TIMEOUT_SECONDS = 60
SNIPPET_CHARS = 300

ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def load_dotenv():
    """Load .env for local runs. In GitHub Actions, secrets come from the environment."""
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def substitute(value, token=None):
    """Recursively replace ${ENV_VAR} and {{token}} in strings, dicts and lists."""
    if isinstance(value, dict):
        return {k: substitute(v, token) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, token) for v in value]
    if not isinstance(value, str):
        return value

    def env_lookup(match):
        name = match.group(1)
        if name not in os.environ:
            raise KeyError(f"Missing environment variable / secret: {name}")
        return os.environ[name]

    value = ENV_PATTERN.sub(env_lookup, value)
    if token is not None:
        value = value.replace("{{token}}", token)
    return value


def extract(data, path):
    """Get a nested value using a dotted path, e.g. 'data.access_token' or 'items.0.id'."""
    for part in path.split("."):
        data = data[int(part)] if isinstance(data, list) else data[part]
    return data


def send_request(spec, token=None):
    spec = substitute(spec, token)
    kwargs = {
        "method": spec.get("method", "GET").upper(),
        "url": spec["url"],
        "headers": spec.get("headers", {}),
        "params": spec.get("params"),
        "timeout": TIMEOUT_SECONDS,
    }
    if "json" in spec:
        kwargs["json"] = spec["json"]
    elif "form" in spec:
        kwargs["data"] = spec["form"]
    elif "body" in spec:
        kwargs["data"] = spec["body"]

    start = time.monotonic()
    response = requests.request(**kwargs)
    elapsed_ms = int((time.monotonic() - start) * 1000)
    return response, elapsed_ms


def generate_token(token_spec):
    response, _ = send_request(token_spec)
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:SNIPPET_CHARS]}")
    return extract(response.json(), token_spec["token_path"])


def is_success(status, api):
    expected = api.get("expected_status", [200])
    if isinstance(expected, int):
        expected = [expected]
    return status in expected


def save_response(name, response):
    RESPONSES_DIR.mkdir(exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", name)
    (RESPONSES_DIR / f"{safe_name}.txt").write_text(
        f"HTTP {response.status_code}\n\n{response.text}"
    )


def run_api(api, token):
    name = api["name"]
    try:
        response, elapsed_ms = send_request(api["request"], token)
    except Exception as exc:  # network error, timeout, missing secret, etc.
        return {"name": name, "ok": False, "status": None, "ms": None, "detail": str(exc)}

    save_response(name, response)
    ok = is_success(response.status_code, api)
    return {
        "name": name,
        "ok": ok,
        "status": response.status_code,
        "reason": response.reason,
        "ms": elapsed_ms,
        "detail": None if ok else response.text[:SNIPPET_CHARS],
    }


def format_duration(ms):
    return f"{ms} ms" if ms < 1000 else f"{ms / 1000:.1f} s"


def build_message(results, run_time, token_error=None):
    header = f"*B2B Prod API Check — {run_time:%d %b %Y, %I:%M %p} IST*"
    run_url = os.environ.get("RUN_URL")

    if token_error:
        lines = [header, f":rotating_light: *Token generation failed* — no APIs were called.",
                 f"```{token_error}```"]
    else:
        lines = [header]
        for r in results:
            icon = ":white_check_mark:" if r["ok"] else ":x:"
            if r["status"] is None:
                lines.append(f"{icon} *{r['name']}* – request error")
            else:
                lines.append(f"{icon} *{r['name']}* – {r['status']} {r['reason']} "
                             f"({format_duration(r['ms'])})")
            if r["detail"]:
                lines.append(f"```{r['detail']}```")
        passed = sum(r["ok"] for r in results)
        lines.append(f"*Summary: {passed}/{len(results)} passed*")

    if run_url:
        lines.append(f"<{run_url}|View run logs>")
    return "\n".join(lines)


def notify_slack(text):
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook:
        print("SLACK_WEBHOOK_URL not set; skipping Slack notification.")
        return
    response = requests.post(webhook, json={"text": text}, timeout=TIMEOUT_SECONDS)
    if not response.ok:
        print(f"Slack notification failed: HTTP {response.status_code} {response.text}")


def main():
    load_dotenv()
    config = yaml.safe_load(CONFIG_PATH.read_text())
    run_time = datetime.now(IST)

    try:
        token = generate_token(config["token"])
    except Exception as exc:
        message = build_message([], run_time, token_error=str(exc))
        print(message)
        notify_slack(message)
        return 1

    results = [run_api(api, token) for api in config["apis"]]
    message = build_message(results, run_time)
    print(message)
    notify_slack(message)
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
