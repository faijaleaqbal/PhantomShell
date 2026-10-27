"""Tests for PhantomShell.

Everything here runs offline: no Ollama daemon, no network, no terminal needed.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import haunt  # noqa: E402

if haunt._HAVE_RICH:
    from rich.console import Console
else:  # pragma: no cover
    Console = None  # type: ignore[assignment]


def make_console() -> "Console":
    return Console(
        file=io.StringIO(),
        force_terminal=False,
        width=100,
        no_color=True,
        legacy_windows=False,
    )


def console_text(console: "Console") -> str:
    return console.file.getvalue()


class TextHygieneTests(unittest.TestCase):
    def test_strips_csi_colour_sequences(self) -> None:
        self.assertEqual(haunt.strip_control_sequences("\x1b[31mred\x1b[0m"), "red")

    def test_strips_osc_title_and_hyperlinks(self) -> None:
        self.assertEqual(haunt.strip_control_sequences("\x1b]0;pwned\x07ok"), "ok")
        self.assertEqual(haunt.strip_control_sequences("\x1b]8;;http://x\x1b\\link\x1b]8;;\x1b\\"), "link")

    def test_strips_bidi_overrides_and_nulls(self) -> None:
        self.assertEqual(haunt.strip_control_sequences("a\u202eb\x00c"), "abc")

    def test_keeps_tabs_and_newlines(self) -> None:
        self.assertEqual(haunt.strip_control_sequences("a\tb\nc"), "a\tb\nc")

    def test_redacts_common_secret_shapes(self) -> None:
        text = "token=supersecret123 password: hunter2 Authorization: Bearer abcdefghijklmnop"
        cleaned = haunt.redact_secrets(text)
        self.assertIn("token=[REDACTED]", cleaned)
        self.assertIn("password=[REDACTED]", cleaned)
        self.assertIn("Bearer [REDACTED]", cleaned)
        self.assertNotIn("supersecret123", cleaned)
        self.assertNotIn("hunter2", cleaned)

    def test_redacts_groq_keys(self) -> None:
        cleaned = haunt.redact_secrets("key gsk_" + "a1" * 20)
        self.assertNotIn("gsk_", cleaned)
        self.assertIn("[REDACTED]", cleaned)

    def test_clean_evidence_keeps_the_tail(self) -> None:
        self.assertEqual(haunt.clean_evidence("x" * 50, 10), "..." + "x" * 10)

    def test_truncate_tail_handles_tiny_limits(self) -> None:
        self.assertEqual(haunt.truncate_tail("abcdef", 0), "")
        self.assertEqual(haunt.truncate_tail("abcdef", 6), "abcdef")


class ReplyParsingTests(unittest.TestCase):
    def test_fenced_reply(self) -> None:
        raw = "The machine remembers your almost-git.\n```bash\ngit status\n```"
        reply = haunt.parse_reply(raw)
        self.assertEqual(reply.fix, "git status")
        self.assertIn("remembers", reply.commentary)

    def test_inline_fence_reply(self) -> None:
        reply = haunt.parse_reply("A tiny haunting. ```sh ls -la```")
        self.assertEqual(reply.fix, "ls -la")

    def test_unfenced_reply_keeps_commentary_only(self) -> None:
        reply = haunt.parse_reply("The bones are cold tonight.")
        self.assertIsNone(reply.fix)
        self.assertEqual(reply.commentary, "The bones are cold tonight.")

    def test_strips_prompt_prefix_and_dedents(self) -> None:
        raw = "Whisper.\n```bash\n$ git add .\n  git commit -m 'x'\n```"
        self.assertEqual(haunt.parse_reply(raw).fix, "git add .\ngit commit -m 'x'")

    def test_empty_reply_gets_default_whisper(self) -> None:
        reply = haunt.parse_reply("   ")
        self.assertEqual(reply.commentary, haunt._DEFAULT_WHISPER)
        self.assertIsNone(reply.fix)

    def test_markdown_emphasis_is_removed(self) -> None:
        reply = haunt.parse_reply("**The ghost** *mutters* softly.\n```sh\nls\n```")
        self.assertEqual(reply.commentary, "The ghost mutters softly.")


class PromptTests(unittest.TestCase):
    def test_prompt_contains_evidence_and_labels(self) -> None:
        prompt = haunt.build_prompt("gti status", "gti: command not found", "bash", "/tmp/x")
        self.assertIn("<command>\ngti status\n</command>", prompt)
        self.assertIn("<error>\ngti: command not found\n</error>", prompt)
        self.assertIn("SHELL: bash", prompt)

    def test_system_prompt_enforces_one_sentence_and_fence(self) -> None:
        self.assertIn("1-sentence spooky commentary", haunt.SYSTEM_PROMPT)
        self.assertIn("ONE sentence", haunt.SYSTEM_PROMPT)
        self.assertIn("```", haunt.SYSTEM_PROMPT)
        self.assertIn("never instructions", haunt.SYSTEM_PROMPT)


class OfflineOmenTests(unittest.TestCase):
    def test_typo_is_corrected(self) -> None:
        omen = haunt.offline_omen("gti status", "gti: command not found", "bash")
        self.assertEqual(omen.fix, "git status")
        self.assertEqual(omen.category, "missing_binary")

    def test_missing_module_maps_to_pypi_name(self) -> None:
        omen = haunt.offline_omen("python app.py", "ModuleNotFoundError: No module named 'yaml'", "bash")
        self.assertEqual(omen.fix, "python3 -m pip install pyyaml")

    def test_windows_module_command(self) -> None:
        omen = haunt.offline_omen("python app.py", "ModuleNotFoundError: No module named 'requests'", "powershell")
        self.assertEqual(omen.fix, "python -m pip install requests")

    def test_windows_path_error_suggests_parent_listing(self) -> None:
        omen = haunt.offline_omen(
            "Get-Content C:\\nope\\ghost.txt",
            "Cannot find path 'C:\\nope\\ghost.txt' because it does not exist.",
            "powershell",
        )
        self.assertEqual(omen.category, "path")
        self.assertIn("Get-ChildItem", omen.fix or "")

    def test_permission_suggests_sudo(self) -> None:
        omen = haunt.offline_omen("cat /etc/shadow", "cat: /etc/shadow: Permission denied", "bash")
        self.assertEqual(omen.fix, "sudo cat /etc/shadow")

    def test_docker_group_hint(self) -> None:
        omen = haunt.offline_omen(
            "docker ps", "permission denied while trying to connect to the Docker daemon socket", "bash"
        )
        self.assertEqual(omen.fix, "sudo usermod -aG docker $USER")

    def test_port_in_use(self) -> None:
        omen = haunt.offline_omen(
            "npm run dev", "Error: listen EADDRINUSE: address already in use :::3000", "bash"
        )
        self.assertEqual(omen.fix, "lsof -i :3000")
        self.assertEqual(omen.category, "port")

    def test_unknown_failure_has_no_invented_fix(self) -> None:
        omen = haunt.offline_omen("weird-command --x", "something odd happened", "bash")
        self.assertIsNone(omen.fix)
        self.assertEqual(omen.category, "generic")

    def test_ctrl_c_is_explained_not_fixed(self) -> None:
        omen = haunt.offline_omen("sleep 100", "exit code 130", "bash")
        self.assertIsNone(omen.fix)
        self.assertIn("interrupted", omen.hint.lower())

    def test_first_token_skips_wrappers(self) -> None:
        self.assertEqual(haunt.first_token("FOO=1 sudo git status"), "git")

    def test_missing_binary_is_read_from_the_error_not_argv(self) -> None:
        omen = haunt.offline_omen("sudo -u root gti status", "bash: gti: command not found", "bash")
        self.assertEqual(omen.fix, "sudo -u root git status")

    def test_closest_command_is_case_insensitive(self) -> None:
        self.assertEqual(haunt.closest_command("GIT"), "git")


class _FakeOllama(BaseHTTPRequestHandler):
    """A 20-line stand-in for `ollama serve`, used only by the tests."""

    models: list[str] = ["llama3.2:1b", "qwen2.5-coder:1.5b"]
    response: str = (
        "The machine remembers a name you almost spoke, and it was git.\n"
        "```bash\n"
        "git status\n"
        "```"
    )
    missing_model: bool = False

    def log_message(self, *args) -> None:  # silence test output
        pass

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/api/tags":
            self._json({"models": [{"name": name} for name in self.models]})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.missing_model:
            self._json({"error": f"model '{payload.get('model')}' not found"}, 404)
            return
        self._json({"model": payload.get("model"), "response": self.response, "done": True})


_CLOUD_REPLY = (
    "The wire hums: a letter was dropped at the door.\n"
    "```bash\n"
    "git status\n"
    "```"
)


class _FakeOpenAI(BaseHTTPRequestHandler):
    """A stand-in for Groq/OpenAI `/chat/completions`, used only by the tests."""

    reply: str = _CLOUD_REPLY
    error_status: int = 200
    error_body: str = ""
    last_auth: str = ""
    last_payload: dict = {}

    def log_message(self, *args) -> None:  # silence test output
        pass

    def _json(self, payload: dict | str, status: int = 200) -> None:
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        body = raw.encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        _FakeOpenAI.last_payload = json.loads(self.rfile.read(length) or b"{}")
        _FakeOpenAI.last_auth = self.headers.get("Authorization", "")
        if _FakeOpenAI.error_status != 200:
            self._json(_FakeOpenAI.error_body or "{}", _FakeOpenAI.error_status)
            return
        self._json(
            {
                "id": "chatcmpl-test",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": _FakeOpenAI.reply}}
                ],
            }
        )


class CloudClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOpenAI)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        _FakeOpenAI.reply = _CLOUD_REPLY
        _FakeOpenAI.error_status = 200
        _FakeOpenAI.error_body = ""
        _FakeOpenAI.last_auth = ""
        _FakeOpenAI.last_payload = {}

    def client(self, **kwargs) -> "haunt.CloudClient":
        defaults = dict(
            provider="groq",
            base_url=self.base,
            api_key="gsk_test_key",
            model="openai/gpt-oss-20b",
            timeout=3,
            key_env="GROQ_API_KEY",
        )
        defaults.update(kwargs)
        return haunt.CloudClient(**defaults)

    def test_generate_sends_bearer_token_and_parses_reply(self) -> None:
        client = self.client()
        reply = client.generate("prompt")
        self.assertEqual(reply.fix, "git status")
        self.assertEqual(_FakeOpenAI.last_auth, "Bearer gsk_test_key")
        self.assertEqual(_FakeOpenAI.last_payload["model"], "openai/gpt-oss-20b")
        self.assertEqual(client.label, "groq/openai/gpt-oss-20b")
        roles = [m["role"] for m in _FakeOpenAI.last_payload["messages"]]
        self.assertEqual(roles, ["system", "user"])

    def test_401_mentions_the_key_variable(self) -> None:
        _FakeOpenAI.error_status = 401
        _FakeOpenAI.error_body = '{"error": {"message": "invalid api key"}}'
        with self.assertRaises(haunt.GhostUnavailable) as ctx:
            self.client().generate("prompt")
        self.assertIn("401", str(ctx.exception))
        self.assertIn("GROQ_API_KEY", str(ctx.exception))

    def test_api_key_is_never_echoed_back(self) -> None:
        _FakeOpenAI.error_status = 401
        _FakeOpenAI.error_body = json.dumps({"error": "bad key gsk_test_key"})
        with self.assertRaises(haunt.GhostUnavailable) as ctx:
            self.client().generate("prompt")
        self.assertNotIn("gsk_test_key", str(ctx.exception))

    def test_429_is_a_rate_limit_message(self) -> None:
        _FakeOpenAI.error_status = 429
        with self.assertRaises(haunt.GhostUnavailable) as ctx:
            self.client().generate("prompt")
        self.assertIn("rate", str(ctx.exception).lower())

    def test_404_points_at_the_model(self) -> None:
        _FakeOpenAI.error_status = 404
        with self.assertRaises(haunt.GhostUnavailable) as ctx:
            self.client().generate("prompt")
        self.assertIn("PHANTOM_MODEL", str(ctx.exception))

    def test_unparseable_body_is_friendly(self) -> None:
        _FakeOpenAI.reply = ""
        with self.assertRaises(haunt.GhostUnavailable) as ctx:
            self.client().generate("prompt")
        self.assertIn("silence", str(ctx.exception))

    def test_content_parts_are_joined(self) -> None:
        parts = {
            "choices": [
                {
                    "message": {
                        "content": [{"type": "text", "text": "```bash\nls\n```"}],
                    }
                }
            ]
        }
        with mock.patch.object(_FakeOpenAI, "do_POST", lambda self: self._json(parts)):
            reply = self.client().generate("prompt")
        self.assertEqual(reply.fix, "ls")

    def test_full_cli_run_uses_the_cloud(self) -> None:
        env = {"GROQ_API_KEY": "gsk_test_key"}
        buf = io.StringIO()
        with mock.patch.dict("os.environ", env, clear=False), contextlib.redirect_stdout(buf):
            code = haunt.run(
                [
                    "gti status",
                    "gti: command not found",
                    "--provider",
                    "groq",
                    "--api-base",
                    self.base,
                    "--fix-only",
                ],
                console=make_console(),
            )
        self.assertEqual(code, 0)
        self.assertEqual(buf.getvalue(), "git status\n")

    def test_cli_labels_the_cloud_model(self) -> None:
        env = {"GROQ_API_KEY": "gsk_test_key"}
        console = make_console()
        with mock.patch.dict("os.environ", env, clear=False):
            haunt.run(
                [
                    "gti status",
                    "gti: command not found",
                    "--provider",
                    "groq",
                    "--api-base",
                    self.base,
                    "--seed",
                    "3",
                    "--no-flicker",
                ],
                console=console,
            )
        self.assertIn("groq/openai/gpt-oss-20b", console_text(console))

    def test_cli_falls_back_to_bones_when_the_key_is_rejected(self) -> None:
        _FakeOpenAI.error_status = 401
        _FakeOpenAI.error_body = json.dumps({"error": "bad key gsk_test_key"})
        env = {"GROQ_API_KEY": "gsk_test_key"}
        console = make_console()
        with mock.patch.dict("os.environ", env, clear=False), contextlib.redirect_stdout(io.StringIO()):
            code = haunt.run(
                [
                    "gti status",
                    "gti: command not found",
                    "--provider",
                    "groq",
                    "--api-base",
                    self.base,
                    "--seed",
                    "2",
                    "--no-flicker",
                ],
                console=console,
            )
        out = console_text(console)
        self.assertEqual(code, 0)
        self.assertIn("git status", out)
        self.assertIn("GROQ_API_KEY", out)
        self.assertNotIn("gsk_test_key", out)


class MakeClientTests(unittest.TestCase):
    def args(self, **kwargs) -> "argparse.Namespace":
        base = dict(
            provider="auto",
            api_base="http://127.0.0.1:1/v1",
            model="auto",
            host="http://127.0.0.1:1",
            timeout=2,
        )
        base.update(kwargs)
        return argparse.Namespace(**base)

    def test_auto_without_keys_stays_local(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            client = haunt.make_client(self.args())
        self.assertIsInstance(client, haunt.OllamaClient)

    def test_auto_prefers_groq_when_key_exists(self) -> None:
        with mock.patch.dict("os.environ", {"GROQ_API_KEY": "gsk_x"}, clear=True):
            client = haunt.make_client(self.args())
        self.assertIsInstance(client, haunt.CloudClient)
        self.assertEqual(client.provider, "groq")
        self.assertEqual(client.model, "openai/gpt-oss-20b")

    def test_explicit_provider_without_key_is_unavailable(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(haunt.GhostUnavailable) as ctx:
                haunt.make_client(self.args(provider="groq"))
        self.assertIn("GROQ_API_KEY", str(ctx.exception))

    def test_generic_key_works_for_any_provider(self) -> None:
        with mock.patch.dict("os.environ", {"PHANTOM_API_KEY": "sk_x"}, clear=True):
            client = haunt.make_client(self.args(provider="openai", model="gpt-4o-mini"))
        self.assertEqual(client.api_key, "sk_x")
        self.assertEqual(client.base_url, "http://127.0.0.1:1/v1")

    def test_custom_provider_allows_an_empty_key(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            client = haunt.make_client(
                self.args(provider="custom", model="local-ghost", api_base="http://127.0.0.1:1234/v1")
            )
        self.assertEqual(client.api_key, "")
        self.assertEqual(client.model, "local-ghost")

    def test_empty_model_on_custom_is_rejected(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(haunt.GhostUnavailable):
                haunt.make_client(self.args(provider="custom", model="auto"))

    def test_cli_passes_provider_flags_through(self) -> None:
        args = haunt.parse_args(["--provider", "openai", "--api-base", "http://x/v1", "--model", "m"])
        self.assertEqual(args.provider, "openai")
        self.assertEqual(args.api_base, "http://x/v1")
        self.assertEqual(args.model, "m")



    def test_silent_during_the_day(self) -> None:
        console = make_console()
        with mock.patch.object(haunt, "is_late_night", return_value=False):
            code = haunt.run(["--night-whisper", "--seed", "1"], console=console)
        self.assertEqual(code, 0)
        self.assertEqual(console_text(console), "")

    def test_whispers_at_night(self) -> None:
        console = make_console()
        with mock.patch.object(haunt, "is_late_night", return_value=True):
            code = haunt.run(["--night-whisper", "--seed", "1"], console=console)
        self.assertEqual(code, 0)
        out = console_text(console)
        self.assertTrue(any(whisper in out for whisper in haunt.NIGHT_IDLE_WHISPERS))


class OllamaClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOllama)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.host = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        _FakeOllama.missing_model = False
        _FakeOllama.models = ["llama3.2:1b", "qwen2.5-coder:1.5b"]

    def test_auto_model_prefers_small_coder(self) -> None:
        client = haunt.OllamaClient(host=self.host, timeout=3, model="auto")
        self.assertEqual(client.resolve_model(), "qwen2.5-coder:1.5b")

    def test_explicit_model_is_respected(self) -> None:
        client = haunt.OllamaClient(host=self.host, timeout=3, model="llama3.2:1b")
        self.assertEqual(client.resolve_model(), "llama3.2:1b")

    def test_generate_returns_parsed_reply(self) -> None:
        client = haunt.OllamaClient(host=self.host, timeout=3, model="auto")
        reply = client.generate("prompt")
        self.assertEqual(reply.fix, "git status")
        self.assertEqual(client.model_used, "qwen2.5-coder:1.5b")

    def test_missing_model_is_a_friendly_error(self) -> None:
        _FakeOllama.missing_model = True
        client = haunt.OllamaClient(host=self.host, timeout=3, model="ghost:1b")
        with self.assertRaises(haunt.GhostUnavailable) as ctx:
            client.generate("prompt")
        self.assertIn("ollama pull ghost:1b", str(ctx.exception))

    def test_connection_refused_is_a_friendly_error(self) -> None:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()  # nothing listens here now

        client = haunt.OllamaClient(host=f"http://127.0.0.1:{port}", timeout=1, model="auto")
        with self.assertRaises(haunt.GhostUnavailable) as ctx:
            client.generate("prompt")
        # Windows may answer a closed local port with a timeout instead of RST.
        message = str(ctx.exception)
        self.assertTrue("no ghost answers" in message or "timed out" in message, message)


class CliTests(unittest.TestCase):
    def run_cli(self, argv: list[str]) -> tuple[int, str]:
        console = make_console()
        code = haunt.run(argv, console=console)
        return code, console_text(console)

    def test_offline_demo_renders_and_exits_zero(self) -> None:
        code, out = self.run_cli(["--demo", "--offline", "--seed", "7", "--no-flicker"])
        self.assertEqual(code, 0)
        self.assertIn("PHANTOM SHELL", out)

    def test_offline_typo_haunt_contains_fix(self) -> None:
        code, out = self.run_cli(
            ["gti status", "gti: command not found", "--offline", "--seed", "2", "--no-flicker"]
        )
        self.assertEqual(code, 0)
        self.assertIn("git status", out)
        self.assertIn("offline omen", out)

    def test_no_args_is_quiet_and_safe(self) -> None:
        code, out = self.run_cli([])
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_fix_only_prints_exactly_one_command(self) -> None:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = haunt.run(
                ["gti status", "gti: command not found", "--offline", "--fix-only"],
                console=make_console(),
            )
        self.assertEqual(code, 0)
        self.assertEqual(buf.getvalue(), "git status\n")

    def test_evidence_can_arrive_via_environment(self) -> None:
        buf = io.StringIO()
        env = {
            "PHANTOM_LAST_COMMAND": "gti status",
            "PHANTOM_LAST_ERROR": "gti: command not found",
        }
        with mock.patch.dict("os.environ", env, clear=False), contextlib.redirect_stdout(buf):
            code = haunt.run(["--offline", "--fix-only"], console=make_console())
        self.assertEqual(code, 0)
        self.assertEqual(buf.getvalue(), "git status\n")

    def test_model_failure_falls_back_to_bones(self) -> None:
        console = make_console()
        with mock.patch.object(
            haunt.OllamaClient,
            "generate",
            side_effect=haunt.GhostUnavailable("no ghost answers at http://127.0.0.1:1"),
        ):
            code = haunt.run(
                [
                    "gti status",
                    "gti: command not found",
                    "--provider",
                    "ollama",
                    "--seed",
                    "1",
                    "--no-flicker",
                ],
                console=console,
            )
        out = console_text(console)
        self.assertEqual(code, 0)
        self.assertIn("git status", out)
        self.assertIn("read from the bones", out)

    def test_art_is_optional(self) -> None:
        _, with_art = self.run_cli(["gti status", "gti: command not found", "--offline", "--seed", "4"])
        _, without_art = self.run_cli(
            ["gti status", "gti: command not found", "--offline", "--seed", "4", "--no-art"]
        )
        self.assertTrue(any(art in with_art for art in haunt.ARTWORKS))
        self.assertFalse(any(art in without_art for art in haunt.ARTWORKS))


if __name__ == "__main__":
    unittest.main()
