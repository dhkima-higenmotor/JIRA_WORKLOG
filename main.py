import sys
import os
import argparse

from ui_tk import JiraWorklogApp


def main():
    parser = argparse.ArgumentParser(
        description="JIRA Worklog - Worklog management tool"
    )
    parser.add_argument(
        "--style",
        default="ui_style.json",
        help="Path to ui_style.json file (default: ui_style.json)",
    )
    args = parser.parse_args()

    style_path = args.style
    if not os.path.isabs(style_path):
        style_path = os.path.abspath(style_path)

    app = JiraWorklogApp(style_path=style_path)
    app.mainloop()


if __name__ == "__main__":
    main()
