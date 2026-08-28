import pathlib

p = pathlib.Path("src/chat/agent.py")
s = p.read_text(encoding="utf-8")

helper = '''def what_i_can_do() -> str:
    """A deterministic answer for when the model produced nothing usable.

    "I am not sure what you need" is a dead end, and it is what the operator got
    after asking the agent to test the browser and report some hate speech — a
    compound request the model routed to `reply` and then left empty. Two model
    attempts had already failed at that point, so a third would not help; what the
    operator needs is the list of things they can actually ask for.

    Built from the registry rather than written out, so an action added tomorrow
    appears here without anyone remembering to update a sentence.
    """
    lines = [
        "I could not tell which of these you wanted — ask me for one directly:",
        "",
    ]
    for name, action in ACTIONS.items():
        mark = "  (asks you to confirm first)" if action.mutating else ""
        lines.append(f"  • {name} — {action.description}{mark}")
    lines.append("")
    lines.append(
        'For example: "classify this text: ..." or "test the browser" or '
        '"stats for the last 30 days".'
    )
    return "\\n".join(lines)


'''

anchor = "class ChatAgent:"
assert s.count(anchor) == 1
s = s.replace(anchor, helper + anchor, 1)

s = s.replace(
    "from src.chat.tools import ACTIONS, ActionError, catalogue, run_action",
    "from src.chat.tools import ACTIONS, ActionError, catalogue, run_action",
    1,
)

# The two places a shrug could reach the operator.
before = s
s = s.replace(
    '            text = decision.message or await self._plain_reply(history, message)',
    '            text = decision.message or await self._plain_reply(history, message)',
    1,
)
s = s.replace(
    '        return answer.message.strip() or "I am not sure what you need."',
    '        return answer.message.strip() or what_i_can_do()',
    1,
)
s = s.replace(
    '            text = decision.message or "I am not sure what you need."',
    '            text = decision.message or what_i_can_do()',
    1,
)
assert s != before, "no fallback text replaced"

p.write_text(s, encoding="utf-8")

import ast

ast.parse(s)
assert "I am not sure what you need" not in s.replace('"I am not sure what you need" is a dead end', "")
print("a failed turn now answers with what the agent can do")
