"""Capabilities — frozen dataclasses with compile_life.

Each contributes tools + messages to LifeContext via fold.

Core tools follow Claude Code's native tool signatures
(Bash, Read, Write, Edit) — the model already knows them.

Real-world capabilities:
  ShellAccess      — Bash, Read, Write, Edit (Claude Code native)
  GitHubAccess     — gh CLI tools (repos, releases, gists)
  EmailAccess      — SMTP/IMAP tools (send, read, search)
  BlockchainWallet — on-chain tools (balance, transactions, send, receive)
  BrowserAccess    — async Playwright tools (browse, screenshot, forms)
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from funcai.agents.tool import tool
from funcai.core.message import system

from typing import TYPE_CHECKING

from mmkr.state import LifeCapability, LifeContext

if TYPE_CHECKING:
    from playwright.async_api import AsyncPlaywright, Browser, BrowserContext, Page


# ═══════════════════════════════════════════════════════════════════════════════
# ShellAccess — Claude Code native tools (Bash, Read, Write, Edit)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class ShellAccess:
    """Claude Code native tools — Bash, Read, Write, Edit.

    The model already knows these tools from training.
    All async. Full filesystem + shell access.
    """

    def compile_life(self, ctx: LifeContext) -> LifeContext:

        @tool(
            "Run a shell command. Returns stdout, stderr, returncode. "
            "run_in_background=True for long-running processes. "
            "output_to_file='/path' to write stdout to file instead of inline "
            "(then use Read with offset/limit to inspect). "
            "Default timeout 30s."
        )
        async def Bash(  # noqa: N802 — matches Claude Code tool name
            command: str,
            timeout: int = 30,
            run_in_background: bool = False,
            output_to_file: str = "",
        ) -> dict[str, str | int]:
            import asyncio
            import os
            import signal

            if run_in_background:
                proc = await asyncio.create_subprocess_shell(
                    f"nohup {command} > /tmp/_bg_stdout.log 2> /tmp/_bg_stderr.log &\n"
                    f"echo $!",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()
                pid = stdout.decode().strip()
                return {"stdout": f"Background process started (pid={pid})", "stderr": "", "returncode": 0}

            # Use process group so we can kill all children on timeout
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                preexec_fn=os.setsid,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout,
                )
            except asyncio.TimeoutError:
                # Kill entire process group (parent + all children)
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    proc.kill()
                # Brief wait for cleanup, don't hang
                try:
                    await asyncio.wait_for(proc.communicate(), timeout=2)
                except (asyncio.TimeoutError, ProcessLookupError):
                    pass
                return {"stdout": "", "stderr": f"timeout after {timeout}s", "returncode": -1}

            stdout_str = stdout.decode(errors="replace")
            stderr_str = stderr.decode(errors="replace")

            if output_to_file:
                import aiofiles
                os.makedirs(os.path.dirname(output_to_file) or ".", exist_ok=True)
                async with aiofiles.open(output_to_file, "w") as f:
                    await f.write(stdout_str)
                return {
                    "stdout": f"(written to {output_to_file}, {len(stdout_str)} chars — use Read to inspect)",
                    "stderr": stderr_str,
                    "returncode": proc.returncode or 0,
                }

            return {
                "stdout": stdout_str,
                "stderr": stderr_str,
                "returncode": proc.returncode or 0,
            }

        @tool(
            "Read file contents. offset=line number to start from, limit=number of lines. "
            "Returns numbered lines. Use offset/limit for large files."
        )
        async def Read(  # noqa: N802
            file_path: str,
            offset: int = 0,
            limit: int = 0,
        ) -> dict[str, str | int]:
            import aiofiles

            try:
                async with aiofiles.open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    content = await f.read()
            except FileNotFoundError:
                return {"error": f"file not found: {file_path}"}
            except PermissionError:
                return {"error": f"permission denied: {file_path}"}

            lines = content.splitlines(keepends=True)
            if offset > 0:
                lines = lines[offset:]
            if limit > 0:
                lines = lines[:limit]

            # Number lines like cat -n
            numbered = "".join(
                f"{i + offset + 1:>6}\t{line}"
                for i, line in enumerate(lines)
            )
            return {"content": numbered, "lines": len(lines)}

        @tool(
            "Write content to a file (creates or overwrites). Creates parent dirs. "
            "Do NOT use for entity .py files — use create_entity instead."
        )
        async def Write(  # noqa: N802
            file_path: str,
            content: str,
        ) -> dict[str, str | bool]:
            import os

            import aiofiles

            # Guard: detect entity definitions being written as raw .py files
            if file_path.endswith(".py") and "@dataclass" in content and "Annotated[" in content:
                return {
                    "error": "BLOCKED: This looks like an entity definition. "
                    "Use create_entity(name, source, domain) instead of Write. "
                    "create_entity verifies contracts, compiles via fold, and catches errors. "
                    "Writing .py files directly bypasses ALL verification.",
                    "written": False,
                }

            os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
            try:
                async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                    await f.write(content)
                return {"written": True, "path": file_path, "bytes": str(len(content.encode("utf-8")))}
            except PermissionError:
                return {"error": f"permission denied: {file_path}"}

        @tool("Replace old_string with new_string in a file. old_string must be unique in the file.")
        async def Edit(  # noqa: N802
            file_path: str,
            old_string: str,
            new_string: str,
        ) -> dict[str, str | bool]:
            import aiofiles

            try:
                async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                    content = await f.read()
            except FileNotFoundError:
                return {"error": f"file not found: {file_path}"}

            count = content.count(old_string)
            if count == 0:
                return {"error": "old_string not found in file"}
            if count > 1:
                return {"error": f"old_string found {count} times — must be unique"}

            new_content = content.replace(old_string, new_string, 1)
            async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                await f.write(new_content)
            return {"edited": True, "path": file_path}

        return replace(
            ctx,
            messages=(*ctx.messages,
                system(text=(
                    "You have Claude Code native tools: Bash (run commands), "
                    "Read (read files), Write (create/overwrite files), Edit (replace strings in files). "
                    "You are root in this container — full access."
                )),
            ),
            tools=(*ctx.tools, Bash, Read, Write, Edit),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AnthropicKey — just context
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class AnthropicKey:
    """Anthropic API key — the brain."""

    key: str

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        return replace(ctx, messages=(*ctx.messages,
            system(text="Anthropic API key available. Claude for reasoning, codegen, analysis, content."),
        ))


# ═══════════════════════════════════════════════════════════════════════════════
# GitHubAccess — real gh CLI tools
# ═══════════════════════════════════════════════════════════════════════════════


def _gh(token: str, args: list[str], *, timeout: int = 30) -> dict[str, str | int]:
    """Run gh CLI with token. Returns {stdout, stderr, returncode}."""
    import os
    import subprocess

    env = {**os.environ, "GH_TOKEN": token}
    try:
        proc = subprocess.run(
            ["gh", *args],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
        return {
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "returncode": proc.returncode,
        }
    except FileNotFoundError:
        return {"error": "gh CLI not installed", "returncode": 1}
    except subprocess.TimeoutExpired:
        return {"error": f"timeout after {timeout}s", "returncode": 1}


@dataclass(frozen=True, slots=True)
class GitHubAccess:
    """GitHub via gh CLI — real repos, releases, gists."""

    token: str
    username: str = ""

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        import os

        tok = self.token
        uname = self.username

        # Set GH_TOKEN in env so curl/gh/git all work
        os.environ["GH_TOKEN"] = tok

        @tool("Create a GitHub repository")
        def github_create_repo(
            name: str,
            description: str = "",
            private: bool = False,
        ) -> dict[str, str | int]:
            args = ["repo", "create", name, "--confirm"]
            if description:
                args.extend(["--description", description])
            if private:
                args.append("--private")
            else:
                args.append("--public")
            result = _gh(tok, args)
            if result.get("returncode") == 0:
                repo_full = f"{uname}/{name}" if uname else name
                return {"status": "created", "repo": repo_full, "url": f"https://github.com/{repo_full}"}
            return {"error": result.get("stderr") or result.get("error", "unknown error")}

        @tool("Push files to a GitHub repository (creates if needed)")
        def github_push_files(
            repo: str,
            files_content: dict[str, str],
            commit_message: str = "initial commit",
        ) -> dict[str, str | int]:
            import os
            import subprocess
            import tempfile

            if not files_content:
                return {"error": "files_content must not be empty"}

            with tempfile.TemporaryDirectory(prefix="mmkr-gh-") as tmpdir:
                for path, content in files_content.items():
                    full = os.path.join(tmpdir, path)
                    os.makedirs(os.path.dirname(full), exist_ok=True)
                    with open(full, "w") as f:
                        f.write(content)

                env = {**os.environ, "GH_TOKEN": tok}
                cmds = [
                    ["git", "init"],
                    ["git", "add", "."],
                    ["git", "commit", "-m", commit_message],
                ]
                for cmd in cmds:
                    r = subprocess.run(cmd, cwd=tmpdir, capture_output=True, text=True, timeout=30, env=env)
                    if r.returncode != 0:
                        return {"error": f"{' '.join(cmd)}: {r.stderr.strip()}"}

                r = subprocess.run(
                    ["gh", "repo", "create", repo, "--source", tmpdir, "--push", "--public"],
                    cwd=tmpdir, capture_output=True, text=True, timeout=60, env=env,
                )
                if r.returncode != 0:
                    repo_url = f"https://github.com/{repo}.git"
                    subprocess.run(
                        ["git", "remote", "add", "origin", repo_url],
                        cwd=tmpdir, capture_output=True, text=True, env=env,
                    )
                    r = subprocess.run(
                        ["git", "push", "-u", "origin", "main"],
                        cwd=tmpdir, capture_output=True, text=True, timeout=60, env=env,
                    )
                    if r.returncode != 0:
                        r = subprocess.run(
                            ["git", "push", "-u", "origin", "master"],
                            cwd=tmpdir, capture_output=True, text=True, timeout=60, env=env,
                        )
                    if r.returncode != 0:
                        return {"error": r.stderr.strip()}

            return {
                "status": "pushed", "repo": repo,
                "files": len(files_content), "url": f"https://github.com/{repo}",
            }

        @tool("Create a GitHub release with optional notes")
        def github_create_release(
            repo: str, tag: str, title: str = "", notes: str = "",
        ) -> dict[str, str | int]:
            args = ["release", "create", tag, "--repo", repo]
            if title:
                args.extend(["--title", title])
            if notes:
                args.extend(["--notes", notes])
            else:
                args.append("--generate-notes")
            result = _gh(tok, args, timeout=60)
            if result.get("returncode") == 0:
                return {"status": "released", "tag": tag, "repo": repo, "url": result.get("stdout", "")}
            return {"error": result.get("stderr") or result.get("error", "unknown error")}

        @tool("List GitHub repositories")
        def github_list_repos(limit: int = 30) -> dict[str, str | list[str]]:
            args = ["repo", "list", "--limit", str(limit)]
            if uname:
                args.insert(2, uname)
            result = _gh(tok, args)
            if result.get("returncode") == 0:
                stdout = str(result.get("stdout", ""))
                repos = [line.split("\t")[0] for line in stdout.splitlines() if line.strip()]
                return {"repos": repos, "count": str(len(repos))}
            return {"error": result.get("stderr") or result.get("error", "unknown error")}

        @tool("Create a GitHub Gist")
        def github_create_gist(
            description: str,
            files_content: dict[str, str],
            public: bool = True,
        ) -> dict[str, str]:
            import os
            import tempfile

            if not files_content:
                return {"error": "files_content must not be empty"}

            with tempfile.TemporaryDirectory(prefix="mmkr-gist-") as tmpdir:
                paths: list[str] = []
                for name, content in files_content.items():
                    p = os.path.join(tmpdir, name)
                    with open(p, "w") as f:
                        f.write(content)
                    paths.append(p)

                args = ["gist", "create", *paths, "--desc", description]
                if public:
                    args.append("--public")
                result = _gh(tok, args)
                if result.get("returncode") == 0:
                    return {"status": "created", "url": str(result.get("stdout", ""))}
                return {"error": result.get("stderr") or result.get("error", "unknown error")}

        @tool(
            "Call GitHub REST API via gh CLI. "
            "endpoint examples: repos/OWNER/REPO/issues, repos/OWNER/REPO/issues/1/comments, "
            "search/repositories?q=QUERY. "
            "output_to_file='/path' to write response to file instead of inline "
            "(then use Read with offset/limit for large responses)."
        )
        def github_api(
            endpoint: str, method: str = "GET", body: str = "",
            output_to_file: str = "",
        ) -> dict[str, str | int]:
            import os as _os
            import subprocess

            args = ["gh", "api", endpoint, "--method", method]
            env = {**_os.environ, "GH_TOKEN": tok}
            try:
                proc = subprocess.run(
                    args,
                    input=body if body else None,
                    capture_output=True, text=True, timeout=30, env=env,
                )
                stdout = proc.stdout.strip()
                if output_to_file and stdout:
                    _os.makedirs(_os.path.dirname(output_to_file) or ".", exist_ok=True)
                    with open(output_to_file, "w") as f:
                        f.write(stdout)
                    return {
                        "stdout": f"(written to {output_to_file}, {len(stdout)} chars — use Read to inspect)",
                        "stderr": proc.stderr.strip(),
                        "returncode": proc.returncode,
                    }
                return {
                    "stdout": stdout,
                    "stderr": proc.stderr.strip(),
                    "returncode": proc.returncode,
                }
            except subprocess.TimeoutExpired:
                return {"error": "timeout after 30s", "returncode": 1}

        return replace(
            ctx,
            messages=(*ctx.messages,
                system(text=(
                    f"GitHub (authenticated as {uname or 'unknown'}): "
                    "github_create_repo, github_push_files, github_create_release, "
                    "github_list_repos, github_create_gist, github_api. "
                    "Use github_api for comments, issues, PRs. NEVER browse() to login."
                )),
            ),
            tools=(*ctx.tools,
                github_create_repo, github_push_files,
                github_create_release, github_list_repos, github_create_gist,
                github_api,
            ),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# EmailAccess — SMTP/IMAP tools (any provider)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class EmailAccess:
    """Email via SMTP/IMAP — send, read, search. Works with any provider."""

    address: str
    password: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        addr = self.address
        pwd = self.password
        smtp_h = self.smtp_host
        smtp_p = self.smtp_port
        imap_h = self.imap_host
        imap_p = self.imap_port

        @tool("Send an email via SMTP")
        def email_send(to: str, subject: str, body: str) -> dict[str, str | bool]:
            if not pwd:
                return {"error": "email password not configured"}
            import smtplib
            from email.mime.text import MIMEText

            msg = MIMEText(body)
            msg["Subject"] = subject
            msg["From"] = addr
            msg["To"] = to
            try:
                with smtplib.SMTP(smtp_h, smtp_p, timeout=15) as server:
                    server.starttls()
                    server.login(addr, pwd)
                    server.send_message(msg)
                return {"sent": True, "to": to, "from": addr}
            except smtplib.SMTPException as e:
                return {"error": f"SMTP error: {e}"}

        @tool("Read recent emails from inbox via IMAP")
        def email_read_inbox(
            limit: int = 10, unread_only: bool = False,
        ) -> dict[str, str | list[dict[str, str]]]:
            if not pwd:
                return {"error": "email password not configured"}
            import email
            import email.header
            import imaplib

            try:
                with imaplib.IMAP4_SSL(imap_h, imap_p) as imap:
                    imap.login(addr, pwd)
                    imap.select("INBOX")
                    criterion = "UNSEEN" if unread_only else "ALL"
                    _status, data = imap.search(None, criterion)
                    ids = data[0].split() if data[0] else []
                    ids = ids[-limit:]

                    emails: list[dict[str, str]] = []
                    for mid in reversed(ids):
                        _status, msg_data = imap.fetch(mid, "(RFC822)")
                        if not msg_data or not msg_data[0]:
                            continue
                        raw = msg_data[0]
                        if isinstance(raw, tuple) and len(raw) >= 2:
                            raw_bytes = raw[1]
                        else:
                            continue
                        if not isinstance(raw_bytes, bytes):
                            continue
                        msg = email.message_from_bytes(raw_bytes)
                        subj_parts = email.header.decode_header(msg.get("Subject", ""))
                        subject_str = ""
                        for part, enc in subj_parts:
                            if isinstance(part, bytes):
                                subject_str += part.decode(enc or "utf-8", errors="replace")
                            else:
                                subject_str += str(part)
                        body_str = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() == "text/plain":
                                    payload = part.get_payload(decode=True)
                                    if isinstance(payload, bytes):
                                        body_str = payload.decode("utf-8", errors="replace")
                                    break
                        else:
                            payload = msg.get_payload(decode=True)
                            if isinstance(payload, bytes):
                                body_str = payload.decode("utf-8", errors="replace")
                        emails.append({
                            "from": str(msg.get("From", "")),
                            "subject": subject_str,
                            "date": str(msg.get("Date", "")),
                            "body": body_str,
                        })
                    return {"emails": emails, "count": str(len(emails))}
            except imaplib.IMAP4.error as e:
                return {"error": f"IMAP error: {e}"}

        @tool("Search emails matching a query via IMAP")
        def email_search(query: str, limit: int = 10) -> dict[str, str | list[dict[str, str]]]:
            if not pwd:
                return {"error": "email password not configured"}
            import email
            import email.header
            import imaplib

            try:
                with imaplib.IMAP4_SSL(imap_h, imap_p) as imap:
                    imap.login(addr, pwd)
                    # Try "All Mail" first (Gmail), fall back to INBOX
                    try:
                        imap.select("[Gmail]/All Mail")
                    except imaplib.IMAP4.error:
                        imap.select("INBOX")
                    # Try Gmail-specific search, fall back to standard IMAP SUBJECT search
                    try:
                        _status, data = imap.search(None, "X-GM-RAW", f'"{query}"')
                    except imaplib.IMAP4.error:
                        _status, data = imap.search(None, "SUBJECT", f'"{query}"')
                    ids = data[0].split() if data[0] else []
                    ids = ids[-limit:]
                    results: list[dict[str, str]] = []
                    for mid in reversed(ids):
                        _status, msg_data = imap.fetch(mid, "(RFC822)")
                        if not msg_data or not msg_data[0]:
                            continue
                        raw = msg_data[0]
                        if isinstance(raw, tuple) and len(raw) >= 2:
                            raw_bytes = raw[1]
                        else:
                            continue
                        if not isinstance(raw_bytes, bytes):
                            continue
                        msg = email.message_from_bytes(raw_bytes)
                        subj_parts = email.header.decode_header(msg.get("Subject", ""))
                        subject_str = ""
                        for part, enc in subj_parts:
                            if isinstance(part, bytes):
                                subject_str += part.decode(enc or "utf-8", errors="replace")
                            else:
                                subject_str += str(part)
                        results.append({
                            "from": str(msg.get("From", "")),
                            "subject": subject_str,
                            "date": str(msg.get("Date", "")),
                        })
                    return {"results": results, "count": str(len(results))}
            except imaplib.IMAP4.error as e:
                return {"error": f"IMAP error: {e}"}

        return replace(
            ctx,
            messages=(*ctx.messages,
                system(text=f"Email ({addr}): email_send, email_read_inbox, email_search."),
            ),
            tools=(*ctx.tools, email_send, email_read_inbox, email_search),
        )


GmailAccess = EmailAccess


# ═══════════════════════════════════════════════════════════════════════════════
# BlockchainWallet — on-chain truth via explorer APIs
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class ChainConfig:
    """Typed EVM chain configuration — no string-keyed dicts."""

    explorer_api: str
    usdt_contract: str
    name: str
    native: str


# Immutable mapping — not a mutable global dict
_CHAIN_CONFIGS: dict[str, ChainConfig] = {
    "bsc": ChainConfig(
        explorer_api="https://api.bscscan.com/api",
        usdt_contract="0x55d398326f99059fF775485246999027B3197955",
        name="BNB Smart Chain", native="BNB",
    ),
    "eth": ChainConfig(
        explorer_api="https://api.etherscan.io/api",
        usdt_contract="0xdAC17F958D2ee523a2206206994597C13D831ec7",
        name="Ethereum", native="ETH",
    ),
    "polygon": ChainConfig(
        explorer_api="https://api.polygonscan.com/api",
        usdt_contract="0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
        name="Polygon", native="MATIC",
    ),
}


@dataclass(frozen=True, slots=True)
class BlockchainWallet:
    """On-chain wallet — USDT on EVM chains."""

    address: str
    private_key: str = ""
    chain: str = "bsc"
    token: str = "USDT"
    explorer_api_key: str = ""
    token_decimals: int = 18

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        addr = self.address
        chain = self.chain
        token = self.token
        api_key = self.explorer_api_key
        decimals = self.token_decimals
        pk = self.private_key

        chain_cfg = _CHAIN_CONFIGS.get(chain, _CHAIN_CONFIGS["bsc"])
        explorer_base = chain_cfg.explorer_api
        token_contract = chain_cfg.usdt_contract  # typed attribute access

        def _explorer_call(params: dict[str, str]) -> dict[str, str]:
            import json
            import urllib.error
            import urllib.parse
            import urllib.request

            # Non-mutating: copy params before adding api key
            call_params = {**params, "apikey": api_key} if api_key else params
            url = f"{explorer_base}?{urllib.parse.urlencode(call_params)}"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "mmkr-agent/1.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                return {"status": "0", "message": f"HTTP {e.code}", "result": ""}
            except Exception as e:
                return {"status": "0", "message": str(e), "result": ""}

        @tool("Check wallet token balance on-chain")
        def wallet_balance() -> dict[str, str | float]:
            result = _explorer_call({
                "module": "account", "action": "tokenbalance",
                "contractaddress": token_contract, "address": addr, "tag": "latest",
            })
            if result.get("status") == "1" or result.get("message") == "OK":
                raw = int(result.get("result", "0"))
                balance = raw / (10 ** decimals)
                return {"balance": balance, "token": token, "chain": chain, "address": addr}
            return {"error": result.get("message", "unknown error"), "balance": 0.0}

        @tool("Get recent token transactions")
        def wallet_transactions(limit: int = 10) -> dict[str, str | list[dict[str, str | float]]]:
            result = _explorer_call({
                "module": "account", "action": "tokentx",
                "contractaddress": token_contract, "address": addr,
                "page": "1", "offset": str(limit), "sort": "desc",
            })
            if result.get("status") == "1":
                txs: list[dict[str, str | float]] = []
                raw_list = result.get("result", [])
                if not isinstance(raw_list, list):
                    return {"error": "unexpected response format"}
                for tx in raw_list:
                    if not isinstance(tx, dict):
                        continue
                    value_raw = int(tx.get("value", "0"))
                    value = value_raw / (10 ** decimals)
                    direction = "in" if tx.get("to", "").lower() == addr.lower() else "out"
                    txs.append({
                        "hash": str(tx.get("hash", "")), "from": str(tx.get("from", "")),
                        "to": str(tx.get("to", "")), "value": value,
                        "direction": direction, "timestamp": str(tx.get("timeStamp", "")),
                    })
                return {"transactions": txs, "count": str(len(txs))}
            return {"error": result.get("message", "unknown error")}

        @tool("Generate a payment request")
        def wallet_payment_request(amount: float, memo: str = "") -> dict[str, str | float]:
            return {
                "address": addr, "amount": amount, "token": token,
                "chain": chain_cfg.name, "memo": memo,
                "instruction": f"Send {amount} {token} to {addr} on {chain_cfg.name}" + (f" (memo: {memo})" if memo else ""),
            }

        @tool("Verify a transaction by hash")
        def wallet_verify_tx(tx_hash: str) -> dict[str, str | float | bool]:
            result = _explorer_call({
                "module": "proxy", "action": "eth_getTransactionReceipt", "txhash": tx_hash,
            })
            receipt = result.get("result")
            if not isinstance(receipt, dict):
                return {"error": "transaction not found or pending", "confirmed": False}
            confirmed = receipt.get("status", "0x0") == "0x1"
            return {
                "tx_hash": tx_hash, "confirmed": confirmed,
                "block": str(receipt.get("blockNumber", "")),
                "from": str(receipt.get("from", "")), "to": str(receipt.get("to", "")),
            }

        tools_list = [wallet_balance, wallet_transactions, wallet_payment_request, wallet_verify_tx]

        if pk:
            @tool("Send tokens from this wallet")
            def wallet_send(to_address: str, amount: float) -> dict[str, str | float | bool]:
                try:
                    from eth_account import Account  # type: ignore[import-untyped]
                except ImportError:
                    return {"error": "eth_account package required. pip install eth-account"}
                nonce_result = _explorer_call({
                    "module": "proxy", "action": "eth_getTransactionCount",
                    "address": addr, "tag": "latest",
                })
                nonce_raw = nonce_result.get("result", "0x0")
                if not isinstance(nonce_raw, str):
                    return {"error": "failed to get nonce"}
                nonce = int(nonce_raw, 16)
                to_padded = to_address.lower().replace("0x", "").zfill(64)
                amount_wei = int(amount * (10 ** decimals))
                amount_padded = hex(amount_wei)[2:].zfill(64)
                tx_data = f"0xa9059cbb{to_padded}{amount_padded}"
                gas_result = _explorer_call({"module": "proxy", "action": "eth_gasPrice"})
                gas_price_raw = gas_result.get("result", "0x12a05f200")
                if not isinstance(gas_price_raw, str):
                    return {"error": "failed to get gas price"}
                gas_price = int(gas_price_raw, 16)
                chain_ids = {"bsc": 56, "eth": 1, "polygon": 137}
                chain_id = chain_ids.get(chain, 56)
                tx = {
                    "nonce": nonce, "gasPrice": gas_price, "gas": 100000,
                    "to": token_contract, "value": 0,
                    "data": bytes.fromhex(tx_data[2:]), "chainId": chain_id,
                }
                try:
                    signed = Account.sign_transaction(tx, pk)
                except Exception as e:
                    return {"error": f"signing failed: {e}"}
                raw_tx = "0x" + signed.raw_transaction.hex()
                broadcast_result = _explorer_call({
                    "module": "proxy", "action": "eth_sendRawTransaction", "hex": raw_tx,
                })
                result_hash = broadcast_result.get("result", "")
                if isinstance(result_hash, str) and result_hash.startswith("0x"):
                    return {"status": "sent", "tx_hash": result_hash, "to": to_address, "amount": amount, "token": token}
                return {"error": broadcast_result.get("message", "broadcast failed")}
            tools_list.append(wallet_send)

        can_send = " Can send." if pk else " Read-only."
        return replace(
            ctx,
            messages=(*ctx.messages,
                system(text=f"Wallet: {addr[:8]}...{addr[-4:]} on {chain_cfg.name}. {token}.{can_send}"),
            ),
            tools=(*ctx.tools, *tools_list),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# BrowserAccess — persistent Playwright session
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class BrowserAccess:
    """Persistent headless Chromium browser via Playwright.

    One browser session shared across all tool calls within a tick.
    Supports multi-step flows (signup, checkout, navigation chains).
    session_dir: if set, cookies/localStorage persist across ticks via storage_state.
    """

    headless: bool = True
    default_timeout: int = 15000
    session_dir: str = ""

    def compile_life(self, ctx: LifeContext) -> LifeContext:
        _headless = self.headless
        _timeout = self.default_timeout
        _session_dir = self.session_dir

        # Session file for cookie/localStorage persistence
        _session_file: Path | None = None
        if _session_dir:
            _session_file = Path(_session_dir) / "session.json"
            Path(_session_dir).mkdir(parents=True, exist_ok=True)

        # Typed mutable session state shared by all tools via closure
        _pw: list[AsyncPlaywright | None] = [None]
        _browser: list[Browser | None] = [None]
        _context_ref: list[BrowserContext | None] = [None]
        _page_ref: list[Page | None] = [None]

        async def _page() -> Page:
            """Get or create persistent browser page."""
            from playwright.async_api import async_playwright

            page = _page_ref[0]
            if page is not None and not page.is_closed():
                return page

            pw = _pw[0]
            if pw is None:
                pw = await async_playwright().start()
                _pw[0] = pw

            browser = _browser[0]
            if browser is None:
                browser = await pw.chromium.launch(headless=_headless)
                _browser[0] = browser

            context_kwargs: dict[str, str | dict[str, int]] = {
                "viewport": {"width": 1280, "height": 720},
            }
            if _session_file and _session_file.exists():
                context_kwargs["storage_state"] = str(_session_file)

            context = await browser.new_context(**context_kwargs)
            _context_ref[0] = context
            page = await context.new_page()
            _page_ref[0] = page
            return page

        @tool(
            "Navigate to URL. Returns url, title, content. Session persists across calls. "
            "save_to_file='/path' to write content to file instead of inline "
            "(then use Read with offset/limit to inspect large pages)."
        )
        async def browse(url: str, wait_for: str = "", save_to_file: str = "") -> dict[str, str]:
            page = await _page()
            try:
                await page.goto(url, timeout=_timeout, wait_until="domcontentloaded")
                if wait_for:
                    await page.wait_for_selector(wait_for, timeout=_timeout)
                content = await page.inner_text("body")
                if save_to_file:
                    import os
                    os.makedirs(os.path.dirname(save_to_file) or ".", exist_ok=True)
                    with open(save_to_file, "w") as f:
                        f.write(content)
                    return {
                        "url": page.url,
                        "title": await page.title(),
                        "content": f"(written to {save_to_file}, {len(content)} chars — use Read to inspect)",
                    }
                return {
                    "url": page.url,
                    "title": await page.title(),
                    "content": content,
                }
            except Exception as exc:
                return {"error": f"{type(exc).__name__}: {exc}", "url": url}

        @tool("Click element by CSS selector. Returns url, title, content after click.")
        async def browser_click(selector: str) -> dict[str, str]:
            page = await _page()
            try:
                await page.click(selector, timeout=_timeout)
                try:
                    await page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    pass  # Click didn't navigate — that's OK
                return {
                    "url": page.url,
                    "title": await page.title(),
                    "content": await page.inner_text("body"),
                }
            except Exception as exc:
                return {"error": f"{type(exc).__name__}: {exc}"}

        @tool("Fill an input field by CSS selector. Use browser_click to submit after.")
        async def browser_type(selector: str, text: str) -> dict[str, str | bool]:
            page = await _page()
            try:
                await page.fill(selector, text, timeout=_timeout)
                return {"filled": True, "selector": selector}
            except Exception as exc:
                return {"error": f"{type(exc).__name__}: {exc}"}

        @tool("Screenshot current page. Saved to /agent-data/screenshots/. Returns file path.")
        async def browser_screenshot(filename: str = "") -> dict[str, str]:
            import os
            import time as _time

            page = await _page()
            screenshots_dir = "/agent-data/screenshots"
            os.makedirs(screenshots_dir, exist_ok=True)
            if not filename:
                filename = f"screenshot_{int(_time.time())}.png"
            if not filename.endswith(".png"):
                filename += ".png"
            filepath = f"{screenshots_dir}/{filename}"
            try:
                await page.screenshot(path=filepath, full_page=False)
                return {"path": filepath, "url": page.url}
            except Exception as exc:
                return {"error": f"{type(exc).__name__}: {exc}"}

        @tool(
            "Get current page content. html=True for raw HTML, selector for specific element. "
            "save_to_file='/path' to write to file instead of inline "
            "(then use Read with offset/limit)."
        )
        async def browser_content(html: bool = False, selector: str = "", save_to_file: str = "") -> dict[str, str]:
            page = await _page()
            try:
                if html:
                    if selector:
                        el = await page.query_selector(selector)
                        if el is None:
                            return {"error": f"selector not found: {selector}"}
                        content = await el.inner_html()
                    else:
                        content = await page.content()
                else:
                    content = await page.inner_text(selector or "body")
                if save_to_file:
                    import os
                    os.makedirs(os.path.dirname(save_to_file) or ".", exist_ok=True)
                    with open(save_to_file, "w") as f:
                        f.write(content)
                    return {
                        "url": page.url, "title": await page.title(),
                        "content": f"(written to {save_to_file}, {len(content)} chars — use Read to inspect)",
                    }
                return {"url": page.url, "title": await page.title(), "content": content}
            except Exception as exc:
                return {"error": f"{type(exc).__name__}: {exc}"}

        @tool("Run JavaScript in the browser page. Returns expression result as string.")
        async def browser_eval(javascript: str) -> dict[str, str]:
            page = await _page()
            try:
                result = await page.evaluate(javascript)
                return {"result": str(result), "url": page.url}
            except Exception as exc:
                return {"error": f"{type(exc).__name__}: {exc}"}

        @tool(
            "Save browser cookies/localStorage to disk. Call after login. "
            "Session auto-restores on next tick. Only works if session_dir is configured."
        )
        async def browser_save_session() -> dict[str, str | bool]:
            context = _context_ref[0]
            if context is None:
                return {"error": "no browser context — browse() first"}
            if _session_file is None:
                return {"error": "session_dir not configured — cannot save"}
            try:
                await context.storage_state(path=str(_session_file))
                return {"saved": True, "path": str(_session_file)}
            except Exception as exc:
                return {"error": f"{type(exc).__name__}: {exc}"}

        @tool("Close browser session and free resources. Auto-saves session if configured. Next browse() auto-reopens.")
        async def browser_close() -> dict[str, str | bool]:
            try:
                # Auto-save session before closing
                context = _context_ref[0]
                if context is not None and _session_file is not None:
                    try:
                        await context.storage_state(path=str(_session_file))
                    except Exception:
                        pass  # Best-effort save
                    await context.close()

                browser = _browser[0]
                if browser is not None:
                    await browser.close()
                pw = _pw[0]
                if pw is not None:
                    await pw.stop()
                _pw[0] = None
                _browser[0] = None
                _context_ref[0] = None
                _page_ref[0] = None
                return {"closed": True}
            except Exception as exc:
                return {"error": f"{type(exc).__name__}: {exc}"}

        session_info = ""
        if _session_file:
            restored = " (restored)" if _session_file.exists() else ""
            session_info = (
                f" browser_save_session() to persist cookies across ticks{restored}."
            )

        return replace(
            ctx,
            messages=(*ctx.messages,
                system(text=(
                    "Browser: persistent Chromium session. "
                    "browse(url) → browser_type(selector, text) → browser_click(selector) "
                    "for multi-step flows. browser_screenshot(), browser_content(html?), "
                    "browser_eval(javascript) for inspection. browser_close() when done. "
                    "Session persists across calls — no need to re-navigate."
                    + session_info
                )),
            ),
            tools=(*ctx.tools, browse, browser_click, browser_type,
                   browser_screenshot, browser_content, browser_eval,
                   browser_save_session, browser_close),
        )
