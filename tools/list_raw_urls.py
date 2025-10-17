import os
import subprocess
import io
import sys

def get_repo_info():
    """Get GitHub repo URL and branch name from git config."""
    remote_url = subprocess.check_output(
        ["git", "config", "--get", "remote.origin.url"], text=True
    ).strip()
    branch = subprocess.check_output(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True
    ).strip()

    # Normalize GitHub URLs
    if remote_url.endswith(".git"):
        remote_url = remote_url[:-4]
    if remote_url.startswith("git@github.com:"):
        remote_url = "https://github.com/" + remote_url.split(":", 1)[1]

    return remote_url, branch


def main():
    repo_url, branch = get_repo_info()
    raw_base = repo_url.replace("https://github.com", "https://raw.githubusercontent.com") + f"/{branch}/"
    print(f"# Repo: {repo_url}\n# Branch: {branch}\n")

    include_exts = {".py", ".csv", ".json", ".patch", ".sh", ".md"}
    include_dirs = {"portfolio_trades", "tools", "inputs", "outputs", "portfolio_targets"}
    exclude_dirs = {".git", ".venv", "__pycache__", ".idea", "tests", "fonts_cache"}

    for root, _, files in os.walk("."):
        # Skip unwanted directories
        if any(ex in root for ex in exclude_dirs):
            continue

        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in include_exts or any(d in root for d in include_dirs):
                rel_path = os.path.join(root, f).lstrip("./")
                print(f"{raw_base}{rel_path}")


if __name__ == "__main__":
    # Capture stdout to also save to a file
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf

    try:
        main()
    finally:
        sys.stdout = old_stdout

    output = buf.getvalue()
    print(output)

    # Write to text file for recordkeeping
    with open("raw_urls.txt", "w") as f:
        f.write(output)
    print("\n✅ URLs written to raw_urls.txt")