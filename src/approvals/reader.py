"""A fresh reader for an approval card, before it is posted."""
import os
import re
import subprocess
import tempfile
from pathlib import Path

def lines(v, pad="  "):
    if isinstance(v, list): return "\n".join(pad + "- " + str(x) for x in v)
    return str(v)

def card_text(c):
    L = [f"[{c.get('type', '')} card]", f"TITLE: {c.get('title')}"]
    if c.get("part_of"): L.append(f"PART OF: {c['part_of']}")
    if c.get("thread"): L.append("(opens with his own Slack message that it answers)")
    if c.get("why"): L.append(f"WHY: {c['why']}")
    if c.get("changes"): L.append(f"WHAT CHANGED IN THIS VERSION: {c['changes']}")
    for f in c.get("facts") or []:
        if isinstance(f, (list, tuple)) and len(f) >= 2: L.append(f"{f[0]}: {lines(f[1])}")
        else: L.append(str(f))
    for q in c.get("quotes") or []:
        L.append(f"SHOWN WORD FOR WORD ({q.get('label') or q.get('who') or ''}):\n{q.get('text')}" + (f"\n  (note: {q['note']})" if q.get("note") else ""))
    t = c.get("table")
    if t:
        L.append("TABLE: " + " | ".join(str(x) for x in t.get("columns", [])))
        for r in t.get("rows", []): L.append("  " + " | ".join(str(x) for x in r))
    for o in c.get("options") or []:
        L.append(f"CHOICE BUTTON: {o.get('name')}" + (" (the writer's pick)" if o.get("name") == c.get("pick") else "")
                 + (f"\n  (line above the buttons: {o['title']})" if o.get("title") else ""))
    for j, p in enumerate(c.get("parts") or [], 1):
        L.append(f"ROW {j}: {p.get('title')}" + (f" ({p['note']})" if p.get("note") else ""))
        for q in p.get("quotes") or []:
            L.append(f"    SHOWN WORD FOR WORD ({q.get('label') or q.get('who') or ''}):\n{q.get('text')}" + (f"\n      (note: {q['note']})" if q.get("note") else ""))
        for f in p.get("facts") or []: L.append(f"    {f[0]}: {lines(f[1], '      ')}" if isinstance(f, (list, tuple)) and len(f) >= 2 else "    " + str(f))
        for o in p.get("options") or []:
            L.append(f"    CHOICE BUTTON: {o.get('name')}" + (" (the writer's pick)" if o.get("name") == p.get("pick") else "")
                     + (f" ({o['title']})" if o.get("title") else ""))
    for s in c.get("sections") or []:
        section_lines = []
        for line in s.get("lines") or []:
            if isinstance(line, dict):
                section_lines.append(line["text"] + (f" · {line['tag']}" if line.get("tag") else ""))
            elif isinstance(line, list):
                section_lines.append(f"{line[0]}: {line[1]}")
            else:
                section_lines.append(line)
        L.append(f"SECTION {s.get('title')}:\n" + lines(section_lines))
    for g in c.get("gallery") or []: L.append(f"PICTURE: {g.get('label') or g.get('caption') or ''}")
    if c.get("image"): L.append("PICTURE (one image)")
    if c.get("video"): L.append("VIDEO (plays on the card)")
    if c.get("if_no_answer"): L.append(f"IF NO ANSWER: {c['if_no_answer']}")
    L.append(f"A YES COVERS: {lines(c.get('approves'))}")
    L.append(f"A YES DOES NOT COVER: {lines(c.get('does_not_approve'))}")
    for k in ("yes_label", "no_label"):
        if c.get(k): L.append(f"BUTTON: {c[k]}")
    return "\n".join(L)


ASK_TEXT = """Read the card once, top to bottom, as him. Then print exactly this, and nothing else:

HE WOULD STOP AT:
1. "<the exact words from the card>": <what he can't tell from the card>
(one numbered line for every place he would have to stop and work something out, in the order he meets them; the single word "nowhere" if there is none)
WHAT HE'D THINK YOU ARE ASKING: <one sentence in words he would use, or "can't tell">
WHAT HE'D THINK A YES MAKES HAPPEN: <one sentence, or "can't tell">

A stop is: a name or phrase the writer made up, or that only makes sense inside the writer's work; an approval, decision, card number or earlier message he is expected to remember, with no statement of what it was; a sentence where he can't tell who does what, or what is true now; a button whose words don't say what tapping it makes happen, or a choice called by a key (A, B, 2) instead of what it does; a plain-word stand-in where the real engineering or poker term would tell him more. Small roughness he would read past is not a stop. Don't suggest rewrites."""

def brief(card, settings):
    name = settings["approver_name"]
    text = (f"You are reading one approval card exactly as its approver, {name}, will: once, cold, on a phone, "
            "between other work. You have none of the context of the agent that wrote it, on purpose. "
            "Use no tools; answer only from what is below.\n\n")
    if settings.get("reader_rules") is not None:
        path, sep, heading = settings["reader_rules"].partition("#")
        path = Path(path).expanduser()
        try:
            rules = path.read_text()
        except OSError as e:
            raise ValueError(f"reader rules {path}: {e}") from e
        if sep:
            lines = rules.splitlines(keepends=True)
            for i, line in enumerate(lines):
                match = re.match(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", line.rstrip("\r\n"))
                if match and match.group(2).strip() == heading:
                    level = len(match.group(1))
                    end = i + 1
                    while end < len(lines):
                        next_heading = re.match(r"^(#{1,6})[ \t]+", lines[end])
                        if next_heading and len(next_heading.group(1)) <= level:
                            break
                        end += 1
                    rules = "".join(lines[i:end])
                    break
            else:
                raise ValueError(f"reader rules {path}: heading {heading!r} not found")
        text += f"Who he is and how he reads, in his own instructions to his agents:\n\n<how_he_reads>\n{rules.rstrip()}\n</how_he_reads>\n\n"
    if settings.get("reader_knows") is not None:
        text += f"What he already knows, so never count it: {settings['reader_knows']} "
    text += ("He does not know anything that happened inside the writer's work, and he does not remember approval cards by their numbers.\n\n"
             "The card may show pictures or a video; you can't see them, so never count a missing picture against it.\n\n"
             f"<card>\n{card_text(card)}\n</card>\n\n" + ASK_TEXT)
    return text


def run(brief_text, reader):
    env = os.environ.copy()
    if reader == "claude":
        cmd = ["claude", "-p", "--model", "opus", "--effort", "medium", "--tools", "",
               "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
               "--permission-mode", "manual", "--permission-prompts", "none"]
        env.pop("CLAUDE_CODE_REMOTE_MEMORY_DIR", None)
    else:
        cmd = ["codex", "exec", "--skip-git-repo-check", "-m", "gpt-6.1-terra",
               "-c", 'model_reasoning_effort="xhigh"', "-"]
    with tempfile.TemporaryDirectory() as directory:
        return subprocess.run(cmd, input=brief_text, text=True, capture_output=True, cwd=directory, env=env, timeout=300)
