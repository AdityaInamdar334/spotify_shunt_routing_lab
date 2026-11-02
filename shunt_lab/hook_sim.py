"""Educational approximation of Spotify's two routing hooks, not CLI-integrated."""
import json
from pathlib import Path
import re
import shlex


def inspect_read(input_json: dict, file_text: str | None, min_lines: int = 350) -> str:
    tool = input_json.get('tool_input', {})
    if tool.get('offset') is not None or tool.get('limit') is not None:
        return 'allow'
    if file_text is None:
        return 'allow'
    return 'block' if len(file_text.splitlines()) > min_lines else 'allow'


def inspect_bash(command: str, line_lookup, min_lines: int = 350) -> str:
    """Mimic common decisions, not a robust shell security parser."""
    if '|' in command or '>' in command: return 'allow'
    try: args = shlex.split(command)
    except ValueError: return 'allow'
    if not args or args[0] not in {'cat', 'head', 'tail', 'less', 'more'}:
        return 'allow'
    targets = [a for a in args[1:] if not a.startswith('-')]
    if not targets: return 'allow'
    value = line_lookup(targets[0])
    if value is None: return 'allow'
    return 'block' if len(value.splitlines()) > min_lines else 'allow'


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--kind', choices=['read', 'bash'], required=True)
    p.add_argument('--threshold', type=int, default=350)
    a = p.parse_args()
    payload = json.loads(input())
    if a.kind == 'read':
        path = payload.get('tool_input', {}).get('file_path')
        text = Path(path).read_text() if path and Path(path).is_file() else None
        answer = inspect_read(payload, text, a.threshold)
    else:
        command = payload.get('tool_input', {}).get('command', '')
        def get(path):
            return Path(path).read_text() if Path(path).is_file() else None
        answer = inspect_bash(command, get, a.threshold)
    print(json.dumps({'routing_decision': answer}))

if __name__ == '__main__': main()
