from __future__ import annotations

import json
import subprocess
from pathlib import Path

STORE = Path(__file__).resolve().parents[3] / "public/js/core/store.js"


def run_store(source: str) -> dict:
    script = f"""
      const listeners = {{}};
      const location = {{ href: "http://app.local/", hash: "" }};
      const stack = [];
      let index = -1;
      function applyUrl(url) {{
        if (!url) return;
        const pos = String(url).indexOf("#");
        location.hash = pos >= 0 ? String(url).slice(pos) : "";
        location.href = "http://app.local/" + location.hash;
      }}
      function move(delta) {{
        const next = index + delta;
        if (next < 0 || next >= stack.length) return;
        index = next;
        const entry = stack[index];
        window.history.state = entry.state;
        applyUrl(entry.url);
        listeners.popstate?.({{ state: entry.state }});
      }}
      globalThis.window = {{
        location,
        addEventListener: (type, fn) => {{ listeners[type] = fn; }},
        history: {{
          state: null,
          pushState(state, title, url) {{
            stack.splice(index + 1); stack.push({{ state, url }}); index = stack.length - 1;
            this.state = state; applyUrl(url);
          }},
          replaceState(state, title, url) {{
            if (index < 0) stack.push({{ state, url }}); else stack[index] = {{ state, url }};
            index = Math.max(index, 0); this.state = state; applyUrl(url);
          }},
          back: () => move(-1),
          forward: () => move(1),
          stackLength: () => stack.length,
        }},
      }};
      {source}
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script], check=True, text=True, capture_output=True
    )
    return json.loads(result.stdout)


def test_pushstate_popstate_and_deep_link_behavior() -> None:
    data = run_store(
        f"""
        const store = await import({json.dumps(STORE.as_uri())});
        store.setState("builderEditAgentId", "marketing.scout.starbridge_researcher");
        store.setState("view", "agents/builder");
        const builder = {{ hash: window.location.hash, state: window.history.state }};
        store.setState("view", "operations");
        store.setState("view", "agents");
        store.setState("view", "pipelines");
        const before = window.history.stackLength();
        window.history.back();
        const backOne = store.getState("view");
        window.history.back();
        const backTwo = store.getState("view");
        window.history.forward();
        const forwardOne = store.getState("view");
        console.log(JSON.stringify({{
          builder, before, after: window.history.stackLength(), backOne, backTwo,
          forwardOne, hash: window.location.hash,
        }}));
        """
    )

    assert data["builder"] == {
        "hash": "#/agents%2Fbuilder",
        "state": {
            "view": "agents/builder",
            "builderEditAgentId": "marketing.scout.starbridge_researcher",
        },
    }
    assert data["before"] == data["after"]
    assert (data["backOne"], data["backTwo"], data["forwardOne"]) == (
        "agents",
        "operations",
        "agents",
    )
    assert data["hash"] == "#/agents"


def test_initial_hash_deep_link_sets_view_on_import() -> None:
    data = run_store(
        f"""
        window.location.hash = "#/pipelines";
        window.location.href = "http://app.local/#/pipelines";
        const store = await import({json.dumps(STORE.as_uri())});
        console.log(JSON.stringify({{
          view: store.getState("view"),
          state: window.history.state,
          hash: window.location.hash,
        }}));
        """
    )

    assert data == {"view": "pipelines", "state": {"view": "pipelines"}, "hash": "#/pipelines"}
