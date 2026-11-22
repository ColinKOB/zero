""




































import json
import os
from dataclasses import dataclass, field
from pathlib import Path

LEAD = "default"
JOBS = ("quick", "everyday", "big")





COMPACT_AT = 200_000
SUMMARIZER = "haiku"
HEADROOM = 172_000










EFFORTS = ("low", "medium", "high", "xhigh", "max")









EXECUTION_GUIDANCE = False


@dataclass(frozen=True)
class Model:
    key: str
    name: str
    plan: str
    id: str
    tier: int
    suits: tuple[str, ...]



    thinking_off: dict | None = None


    window: int = 0
    long_context: bool = False
    effort: str | None = None
    summarizer: str = SUMMARIZER
    efforts: tuple[str, ...] = EFFORTS
    base_effort: str = "medium"



    compact_tokens: int = 0
    prices: dict | None = None

    @property
    def provider(self) -> str:
        return plan_of(self.plan).provider

    @property
    def keyed(self) -> bool:
        ""
        return self.plan in KEYED

    @property
    def pins_window(self) -> bool:
        return self.long_context or bool(self.compact_tokens)

    @property
    def compact_at(self) -> int:
        ""
        if self.compact_tokens:
            return self.compact_tokens
        return self.window - HEADROOM if self.long_context else COMPACT_AT

    def cost(self, uncached: int, cached: int, out: int) -> float | None:
        ""

        if not self.prices:
            return None
        hi = int(uncached) + int(cached) > self.prices["tier_at"]
        p = {k: v[1 if hi else 0] for k, v in self.prices.items() if k != "tier_at"}
        return (int(uncached) * p["in"] + int(cached) * p["cached"] + int(out) * p["out"]) / 1e6

    @property
    def default_effort(self) -> str:
        ""
        return self.effort or self.base_effort


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    provider: str
    sign_in: str

    recommended: dict = field(default_factory=dict)
    backup: str = ""
    helper: str = ""
    company: str = ""
    key_env: str = ""








PLANS: dict[str, Plan] = {p.key: p for p in (
    Plan("claude", "Claude", "claude-subscription-directsdk-experimental", "claude",
         {"quick": "haiku", "everyday": "sonnet", "big": "opus", "lead": "big"}, backup="opus", helper="haiku",
         company="Anthropic"),
    Plan("chatgpt", "ChatGPT", "openai-codex", "codex",
         {"quick": "luna", "everyday": "sol", "big": "astra", "lead": "everyday"}, backup="sol", helper="luna",
         company="OpenAI"),
)}






KEYED: dict[str, Plan] = {p.key: p for p in (


    Plan("meta", "Meta", "meta-ai", "key", company="Meta", key_env="MODEL_API_KEY"),
)}


def plan_of(key: str) -> Plan:
    return PLANS.get(key) or KEYED[key]


















CATALOG: dict[str, Model] = {m.key: m for m in (
    Model("haiku", "Haiku", "claude", "claude-haiku-5-5", 0, ("quick", "everyday"), {"type": "disabled"}, 200_000),
    Model("sonnet", "Sonnet", "claude", "claude-sonnet-5-5", 1, JOBS, {"type": "between_tools"}, 1_000_000),
    Model("opus", "Opus", "claude", "opus", 2, JOBS, None, 1_000_000),
    Model("luna", "GPT-6 Luna", "chatgpt", "gpt-6-luna", 0, ("quick", "everyday"), None, 272_000),
    Model("sol", "GPT-6.1 Sol", "chatgpt", "gpt-6.1-sol", 1, JOBS, None, 872_000, True, "high", "sol"),
    Model("terra", "GPT-6.1 Terra", "chatgpt", "gpt-6.1-terra", 1, ("everyday", "big"), None, 872_000, True, "high",
          "sol", ("low", "medium", "high", "xhigh")),
    Model("astra", "GPT-6 Astra", "chatgpt", "gpt-6-astra", 2, JOBS, None, 872_000, True, "high", "sol"),








    Model("ember", "Muse Ember", "meta", "muse-ember-1.0", 0, (), None, 1_048_576, False, "high", "ember",
          ("low", "medium", "high", "xhigh"), compact_tokens=160_000,
          prices={"tier_at": 200_000, "in": (0.10, 0.30), "cached": (0.004, 0.012), "out": (0.50, 1.50)}),
)}
BY_ID = {m.id: m for m in CATALOG.values()} | {"claude-opus-5-5": CATALOG["opus"]}


REASONING = {m.id: m.effort for m in CATALOG.values() if m.effort and not m.keyed}
PROVIDERS = {p.key: p.provider for p in PLANS.values()}
KEYED_PROVIDERS = {p.provider: p for p in KEYED.values()}


HELPER = {p.key: CATALOG[p.helper] for p in PLANS.values()}


@dataclass(frozen=True)
class Setup:
    use: tuple[str, ...]
    pick: str
    jobs: dict[str, str]
    zips: dict[str, str]
    lead_job: str
    efforts: dict[str, str] = field(default_factory=dict)


def plans_in(raw_use) -> tuple[str, ...]:
    ""
    given = [raw_use] if isinstance(raw_use, str) else raw_use if isinstance(raw_use, list) else []
    return tuple(k for k in PLANS if k in given)


def allowed(use) -> list[Model]:
    return [m for m in CATALOG.values() if m.plan in use]


def setup(raw) -> Setup | None:
    ""

    use = plans_in(raw.get("use")) if isinstance(raw, dict) else ()
    if not use:
        return None
    ok = {m.key for m in allowed(use)}
    rec = PLANS[use[0]].recommended
    given = raw.get("jobs") if isinstance(raw.get("jobs"), dict) else {}
    jobs = {j: (given.get(j) if given.get(j) in ok and j in CATALOG[given[j]].suits else rec[j]) for j in JOBS}
    jobs["lead"] = jobs[rec["lead"]]


    asked = raw.get("pick")
    pick = asked if asked in ok else "auto" if asked in (None, "", "auto") else jobs["everyday"]
    pins = raw.get("zips") if isinstance(raw.get("zips"), dict) else {}


    zips = {str(z): k for z, k in pins.items() if k in ok or (k in CATALOG and CATALOG[k].keyed)}
    levels = raw.get("efforts") if isinstance(raw.get("efforts"), dict) else {}
    efforts = {str(z): e for z, e in levels.items() if e in EFFORTS}
    return Setup(use=use, pick=pick, jobs=jobs, zips=zips, lead_job=rec["lead"], efforts=efforts)


def resolve(s: Setup | None, zip_id: str, job: str) -> Model | None:
    ""


    if job not in JOBS:
        raise ValueError(f"job {job!r}: one of {', '.join(JOBS)}")
    if s is None:
        return HELPER["claude"] if job == "quick" else None
    if s.pick != "auto":
        return CATALOG[s.pick]
    if job != "quick" and zip_id in s.zips:
        return CATALOG[s.zips[zip_id]]
    if job == "everyday" and zip_id == LEAD:
        return CATALOG[s.jobs["lead"]]
    return CATALOG[s.jobs[job]]


def chosen_effort(s: Setup | None, zip_id: str) -> str | None:
    ""
    m = resolve(s, zip_id, "everyday")
    chosen = s.efforts.get(zip_id) if s is not None else None
    return chosen if m is not None and chosen in m.efforts else None


def effort(s: Setup | None, zip_id: str) -> str | None:
    ""

    m = resolve(s, zip_id, "everyday")
    return None if m is None else chosen_effort(s, zip_id) or m.default_effort


def pick(raw, zip_id: str, job: str) -> Model | None:
    ""
    return resolve(setup(raw), zip_id, job)


def backup(s: Setup | None, provider: str) -> list[dict]:
    ""

    if provider in KEYED_PROVIDERS:
        return []
    own = next((p.key for p in PLANS.values() if p.provider == provider), None)
    others = [k for k in (s.use if s is not None else tuple(PLANS)) if k != own]
    if not others:
        return []
    m = CATALOG[PLANS[others[0]].backup]
    return [{"provider": m.provider, "model": m.id}]


def chatgpt_route(model: dict) -> dict:
    ""


    if model.get("provider") != "openai-codex":
        return model
    try:
        plan = json.loads(Path(os.environ["ZERO_HOME"]).read_text())["plan"]["chatgpt"]
    except (KeyError, OSError, ValueError, TypeError):
        return model
    if plan.get("runtime") != "codex_app_server" or not plan.get("codex_command"):
        return model
    return {**model, "provider": "openai", "openai_runtime": "codex_app_server", "codex_bin": plan["codex_command"]}


def hermes_block(s: Setup | None, zip_id: str) -> dict | None:
    ""




    m = resolve(s, zip_id, "everyday")
    if m is None:
        return None
    chosen = chosen_effort(s, zip_id)
    summary = CATALOG[m.summarizer]





    window = lambda x: {"context_length": x.window} if x.pins_window else {}











    rescue = backup(s, summary.provider)
    return {"model": chatgpt_route({"provider": m.provider, "default": m.id, **window(m)}),
            "fallback_providers": backup(s, m.provider),
            "agent.reasoning_overrides": {**REASONING, **({m.id: m.effort} if m.keyed and m.effort else {}),
                                          **({m.id: chosen} if chosen else {})},
            "agent.execution_guidance": EXECUTION_GUIDANCE,
            "compression.threshold_tokens": m.compact_at,
            "auxiliary.compression": {"provider": summary.provider, "model": summary.id, **window(summary),
                                      **({"fallback_chain": rescue} if rescue else {})}}


def company_of(m: Model | None) -> str | None:
    ""
    return plan_of(m.plan).company if m is not None else None


def company(s: Setup | None, zip_id: str, configured: str | None = None) -> str | None:
    ""




    m = resolve(s, zip_id, "everyday") or (BY_ID.get(configured) if configured else None)
    return company_of(m)


def same_company(s: Setup | None, a: str, b: str, configured=None) -> bool:
    ""



    ca = company(s, a, configured(a) if configured else None)
    cb = company(s, b, configured(b) if configured else None)
    return ca is None or cb is None or ca == cb


def recommended(use) -> dict:
    ""
    return {"use": list(plans_in(use)), "pick": "auto", "jobs": {}, "zips": {}}


def use_for(signed_in: dict) -> list[str]:
    ""
    return [k for k in PLANS if signed_in.get(k)]
