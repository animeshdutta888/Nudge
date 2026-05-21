from __future__ import annotations

import os
import sys


def main() -> int:
    if sys.version_info < (3, 10):
        print("Nudge requires Python 3.10+. You are running:", sys.version.split()[0])
        print("Fix: create a venv with Python 3.10+ and `pip install -r requirements.txt`.")
        return 1

    if len(sys.argv) > 1:
        from app.agent.core import run_agent

        arg_text = " ".join(sys.argv[1:]).strip()
        if arg_text.lower() in {"start-day", "start day"}:
            arg_text = "start my day"
        if arg_text.lower() in {"close-day", "close day"}:
            arg_text = "close my day"
        print(run_agent(arg_text, source="cli"))
        return 0

    print("Nudge (local-only). Type 'help' or 'quit'.")
    while True:
        try:
            text = input("nudge> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            return 0

        if not text:
            continue
        if text.lower() in {"q", "quit", "exit"}:
            print("Bye.")
            return 0
        if text.lower() in {"clear", "cls"}:
            # Local UX command; never stored.
            os.system("clear")
            continue
        if text.lower() == "help":
            print(
                "Commands:\n"
                "- log: <text>   store a daily log\n"
                "- note: <text>  store a note (second brain)\n"
                "- save: <text>  alias for note:\n"
                "- remember: <text>  alias for note:\n"
                "- checkin       3-question daily check-in (saved as one log)\n"
                "- review week   weekly review (logs + notes)\n"
                "- ask           get a random question to build persona (ask reset clears history)\n"
                "- remind: <when> <text>  save a reminder (e.g. tomorrow 09:00 call mom)\n"
                "- reminders     list reminders\n"
                "- done <id>     mark reminder done\n"
                "- project add <name>         add a project\n"
                "- goal add <project> :: <goal>  add a goal to a project\n"
                "- done <project> :: <n>      mark a project goal done\n"
                "- projects      list projects and goal counts\n"
                "- recent        show recent logs/notes with repair indices\n"
                "- edit note|log <n> :: <text>   edit a recent item\n"
                "- delete note|log <n>          delete a recent item\n"
                "- pin/unpin note|log <n>       pin or unpin a recent item\n"
                "- timeline      show recent cross-memory timeline\n"
                "- story week    short weekly narrative\n"
                "- activities    recommend activities based on persona\n"
                "- dashboard     run `python -m app.dashboard` in another terminal\n"
                "- autosave on|off|status  control intelligent auto-saving\n"
                "  With autosave ON, Nudge may still ask before saving borderline but useful new context.\n"
                "- approve       confirm a suggested save or plan\n"
                "- skip          discard a suggested save or plan\n"
                "- persona       print current persona JSON\n"
                "- insights      weekly summary + patterns\n"
                "- (anything)    ask a question or reflect\n"
                "- clear         clear the screen\n"
                "- quit: exit\n"
            )
            continue

        if text.lower() == "checkin":
            energy = input("energy (1-10 or a word): ").strip()
            focus = input("focus (what are you focusing on today?): ").strip()
            win = input("one win (small counts): ").strip()
            entry = f"Daily check-in: energy={energy or 'n/a'}; focus={focus or 'n/a'}; win={win or 'n/a'}"

            from app.agent.core import run_agent

            print(run_agent(f"log: {entry}"))
            continue

        from app.agent.core import run_agent  # local import so version/deps errors show nicely

        out = run_agent(text)
        print(out)


if __name__ == "__main__":
    raise SystemExit(main())
