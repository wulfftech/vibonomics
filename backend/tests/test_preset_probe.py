"""scripts/preset_probe.py: the safety rails, the key scrubbing, and what each experiment reports.

Nothing here reaches OpenRouter: a stateful fake stands in for its API, behind respx, and every key is made up.
"""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import re
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "preset_probe.py"
_spec = importlib.util.spec_from_file_location("preset_probe", SCRIPT)
assert _spec is not None and _spec.loader is not None
probe = importlib.util.module_from_spec(_spec)
sys.modules["preset_probe"] = probe
_spec.loader.exec_module(probe)

KEY = "sk-or-v1-test0123456789abcdef0123456789abcdef"
LEAK = "sk-or-v1-leakedleakedleakedleaked00"
A, B = probe.SLUG_A, probe.SLUG_B
FREE_A = "acme/alpha:free"  # two free providers, both support repetition_penalty
FREE_B = "acme/beta:free"  # one free provider, no repetition_penalty
STEALTH = "stealth/crouton-alpha"  # free by price, one endpoint of unknown quantization
PAID = "acme/paid"

KNOWN_TOP = {
    "model",
    "models",
    "provider",
    "temperature",
    "top_p",
    "max_tokens",
    "max_completion_tokens",
    "repetition_penalty",
    "frequency_penalty",
    "presence_penalty",
    "route",
}
KNOWN_PROVIDER = {"only", "order", "sort", "quantizations", "ignore", "require_parameters"}
FREE = {"prompt": "0", "completion": "0"}


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """The probe talks to a fake here. A request that slipped past it must fail loudly, not reach OpenRouter."""

    def refuse(*_args, **_kwargs):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


def _endpoint(name: str, tag: str, quant: str, params: list[str], pricing: dict[str, str] | None = None) -> dict:
    return {
        "provider_name": name,
        "tag": tag,
        "quantization": quant,
        "supported_parameters": params,
        "pricing": pricing or FREE,
    }


@dataclass
class Call:
    method: str
    path: str
    body: Any
    authorization: str | None


class Fake:
    """A small model of OpenRouter's preset API, with switches for each behaviour the probe asks about."""

    def __init__(self, **opts: Any) -> None:
        self.calls: list[Call] = []
        self.presets: dict[str, dict[str, Any]] = {}
        self.list_extra: dict[str, Any] = {}
        self.list_status = 200
        self.network_down = False
        self.write_status: int | None = None
        self.write_body: Any = None
        # what the preset API does
        self.rename_max_tokens = False
        self.sort_object = False
        self.store_system = True
        self.carry_system = False
        self.drop: set[str] = set()
        self.reject: set[str] = set()
        self.unknown = "drop"  # drop, keep or reject
        self.unknown_message = "Unrecognized key: {key}"
        self.return_version = True
        self.model_override: str | None = None
        # what routing does
        self.request_wins = True
        self.provider_replace = False
        self.quant_excluded = True
        self.models_status = 200
        self.models = [
            {"id": FREE_A, "pricing": FREE, "supported_parameters": ["temperature", "repetition_penalty"]},
            {"id": FREE_B, "pricing": FREE, "supported_parameters": ["temperature"]},
            {"id": STEALTH, "pricing": FREE, "supported_parameters": []},
            {
                "id": PAID,
                "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                "supported_parameters": ["repetition_penalty"],
            },
        ]
        rp = ["temperature", "repetition_penalty"]
        self.endpoints: dict[str, list[dict]] = {
            FREE_A: [_endpoint("Venice", "venice/fp8", "fp8", rp), _endpoint("Chutes", "chutes/bf16", "bf16", rp)],
            FREE_B: [_endpoint("Acme", "acme", "fp16", ["temperature"])],
            STEALTH: [_endpoint("Stealth", "stealth", "unknown", [])],
            PAID: [_endpoint("Paid", "paid", "fp8", rp, {"prompt": "0.000001", "completion": "0.000002"})],
        }
        for k, v in opts.items():
            assert hasattr(self, k), k
            setattr(self, k, v)

    # -- helpers

    def latest(self, slug: str) -> dict[str, Any]:
        return self.presets[slug]["versions"][-1]

    def posts(self, prefix: str = "") -> list[Call]:
        return [c for c in self.calls if c.method == "POST" and c.path.startswith(prefix)]

    def completions(self) -> list[Call]:
        return [c for c in self.calls if c.method == "POST" and c.path == "/chat/completions"]

    def provider_name(self, model: str, tag: str) -> str:
        return next((e["provider_name"] for e in self.endpoints.get(model, []) if e["tag"] == tag), "Unknown")

    # -- the API

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v1")
        body = json.loads(request.content) if request.content else None
        self.calls.append(Call(request.method, path, body, request.headers.get("authorization")))
        if self.network_down:
            raise httpx.ConnectError("no route to host", request=request)
        if path == "/presets" and request.method == "GET":
            rows = [{"slug": s, "name": s, "status": p["status"]} for s, p in self.presets.items()]
            return httpx.Response(self.list_status, json={"data": rows, **self.list_extra})
        if (m := re.fullmatch(r"/presets/([^/]+)/chat/completions", path)) and request.method == "POST":
            return self.write(m[1], body)
        if m := re.fullmatch(r"/presets/([^/]+)/versions", path):
            if m[1] not in self.presets:
                return httpx.Response(404, json={"error": {"message": "not found", "code": 404}})
            return httpx.Response(200, json={"data": self.presets[m[1]]["versions"]})
        if m := re.fullmatch(r"/presets/([^/]+)", path):
            if m[1] not in self.presets:
                return httpx.Response(404, json={"error": {"message": "not found", "code": 404}})
            return httpx.Response(200, json={"data": self.view(m[1])})
        if path == "/models":
            if self.models_status != 200:
                return httpx.Response(self.models_status, json={"error": {"message": "down"}})
            return httpx.Response(200, json={"data": self.models})
        if m := re.fullmatch(r"/models/(.+)/endpoints", path):
            return httpx.Response(200, json={"data": {"id": m[1], "endpoints": self.endpoints.get(m[1], [])}})
        if path == "/chat/completions" and request.method == "POST":
            return self.complete(body)
        return httpx.Response(404, json={"error": {"message": f"no route {request.method} {path}", "code": 404}})

    def view(self, slug: str) -> dict[str, Any]:
        preset = self.presets[slug]
        return {"slug": slug, "name": slug, "status": preset["status"], "designated_version": self.latest(slug)}

    def write(self, slug: str, body: dict[str, Any]) -> httpx.Response:
        if self.write_status is not None:
            return httpx.Response(self.write_status, json=self.write_body)
        body = copy.deepcopy(body)
        messages = body.pop("messages", [])
        provider = body.get("provider") if isinstance(body.get("provider"), dict) else {}
        for name in sorted(self.reject):
            if name in body or name.removeprefix("provider.") in provider:
                msg = {"error": {"message": self.unknown_message.format(key=name), "code": 400}}
                return httpx.Response(400, json=msg)
        stray = [k for k in body if k not in KNOWN_TOP] + [f"provider.{k}" for k in provider if k not in KNOWN_PROVIDER]
        if self.unknown == "reject" and stray:
            msg = {"error": {"message": self.unknown_message.format(key=stray[0]), "code": 400}}
            return httpx.Response(400, json=msg)
        config = body
        if self.unknown == "drop":
            config = {k: v for k, v in body.items() if k in KNOWN_TOP}
            if isinstance(config.get("provider"), dict):
                config["provider"] = {k: v for k, v in config["provider"].items() if k in KNOWN_PROVIDER}
        if self.rename_max_tokens and "max_tokens" in config:
            config["max_completion_tokens"] = config.pop("max_tokens")
        for dotted in self.drop:
            if dotted.startswith("provider.") and isinstance(config.get("provider"), dict):
                config["provider"].pop(dotted.split(".", 1)[1], None)
            else:
                config.pop(dotted, None)
        if self.sort_object and isinstance(config.get("provider", {}).get("sort"), str):
            config["provider"]["sort"] = {"by": config["provider"]["sort"], "partition": "none"}
        if self.model_override:
            config["model"] = self.model_override
        sent = next((m["content"] for m in messages if m.get("role") == "system"), None)
        versions = self.presets.setdefault(slug, {"status": "active", "versions": []})["versions"]
        previous = versions[-1]["system_prompt"] if versions else None
        kept = previous if self.carry_system else None
        prompt = sent if sent is not None and self.store_system else kept
        version = {"version": len(versions) + 1, "config": config, "system_prompt": prompt}
        versions.append(version)
        data: dict[str, Any] = {"slug": slug, "name": slug, "status": "active"}
        if self.return_version:
            data["designated_version"] = version
        return httpx.Response(200, json={"data": data})

    def complete(self, body: dict[str, Any]) -> httpx.Response:
        slug = body["model"].removeprefix("@preset/")
        config = self.latest(slug)["config"]
        # The request's own provider block either replaces the preset's or is merged into it.
        own = body.get("provider") or {}
        provider = own if self.provider_replace and own else {**(config.get("provider") or {}), **own}
        model = config["model"]
        if model == STEALTH and provider.get("quantizations") and self.quant_excluded:
            err = {"error": {"message": "No endpoints found matching the requested quantization.", "code": 404}}
            return httpx.Response(404, json=err)
        if provider.get("require_parameters") and config.get("repetition_penalty") and model == FREE_B:
            err = {
                "error": {
                    "message": "No endpoints found that can handle the requested parameters.",
                    "code": 404,
                    "metadata": {"provider_name": None},
                }
            }
            return httpx.Response(404, json=err)
        cap = body.get("max_tokens") if self.request_wins else config.get("max_tokens", body.get("max_tokens"))
        tokens, finish = (cap, "length") if isinstance(cap, int) and cap < 20 else (2, "stop")
        served = "Acme"
        if provider.get("only"):
            served = self.provider_name(model, provider["only"][0])
        elif provider.get("order"):
            served = self.provider_name(model, provider["order"][0])
        return httpx.Response(
            200,
            json={
                "id": "gen-1",
                "provider": served,
                "model": model,
                "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": finish}],
                "usage": {"prompt_tokens": 9, "completion_tokens": tokens},
            },
        )


def run(
    fake: Fake, tmp_path: Path, *args: str, env: dict[str, str] | None = None
) -> tuple[int, dict[str, Any] | None, Path]:
    out = tmp_path / "report.json"
    with respx.mock(assert_all_called=False) as router:
        router.route().mock(side_effect=fake.handle)
        code = probe.main(["--out", str(out), *args], env={probe.KEY_ENV: KEY} if env is None else env)
    return code, (json.loads(out.read_text(encoding="utf-8")) if out.exists() else None), out


def steps_named(report: dict[str, Any], prefix: str) -> list[dict[str, Any]]:
    return [s for s in report["steps"] if s["name"].startswith(prefix)]


def answer(report: dict[str, Any], question: str) -> str:
    return next(i["answer"] for i in report["summary"] if i["question"].startswith(question))


# ---- safety rails -------------------------------------------------------------------------------------------


def test_refuses_without_a_key(tmp_path, capsys):
    fake = Fake()
    code, report, out = run(fake, tmp_path, env={})
    assert code == probe.EXIT_REFUSED == 2
    assert probe.KEY_ENV in capsys.readouterr().err
    assert fake.calls == [] and report is None and not out.exists()


def test_a_key_given_as_an_argument_is_not_accepted_or_echoed(tmp_path, capsys):
    fake = Fake()
    code, _report, _out = run(fake, tmp_path, "--key", KEY, env={})
    captured = capsys.readouterr()
    assert code == 2
    assert KEY not in captured.out + captured.err
    assert fake.calls == []


def test_refuses_a_workspace_that_holds_other_presets(tmp_path, capsys):
    fake = Fake()
    fake.presets["my-real-preset"] = {"status": "active", "versions": []}
    code, report, out = run(fake, tmp_path)
    assert code == 2
    assert "my-real-preset" in capsys.readouterr().err
    assert [c.method for c in fake.calls] == ["GET"] and fake.calls[0].path == "/presets"
    assert report is None and not out.exists()


def test_the_test_workspace_flag_lets_it_run_among_other_presets(tmp_path):
    fake = Fake()
    fake.presets["my-real-preset"] = {"status": "active", "versions": []}
    code, report, _ = run(fake, tmp_path, "--i-know-this-is-a-test-workspace")
    assert code == 0 and report is not None
    assert set(fake.presets) == {"my-real-preset", A, B}


def test_probe_presets_from_an_earlier_run_do_not_stop_it(tmp_path):
    fake = Fake()
    fake.presets[A] = {
        "status": "active",
        "versions": [{"version": 1, "config": {"model": FREE_A}, "system_prompt": None}],
    }
    code, _report, _ = run(fake, tmp_path)
    assert code == 0
    # Reused as new versions on top of the old one.
    assert fake.latest(A)["version"] > 1


@pytest.mark.parametrize("extra", [{"has_more": True}, {"next_cursor": "abc"}, {"total_count": 40}])
def test_refuses_when_the_listing_may_hide_more_presets(tmp_path, capsys, extra):
    code, _report, _ = run(Fake(list_extra=extra), tmp_path)
    assert code == 2 and "paginated" in capsys.readouterr().err


def test_a_plain_http_base_is_refused_but_a_local_one_is_allowed():
    with pytest.raises(probe.Refused):
        probe.check_base("http://example.com/api/v1")
    assert probe.check_base("http://localhost:8080/api/v1/") == "http://localhost:8080/api/v1"
    assert probe.check_base("https://openrouter.ai/api/v1/") == probe.DEFAULT_BASE


def test_http_base_exits_2_without_a_request(tmp_path):
    fake = Fake()
    code, _report, _ = run(fake, tmp_path, "--base", "http://example.com/api/v1")
    assert code == 2 and fake.calls == []


def test_a_refused_key_exits_1_and_still_writes_the_report(tmp_path, capsys):
    fake = Fake(list_status=401)
    code, report, _ = run(fake, tmp_path)
    assert code == probe.EXIT_FAILED == 1
    assert "refused the key" in capsys.readouterr().err
    assert report is not None and report["steps"][0]["status"] == 401
    assert fake.posts() == []


def test_an_unreachable_host_exits_1(tmp_path):
    code, report, _ = run(Fake(network_down=True), tmp_path)
    assert code == 1 and report is not None
    assert "ConnectError" in report["steps"][0]["observed"]


def test_only_the_two_probe_presets_are_ever_written(tmp_path):
    fake = Fake()
    code, report, _ = run(fake, tmp_path)
    assert code == 0 and report is not None
    paths = {c.path for c in fake.posts("/presets")}
    assert paths == {f"/presets/{A}/chat/completions", f"/presets/{B}/chat/completions"}
    assert set(fake.presets) == {A, B}
    assert report["counts"]["presets_created_or_updated"] == [A, B]
    assert {c.method for c in fake.calls} == {"GET", "POST"}


def make_probe(**opts: Any) -> Any:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    kwargs = {"skip_calls": False, "max_live_calls": 8, "say": lambda _t: None, **opts}
    return probe.Probe(client, probe.DEFAULT_BASE, KEY, **kwargs)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/presets/someone-elses/chat/completions", {}),
        ("POST", "/presets/vibonomics-probe-c/chat/completions", {}),
        ("DELETE", f"/presets/{A}", None),
        ("PUT", f"/presets/{A}", {}),
        ("PATCH", f"/presets/{A}", {}),
        ("POST", "/chat/completions", {"model": "openai/gpt-5", "max_tokens": 8}),
        ("POST", "/chat/completions", {"model": f"@preset/{A}x", "max_tokens": 8}),
        ("POST", "/chat/completions", {"model": f"@preset/{B}", "max_tokens": 200}),
        ("POST", "/keys", {}),
    ],
)
def test_the_guard_refuses_anything_outside_the_probe(method, path, body):
    with pytest.raises(probe.Refused):
        make_probe()._send(method, path, body)


def test_the_guard_allows_reads_probe_writes_and_capped_calls():
    p = make_probe()
    for method, path, body in [
        ("GET", "/presets", None),
        ("GET", "/models", None),
        ("POST", f"/presets/{A}/chat/completions", {"model": FREE_A}),
        ("POST", f"/presets/{B}/chat/completions", {"model": FREE_A}),
        ("POST", "/chat/completions", {"model": f"@preset/{B}", "max_tokens": 8}),
    ]:
        assert p._send(method, path, body).status == 200


def test_the_guard_refuses_a_completion_over_the_cap_or_with_skip_calls():
    p = make_probe(max_live_calls=1)
    p.live_calls = 2
    with pytest.raises(probe.Refused):
        p._send("POST", "/chat/completions", {"model": f"@preset/{B}", "max_tokens": 8})
    with pytest.raises(probe.Refused):
        make_probe(skip_calls=True)._send("POST", "/chat/completions", {"model": f"@preset/{B}", "max_tokens": 8})


# ---- live calls -----------------------------------------------------------------------------------------------


def test_a_full_run_makes_six_live_calls_each_a_tiny_one_to_a_free_model(tmp_path):
    fake = Fake()
    code, report, _ = run(fake, tmp_path)
    assert code == 0 and report is not None
    calls = fake.completions()
    assert len(calls) == report["counts"]["live_calls"] == 6
    for call in calls:
        assert call.body["max_tokens"] == 8
        assert call.body["model"] == f"@preset/{B}"
        assert len(call.body["messages"]) == 1 and call.body["messages"][0]["role"] == "user"
    prompts = {c.body["messages"][0]["content"] for c in calls}
    assert probe.LIVE_PROMPT in prompts
    # The preset behind each call held free models only at the time (checked by the probe against the public list).
    live = [s for s in report["steps"] if s["path"] == "/chat/completions"]
    assert len(live) == 6 and all(s["request_body"]["max_tokens"] == 8 for s in live)


def test_no_live_call_goes_through_a_preset_that_holds_a_paid_model(tmp_path):
    fake = Fake(model_override=PAID)
    code, report, _ = run(fake, tmp_path)
    assert code == 0 and report is not None
    assert fake.completions() == []
    refused = [s for s in report["steps"] if "refused" in s["observed"]]
    assert refused and all("not a verified free model" in s["observed"] for s in refused)
    assert report["counts"]["live_calls"] == 0


def test_a_paid_model_is_never_chosen_for_a_preset(tmp_path):
    fake = Fake()
    run(fake, tmp_path)
    for call in fake.posts("/presets"):
        assert PAID not in json.dumps(call.body)


@pytest.mark.parametrize(("cap", "skipped"), [(0, 5), (2, 3), (3, 2), (5, 1), (6, 0), (8, 0)])
def test_the_live_call_cap_is_a_hard_limit(tmp_path, cap, skipped):
    fake = Fake()
    code, report, _ = run(fake, tmp_path, "--max-live-calls", str(cap))
    assert code == 0 and report is not None
    assert len(fake.completions()) == min(cap, 6) == report["counts"]["live_calls"]
    assert len([s for s in report["steps"] if "limit of" in s["observed"]]) == skipped


def test_skip_calls_makes_no_completion_request_but_still_writes_presets(tmp_path):
    fake = Fake()
    code, report, _ = run(fake, tmp_path, "--skip-calls")
    assert code == 0 and report is not None
    assert fake.completions() == []
    assert report["counts"]["live_calls"] == 0 and report["counts"]["preset_writes"] > 0
    assert set(fake.presets) == {A}
    assert steps_named(report, "5: live routing")[0]["observed"] == "skipped: --skip-calls"
    assert answer(report, "A request's parameter") == "inconclusive"


def test_a_free_model_is_a_free_suffix_or_all_prices_zero():
    assert probe.is_free_row({"id": "x/y:free", "pricing": {"prompt": "0.5", "completion": "1"}})
    assert probe.is_free_row({"id": "x/y", "pricing": {"prompt": "0", "completion": "0", "request": "0"}})
    assert not probe.is_free_row({"id": "x/y", "pricing": {"prompt": "0", "completion": "0", "request": "0.01"}})
    assert not probe.is_free_row({"id": "x/y", "pricing": {"prompt": "-1", "completion": "-1"}})
    assert not probe.is_free_row({"id": "x/y", "pricing": {"prompt": "0"}})
    assert not probe.is_free_row({"id": "x/y"})
    assert not probe.is_free_row({"pricing": {"prompt": "0", "completion": "0"}})


def test_without_a_free_model_the_presets_are_written_and_no_live_call_is_made(tmp_path):
    fake = Fake()
    fake.models = [m for m in fake.models if m["id"] == PAID]
    code, report, _ = run(fake, tmp_path)
    assert code == 0 and report is not None
    assert fake.completions() == []
    assert steps_named(report, "5: live routing")[0]["observed"].startswith("skipped")
    # The stand-in ids the presets use are never trusted as free.
    assert fake.latest(A)["config"]["model"] in probe.FALLBACK_MODELS


def test_an_unreadable_model_list_skips_the_live_experiments_only(tmp_path):
    fake = Fake(models_status=500)
    code, report, _ = run(fake, tmp_path)
    assert code == 0 and report is not None
    assert fake.completions() == []
    assert steps_named(report, "1a: write")[0]["status"] == 200


def test_an_experiment_that_crashes_is_recorded_and_the_rest_go_on(tmp_path, monkeypatch):
    def boom(self):
        raise RuntimeError("deliberate")

    monkeypatch.setattr(probe.Probe, "exp2", boom)
    code, report, _ = run(Fake(), tmp_path)
    assert code == 0 and report is not None
    assert "RuntimeError: deliberate" in steps_named(report, "experiment 2 stopped")[0]["observed"]
    assert steps_named(report, "4b: versions") and steps_named(report, "5i: precedence")


def test_failing_writes_are_findings_not_crashes(tmp_path):
    fake = Fake(write_status=500, write_body={"error": {"message": "boom", "code": 500}})
    code, report, _ = run(fake, tmp_path)
    assert code == 0 and report is not None
    assert all(s["observed"].startswith("rejected: HTTP 500") for s in report["steps"] if s["name"].endswith(": write"))
    assert fake.completions() == []
    assert answer(report, "Which extra keys").startswith("1a: rejected: HTTP 500: boom")


# ---- secrets --------------------------------------------------------------------------------------------------


def test_scrub_takes_out_keys_bearer_tokens_and_urls_carrying_them():
    text = f"key {KEY} and {LEAK}, Authorization: Bearer abc.def-12345, https://x.test/?k=sk-or-v1-zzz&a=1, sk-or-"
    out = probe.scrub(text, KEY)
    assert probe.scrub("Missing bearer token") == "Missing bearer token"
    for secret in (KEY, LEAK, "abc.def-12345", "sk-or-v1-zzz", "sk-or-"):
        assert secret not in out
    assert out.count(probe.REDACTED) >= 4
    assert "&a=1" in out
    assert probe.scrub("plain", None) == "plain"
    assert probe.scrub("a custom-key-9999", "custom-key-9999") == f"a {probe.REDACTED}"
    assert probe.REDACTED in probe.scrub("sk-proj-abcdefghijklmnopqrstuvwxyz")


def test_a_key_in_an_error_body_reaches_neither_the_output_nor_the_report(tmp_path, capsys):
    leak = f"Invalid key {KEY}; also {LEAK}; Bearer {KEY}; see https://openrouter.ai/x?key={LEAK}"
    fake = Fake(unknown="reject", unknown_message=leak + " {key}")
    code, report, out = run(fake, tmp_path)
    captured = capsys.readouterr()
    assert code == 0 and report is not None
    text = out.read_text(encoding="utf-8")
    for secret in (KEY, LEAK, "sk-or-v1-leaked"):
        assert secret not in captured.out + captured.err
        assert secret not in text
    # The error did get through, with the key gone.
    assert probe.REDACTED in text and "Invalid key" in text
    assert any("Invalid key" in s["observed"] for s in report["steps"])


def test_a_key_in_a_success_body_and_a_failed_listing_is_scrubbed_too(tmp_path, capsys):
    fake = Fake(list_status=403)
    fake.list_extra = {"error": {"message": f"nope {KEY}"}}
    code, report, out = run(fake, tmp_path)
    captured = capsys.readouterr()
    assert code == 1 and report is not None
    assert KEY not in captured.out + captured.err + out.read_text(encoding="utf-8")


def test_the_key_goes_only_in_the_authorization_header_never_in_a_body_or_the_report(tmp_path):
    fake = Fake()
    code, report, out = run(fake, tmp_path)
    assert code == 0 and report is not None
    assert KEY not in out.read_text(encoding="utf-8")
    assert all(KEY not in json.dumps(c.body) for c in fake.calls)
    # The public reads carry no key at all.
    for call in fake.calls:
        if call.path.startswith("/models"):
            assert call.authorization is None
        else:
            assert call.authorization == f"Bearer {KEY}"
    assert "authorization" not in json.dumps(report).lower()


def test_a_character_the_console_cannot_show_does_not_stop_the_run(tmp_path, monkeypatch):
    console = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    monkeypatch.setattr(sys, "stdout", console)
    fake = Fake(unknown="reject", unknown_message="Clé inconnue → {key} \U0001f600")
    code, report, out = run(fake, tmp_path)
    assert code == 0 and report is not None
    console.flush()
    assert "inconnue" in console.buffer.getvalue().decode("ascii")  # type: ignore[attr-defined]
    # The report keeps the real characters.
    assert "Clé inconnue →" in out.read_text(encoding="utf-8")


def test_the_report_is_utf8_without_a_bom_and_with_unix_newlines(tmp_path):
    _code, _report, out = run(Fake(), tmp_path)
    raw = out.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw


# ---- the report ---------------------------------------------------------------------------------------------------


def test_every_step_has_the_fields_and_an_excerpt_of_at_most_600_characters(tmp_path):
    fake = Fake()
    fake.models[0]["description"] = "x" * 5000
    code, report, _ = run(fake, tmp_path)
    assert code == 0 and report is not None
    for s in report["steps"]:
        assert {"name", "asked", "method", "path", "request_body", "status", "response_excerpt", "observed"} <= set(s)
        assert len(s["response_excerpt"]) <= probe.EXCERPT_CHARS
        assert s["asked"] and s["observed"], s["name"]
    listing = steps_named(report, "discover: public model list")[0]
    assert len(listing["response_excerpt"]) == probe.EXCERPT_CHARS


def test_the_summary_answers_every_question_on_a_full_run(tmp_path, capsys):
    code, report, _ = run(Fake(), tmp_path)
    assert code == 0 and report is not None
    assert len(report["summary"]) == len(probe.QUESTIONS)
    assert [i["answer"] for i in report["summary"] if i["answer"] == "inconclusive"] == []
    out = capsys.readouterr().out
    for _key, question in probe.QUESTIONS:
        assert question in out
    assert "Report:" in out


# ---- experiment 1: what persists ------------------------------------------------------------------------------


def outcomes(old: dict[str, Any], new: dict[str, Any]) -> dict[str, str]:
    found, _extras = probe.compare_config(old, new)
    return {o.key: o.status for o in found}


def test_compare_config_finds_kept_renamed_changed_and_dropped_keys():
    sent = {
        "model": "m",
        "temperature": 0.3,
        "max_tokens": 64,
        "route": "sort",
        "provider": {"sort": "price", "only": ["a", "b"], "ignore": ["x"]},
    }
    stored = {
        "model": "m",
        "temperature": 0.3,
        "max_completion_tokens": 64,
        "provider": {"sort": {"by": "price", "partition": "none"}, "only": ["b", "a"]},
        "allow_fallbacks": True,
    }
    found, extras = probe.compare_config(sent, stored)
    assert {o.key: o.status for o in found} == {
        "model": "kept",
        "temperature": "kept",
        "max_tokens": "renamed",
        "route": "dropped",
        "provider.sort": "changed",
        "provider.only": "reordered",
        "provider.ignore": "dropped",
    }
    assert next(o for o in found if o.key == "max_tokens").stored_as == "max_completion_tokens"
    assert extras == {"allow_fallbacks": True}
    lines = probe.describe_outcomes(found, extras)
    assert "max_tokens: stored under the name max_completion_tokens" in lines
    assert any(line.startswith('provider.sort: changed, sent "price" and stored {') for line in lines)
    assert "route: dropped, not in the stored config" in lines
    assert lines[-1].startswith("added by OpenRouter: allow_fallbacks")


def test_compare_config_treats_numbers_and_booleans_carefully():
    assert outcomes({"a": 1}, {"a": 1.0}) == {"a": "kept"}
    assert outcomes({"a": 0}, {"a": False}) == {"a": "changed"}
    assert outcomes({"a": 0.1}, {"a": 0.1000000001}) == {"a": "kept"}
    assert outcomes({"max_completion_tokens": 5}, {"max_tokens": 5}) == {"max_completion_tokens": "renamed"}
    assert outcomes({"max_tokens": 5}, {"max_completion_tokens": 9}) == {"max_tokens": "renamed_changed"}


def test_sort_shape_tells_a_string_from_an_object():
    assert probe.sort_shape({"provider": {"sort": "price"}}) == 'string "price"'
    assert probe.sort_shape({"provider": {"sort": {"by": "price", "partition": "none"}}}).startswith("object {")
    assert probe.sort_shape({"provider": {"only": ["x"]}}) == "absent"
    assert probe.sort_shape({}) == "absent" and probe.sort_shape(None) == "absent"


def test_a_plain_preset_api_keeps_everything_under_the_names_sent(tmp_path):
    code, report, _ = run(Fake(), tmp_path)
    assert code == 0 and report is not None
    text = answer(report, "Which extra keys")
    assert "dropped" not in text and "renamed" not in text
    assert "kept:" in text and "provider.require_parameters" in text and "route" in text
    assert answer(report, "Shape of provider.sort").endswith('string "price"')
    # Both token-cap variants were sent, and each came back under its own name.
    assert "sent max_tokens, stored as max_tokens" in text
    assert "sent max_completion_tokens, stored as max_completion_tokens" in text


def test_max_tokens_stored_as_max_completion_tokens_is_reported_as_a_rename(tmp_path):
    code, report, _ = run(Fake(rename_max_tokens=True), tmp_path)
    assert code == 0 and report is not None
    text = answer(report, "Which extra keys")
    assert "sent max_tokens, stored as max_completion_tokens" in text
    assert "max_tokens -> max_completion_tokens" in text
    first_read = steps_named(report, "1a: read back")[0]
    assert "max_tokens: stored under the name max_completion_tokens" in first_read["observed"]


def test_a_sort_that_comes_back_as_an_object_is_reported_with_its_shape(tmp_path):
    code, report, _ = run(Fake(sort_object=True), tmp_path)
    assert code == 0 and report is not None
    assert answer(report, "Shape of provider.sort") == (
        'sent the string \'price\'; read back as object {"by": "price", "partition": "none"}'
    )
    assert "changed: provider.sort" in answer(report, "Which extra keys")


def test_keys_openrouter_does_not_keep_are_reported_as_dropped(tmp_path):
    code, report, _ = run(Fake(drop={"route", "provider.quantizations", "repetition_penalty"}), tmp_path)
    assert code == 0 and report is not None
    text = answer(report, "Which extra keys")
    assert re.search(r"dropped: [^|]*route", text) and "provider.quantizations" in text and "repetition_penalty" in text
    assert answer(report, "A request's parameter") != "inconclusive"  # the live experiments still ran


def test_a_key_the_write_rejects_is_left_out_and_the_next_attempt_goes_ahead(tmp_path):
    fake = Fake(reject={"route"})
    code, report, _ = run(fake, tmp_path)
    assert code == 0 and report is not None
    writes = steps_named(report, "1a: write")
    assert [w["status"] for w in writes] == [400, 200]
    assert "the message names route" in writes[0]["observed"]
    assert "without route" in writes[1]["name"]
    assert "rejected these, so later writes leave them out: route" in answer(report, "Which extra keys")
    for call in fake.posts(f"/presets/{A}")[2:]:
        assert "route" not in call.body


def test_a_peeled_provider_key_stays_in_the_body_the_report_shows_for_the_rejected_attempt(tmp_path):
    fake = Fake(reject={"provider.sort"})
    code, report, _ = run(fake, tmp_path)
    assert code == 0 and report is not None
    first, second = steps_named(report, "1a: write")[:2]
    assert first["request_body"]["provider"]["sort"] == "price"
    assert "sort" not in second["request_body"]["provider"]
    assert "provider.sort" in answer(report, "Which extra keys")


def test_peeling_stops_when_the_message_names_nothing_it_can_drop(tmp_path):
    fake = Fake(write_status=400, write_body={"error": {"message": "bad request", "code": 400}})
    code, report, _ = run(fake, tmp_path)
    assert code == 0 and report is not None
    assert len(steps_named(report, "1a: write")) == 1


def test_blamed_keys_never_name_the_model_or_a_whole_provider_block():
    cfg = {"model": "m", "models": ["m"], "temperature": 1, "provider": {"sort": "price", "only": ["x"]}}
    assert probe.blamed_keys(cfg, "model is invalid, provider is wrong") == []
    assert probe.blamed_keys(cfg, "bad temperature and sort") == ["temperature", "provider.sort"]
    assert probe.blamed_keys(cfg, "temperatures") == []


# ---- experiment 2: the system prompt ------------------------------------------------------------------------


def test_classify_system_prompt_covers_each_outcome():
    c = probe.classify_system_prompt
    assert c(None, "one", "one")[0] == "stored"
    assert c("one", "three", "three")[0] == "stored"
    assert c("one", "one", "three")[0] == "ignored"
    assert c("one", None, "three")[0] == "not_stored"
    assert c(None, None, "one")[0] == "not_stored"
    assert c("one", "one", None)[0] == "kept"
    assert c("one", None, None)[0] == "nulled"
    assert c(None, None, None)[0] == "empty"
    assert c("one", "other", None)[0] == "changed"
    assert c("one", "other", "three")[0] == "changed"
    assert c("one", "", None)[0] == "nulled"


def test_a_system_prompt_that_is_nulled_by_a_version_without_one_is_reported(tmp_path):
    code, report, _ = run(Fake(carry_system=False), tmp_path)
    assert code == 0 and report is not None
    text = answer(report, "System prompt")
    assert "first write: the system message sent was stored" in text
    assert "a version with no system message: no system message sent: system_prompt went from" in text
    assert "to null" in text
    assert "a version with a different system message: the system message sent was stored" in text
    assert not text.startswith("inconclusive")
    read = steps_named(report, "2a: read back")[0]
    assert (
        "system_prompt: no system message sent: system_prompt went from 'PROBE system prompt one' to null"
        in read["observed"]
    )


def test_a_system_prompt_that_is_kept_is_reported_as_kept(tmp_path):
    code, report, _ = run(Fake(carry_system=True), tmp_path)
    assert code == 0 and report is not None
    assert "a version with no system message: no system message sent: system_prompt was kept" in answer(
        report, "System prompt"
    )


def test_a_system_message_that_is_ignored_is_reported_as_ignored(tmp_path):
    # Stored once, then a different one sent is ignored: needs a fake that keeps the first prompt only.
    fake = Fake(carry_system=True)
    original = fake.write

    def write(slug, body):
        resp = original(slug, body)
        versions = fake.presets[slug]["versions"]
        if len(versions) > 1 and any(m.get("role") == "system" for m in body["messages"]):
            versions[-1]["system_prompt"] = versions[0]["system_prompt"]
            resp = httpx.Response(200, json={"data": {"slug": slug, "designated_version": versions[-1]}})
        return resp

    fake.write = write  # type: ignore[method-assign]
    code, report, _ = run(fake, tmp_path)
    assert code == 0 and report is not None
    assert "the system message sent was ignored: system_prompt is still 'PROBE system prompt one'" in answer(
        report, "System prompt"
    )


def test_if_no_system_prompt_is_ever_stored_carry_over_is_inconclusive(tmp_path):
    code, report, _ = run(Fake(store_system=False), tmp_path)
    assert code == 0 and report is not None
    text = answer(report, "System prompt")
    assert text.startswith("inconclusive about carry-over, because no system prompt was ever stored.")
    assert "was not stored" in text


# ---- experiment 3: unknown keys ----------------------------------------------------------------------------


def test_unknown_keys_that_are_dropped_are_reported_as_dropped(tmp_path):
    code, report, _ = run(Fake(unknown="drop"), tmp_path)
    assert code == 0 and report is not None
    text = answer(report, "Unknown keys")
    assert "probe_unknown_key was accepted and silently dropped" in text
    assert "provider.probe_unknown_nested was accepted and silently dropped" in text


def test_unknown_keys_that_are_kept_are_reported_as_kept(tmp_path):
    code, report, _ = run(Fake(unknown="keep"), tmp_path)
    assert code == 0 and report is not None
    text = answer(report, "Unknown keys")
    assert 'probe_unknown_key was accepted and kept ("probe")' in text
    assert 'provider.probe_unknown_nested was accepted and kept ("probe")' in text


def test_unknown_keys_that_are_rejected_are_reported_with_the_status(tmp_path):
    code, report, _ = run(Fake(unknown="reject"), tmp_path)
    assert code == 0 and report is not None
    text = answer(report, "Unknown keys")
    assert "probe_unknown_key was rejected: Unrecognized key: probe_unknown_key (HTTP 400)" in text
    assert "provider.probe_unknown_nested was rejected" in text
    assert steps_named(report, "3a: write")[0]["status"] == 400


def test_classify_unknown_key_without_a_read_back_is_inconclusive():
    reply = probe.Reply(200, "", {"data": {}})
    assert probe.classify_unknown_key(reply, None, "k", False)[0] == "inconclusive"
    assert probe.classify_unknown_key(reply, {"provider": {"k": 1}}, "k", True)[0] == "kept"
    assert probe.classify_unknown_key(reply, {"provider": {}}, "k", True)[0] == "dropped"


# ---- experiment 4: versions ------------------------------------------------------------------------------------


def test_versions_are_listed_with_the_designated_one_and_the_write_answers(tmp_path):
    code, report, _ = run(Fake(), tmp_path)
    assert code == 0 and report is not None
    listing = steps_named(report, "4b: versions")[0]
    assert listing["path"] == f"/presets/{A}/versions"
    versions = report["findings"]["versions"]
    assert versions["listed"] == list(range(1, len(versions["listed"]) + 1)) and len(versions["listed"]) == 6
    assert versions["designated"] == 6
    assert f"{len(versions['listed'])} versions listed: {versions['listed']}" in listing["observed"]
    assert "Designated: 6" in listing["observed"]
    assert answer(report, "Does a write").startswith("yes: every accepted write answered with the version")
    assert "designated version: 6" in steps_named(report, "4a: read the preset")[0]["observed"]


def test_a_write_that_does_not_name_its_version_is_reported_and_still_read_back(tmp_path):
    code, report, _ = run(Fake(return_version=False), tmp_path)
    assert code == 0 and report is not None
    assert answer(report, "Does a write").startswith("no: only 0 of")
    write = steps_named(report, "1a: write")[0]
    assert "names no version" in write["observed"]
    assert steps_named(report, "1a: read back")


# ---- experiment 5: what each live call showed -----------------------------------------------------------------------


def reply(status: int, body: Any) -> Any:
    return probe.Reply(status, json.dumps(body), body)


def done(provider: str | None = "Venice", tokens: int | None = 8, finish: str = "length", **extra: Any) -> Any:
    body: dict[str, Any] = {
        "choices": [{"message": {"content": "x"}, "finish_reason": finish}],
        "usage": {"completion_tokens": tokens} if tokens is not None else {},
        **extra,
    }
    if provider:
        body["provider"] = provider
    return reply(200, body)


KW = {"preset_cap": 3, "request_cap": 8, "preset_temp": 0.1, "request_temp": 1.5}


def test_precedence_from_the_length_of_the_answer():
    verdict, text = probe.classify_precedence(done(tokens=8), **KW)
    assert verdict == "request" and "temperature" in text.lower() and "cannot be observed" in text
    assert probe.classify_precedence(done(tokens=3, finish="length"), **KW)[0] == "preset"
    assert probe.classify_precedence(done(tokens=2, finish="stop"), **KW)[0] == "inconclusive"
    assert probe.classify_precedence(done(tokens=None), **KW)[0] == "inconclusive"
    assert probe.classify_precedence(reply(429, {"error": {"message": "slow down", "code": 429}}), **KW)[0] == (
        "inconclusive"
    )


def test_precedence_from_an_echoed_temperature_beats_the_stand_in():
    assert probe.classify_precedence(done(tokens=3, temperature=1.5), **KW)[0] == "request"
    assert probe.classify_precedence(done(tokens=8, temperature=0.1), **KW)[0] == "preset"
    verdict, text = probe.classify_precedence(done(tokens=8, parameters={"top_p": 1}), **KW)
    assert verdict == "request" and '"parameters"' in text


def endpoint(name: str, tag: str) -> Any:
    return probe.Endpoint(name, tag, "fp8", frozenset({"repetition_penalty"}), True, True)


def test_provider_merge_is_read_from_who_served():
    pinned, other = endpoint("Venice", "venice/fp8"), endpoint("Chutes", "chutes/bf16")
    assert probe.classify_merge(done("Venice"), pinned, other)[0] == "merge"
    assert probe.classify_merge(done("Chutes"), pinned, other)[0] == "replace"
    assert probe.classify_merge(done("Somebody Else"), pinned, other)[0] == "replace"
    assert probe.classify_merge(done(None), pinned, other)[0] == "inconclusive"
    assert probe.classify_merge(reply(404, {"error": {"message": "no", "code": 404}}), pinned, other)[0] == (
        "inconclusive"
    )
    # With one provider only, serving by the pinned one proves nothing.
    assert probe.classify_merge(done("Venice"), pinned, None)[0] == "inconclusive"
    assert probe.classify_merge(done("Chutes"), pinned, None)[0] == "replace"


def test_unknown_quantization_excluded_or_passed():
    err = reply(404, {"error": {"message": "No endpoints found matching quantization", "code": 404}})
    verdict, text = probe.classify_quantization(err, "unknown")
    assert verdict == "excluded" and "HTTP 404" in text and "No endpoints found" in text and "unknown" in text
    assert probe.classify_quantization(done("Stealth"), "unknown")[0] == "passes"
    assert probe.classify_quantization(probe.Reply(None, error="ConnectError: x"), None)[0] == "inconclusive"


def test_require_parameters_reports_the_error_shape_and_the_message():
    err = reply(
        404, {"error": {"message": "No endpoints found that can handle the requested parameters.", "code": 404}}
    )
    verdict, text = probe.classify_require_parameters(err, True)
    assert verdict == "error"
    assert "HTTP 404, error object with keys code, message" in text
    assert "message: No endpoints found that can handle the requested parameters." in text
    assert probe.classify_require_parameters(done("Acme"), False)[0] == "dropped"
    assert "inferred" in probe.classify_require_parameters(done("Acme"), False)[1]
    assert probe.classify_require_parameters(done("Acme"), True)[0] == "served"
    assert probe.classify_require_parameters(probe.Reply(None, error="Timeout"), True)[0] == "inconclusive"


def test_error_text_and_shape_for_odd_bodies():
    assert probe.error_text(probe.Reply(500, "gateway down", None)) == "gateway down"
    assert probe.error_text(reply(400, {"error": "plain string"})) == "plain string"
    assert probe.error_text(reply(400, {"message": "top level"})) == "top level"
    assert probe.error_text(
        reply(400, {"error": {"message": "m", "code": 7, "metadata": {"raw": "upstream said no"}}})
    ) == ("code 7: m (upstream: upstream said no)")
    assert probe.error_shape(probe.Reply(500, "x", None)) == "body is not a JSON object"
    assert probe.error_shape(reply(400, {"error": "s"})) == "error as a str"
    assert probe.error_shape(reply(400, {"detail": "s"})) == "no error key; body keys detail"


def test_the_live_experiments_report_what_the_fake_routing_does(tmp_path):
    code, report, _ = run(Fake(), tmp_path)
    assert code == 0 and report is not None
    assert answer(report, "A request's parameter").startswith("request:")
    merge = answer(report, "A request's provider block")
    assert merge.startswith("merge (by who served): HTTP 200: served by Venice")
    assert "| merge (through require_parameters): HTTP 404: still refused" in merge
    assert answer(report, "provider.quantizations").startswith("excluded: HTTP 404")
    text = answer(report, "require_parameters")
    assert "with true: error: HTTP 404, error object with keys code, message, metadata" in text
    assert "with false: dropped: HTTP 200" in text
    # The exact error body is in the step, scrubbed.
    step = steps_named(report, "5iv: require_parameters true")[0]
    assert (
        step["status"] == 404
        and "No endpoints found that can handle the requested parameters" in step["response_excerpt"]
    )
    assert step["request_body"]["model"] == f"@preset/{B}"


def test_the_live_experiments_report_the_opposite_behaviour_too(tmp_path):
    fake = Fake(request_wins=False, provider_replace=True, quant_excluded=False)
    code, report, _ = run(fake, tmp_path)
    assert code == 0 and report is not None
    assert answer(report, "A request's parameter").startswith("preset:")
    merge = answer(report, "A request's provider block")
    assert merge.startswith("replace (by who served): HTTP 200: served by Chutes")
    assert "| replace (through require_parameters): HTTP 200: served." in merge
    assert answer(report, "provider.quantizations").startswith("passes: HTTP 200")


def test_with_a_single_provider_only_the_require_parameters_test_can_tell_merge_from_replace(tmp_path):
    for replace, expected in ((False, "merge"), (True, "replace")):
        fake = Fake(provider_replace=replace)
        fake.endpoints[FREE_A] = fake.endpoints[FREE_A][:1]
        code, report, _ = run(fake, tmp_path)
        assert code == 0 and report is not None
        text = answer(report, "A request's provider block")
        assert text.startswith("inconclusive (by who served)") or text.startswith("replace (by who served)")
        assert f"| {expected} (through require_parameters)" in text


def test_the_merge_through_require_parameters_needs_the_preset_to_be_refused_on_its_own():
    ok = reply(200, {"provider": "Acme"})
    refused = reply(404, {"error": {"message": "No endpoints found", "code": 404}})
    assert probe.classify_merge_by_requirement(ok, True)[0] == "replace"
    assert probe.classify_merge_by_requirement(refused, True)[0] == "merge"
    assert probe.classify_merge_by_requirement(ok, False)[0] == "inconclusive"
    assert probe.classify_merge_by_requirement(refused, False)[0] == "inconclusive"
    assert probe.classify_merge_by_requirement(reply(429, {"error": {"message": "slow", "code": 429}}), True)[0] == (
        "inconclusive"
    )
    assert probe.classify_merge_by_requirement(probe.Reply(None, error="Timeout"), True)[0] == "inconclusive"


def test_the_merge_call_orders_a_provider_other_than_the_pinned_one(tmp_path):
    fake = Fake()
    run(fake, tmp_path)
    merge = next(c for c in fake.completions() if c.body.get("provider"))
    assert merge.body["provider"] == {"order": ["chutes/bf16"]}
    assert fake.latest(B)["config"]["model"] in {FREE_A, FREE_B, STEALTH}


def test_the_precedence_call_asks_for_a_higher_temperature_than_the_preset_holds(tmp_path):
    fake = Fake()
    run(fake, tmp_path)
    first = fake.completions()[0]
    assert first.body["temperature"] == 1.5 and first.body["max_tokens"] == 8
    setup = next(c for c in fake.posts(f"/presets/{B}") if c.body.get("temperature") == 0.1)
    assert setup.body["max_tokens"] == 3


def test_a_key_openrouter_does_not_keep_is_flagged_next_to_the_call_that_depends_on_it(tmp_path):
    fake = Fake(drop={"provider.require_parameters"})
    code, report, _ = run(fake, tmp_path)
    assert code == 0 and report is not None
    setup = steps_named(report, "5iv setup: read back")[0]
    assert "does not test it: provider.require_parameters" in setup["observed"]


def test_without_the_stealth_model_only_that_experiment_is_skipped(tmp_path):
    fake = Fake()
    fake.models = [m for m in fake.models if m["id"] != STEALTH]
    code, report, _ = run(fake, tmp_path)
    assert code == 0 and report is not None
    assert "skipped" in steps_named(report, "5iii")[0]["observed"]
    assert answer(report, "provider.quantizations") == "inconclusive"
    assert len(fake.completions()) == 5


def test_a_stealth_model_that_is_not_free_gets_no_call(tmp_path):
    fake = Fake()
    next(m for m in fake.models if m["id"] == STEALTH)["pricing"] = {"prompt": "0.000001", "completion": "0.000001"}
    code, report, _ = run(fake, tmp_path)
    assert code == 0 and report is not None
    assert "not free" in steps_named(report, "5iii")[0]["observed"]
    assert len(fake.completions()) == 5


def test_a_stealth_model_with_a_priced_endpoint_gets_no_call(tmp_path):
    fake = Fake()
    fake.endpoints[STEALTH][0]["pricing"] = {"prompt": "0.000002", "completion": "0.000002"}
    code, report, _ = run(fake, tmp_path)
    assert code == 0 and report is not None
    assert "an endpoint of it is priced" in steps_named(report, "5iii")[0]["observed"]
    assert len(fake.completions()) == 5


def test_a_model_with_a_priced_endpoint_is_never_used_for_a_live_call(tmp_path):
    # An unpinned call could land on any endpoint a model lists, so one priced endpoint rules the model out.
    fake = Fake()
    fake.endpoints[FREE_A][0]["pricing"] = {"prompt": "0.000002", "completion": "0.000002"}
    code, report, _ = run(fake, tmp_path)
    assert code == 0 and report is not None
    for call in fake.posts(f"/presets/{B}"):
        assert call.body["model"] != FREE_A
    assert "skipped" in steps_named(report, "5ii")[0]["observed"]
    assert answer(report, "A request's provider block").startswith("merge (through require_parameters)")


def test_a_live_call_that_gets_no_answer_is_recorded_as_a_finding(tmp_path):
    fake = Fake()

    def hang(body):
        raise httpx.ReadTimeout("slow")

    fake.complete = hang  # type: ignore[method-assign]
    code, report, _ = run(fake, tmp_path)
    assert code == 0 and report is not None
    live = [s for s in report["steps"] if s["path"] == "/chat/completions"]
    assert live and all(s["status"] is None and "ReadTimeout" in s["observed"] for s in live)
    assert answer(report, "A request's parameter").startswith("inconclusive: the call failed (no HTTP answer)")


def test_main_returns_an_int_for_help_and_bad_arguments(capsys):
    assert probe.main(["--help"], env={}) == 0
    assert probe.main(["--max-live-calls", "many"], env={probe.KEY_ENV: KEY}) == 2
    assert "--i-know-this-is-a-test-workspace" in capsys.readouterr().out
    assert probe.main(["--max-live-calls", "-1"], env={probe.KEY_ENV: KEY}) == 2


def test_the_key_comes_from_the_environment_only():
    parser = probe.build_parser()
    options = {opt for action in parser._actions for opt in action.option_strings}
    assert not {o for o in options if "key" in o and o != "--i-know-this-is-a-test-workspace"}
    assert probe.KEY_ENV == "OPENROUTER_API_KEY"


def test_listing_incomplete_hints():
    rows = [{"slug": "x"}]
    assert not probe.listing_incomplete({"data": rows}, rows)
    assert not probe.listing_incomplete({"data": rows, "total_count": 1, "has_more": False}, rows)
    assert probe.listing_incomplete({"data": rows, "hasMore": True}, rows)
    assert probe.listing_incomplete({"data": rows, "total": 2}, rows)
    assert not probe.listing_incomplete([], rows)
