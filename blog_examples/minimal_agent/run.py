# run_minimal.py — A tick-based autonomous agent in ~50 lines
import anthropic
import json
import time
from pathlib import Path

DATA = Path("~/.my_agent").expanduser()
DATA.mkdir(exist_ok=True)
MEMORY = DATA / "memories.json"
TRACE  = DATA / "session.trace.jsonl"

def load_memories():
    if MEMORY.exists():
        return json.loads(MEMORY.read_text())
    return []

def save_memory(content: str, category: str = "general"):
    mems = load_memories()
    mems.append({"ts": time.time(), "category": category, "content": content})
    MEMORY.write_text(json.dumps(mems[-50:], indent=2))

def log_event(event_type: str, summary: str, **kw):
    event = {"ts": time.time(), "event_type": event_type, "summary": summary, **kw}
    with open(TRACE, "a") as f:
        f.write(json.dumps(event) + "\n")

def tick(client, goal: str, tick_num: int):
    memories = load_memories()
    memory_text = "\n".join(f"- {m['content']}" for m in memories[-10:])
    log_event("mmkr:tick_start", f"Tick {tick_num} start", tick=tick_num)
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": f"""You are an autonomous agent. Goal: {goal}
Tick: {tick_num}
Recent memories:
{memory_text or "(none yet)"}

Think. Decide one action. Output JSON:
{{"action": "...", "memory": "...", "done": false}}"""
        }]
    )
    text = response.content[0].text.strip()
    try:
        result = json.loads(text[text.find("{"):text.rfind("}")+1])
    except Exception:
        result = {"action": text[:200], "memory": text[:100], "done": False}
    print(f"  [{tick_num}] {result.get('action', '?')}")
    save_memory(result.get("memory", result.get("action", "")))
    log_event("mmkr:tick_end", result.get("action", ""), tick=tick_num)
    return result.get("done", False)

if __name__ == "__main__":
    import os
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    goal = os.environ.get("AGENT_GOAL", "Learn something new each tick.")
    for i in range(1, 11):
        done = tick(client, goal, i)
        if done:
            break
        time.sleep(1)
