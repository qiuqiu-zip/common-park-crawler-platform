import re
import shlex
from pathlib import Path

from crawler_platform.cli import main
from scripts import quality_gate


REQUIRED_DOCS = [
    "docs/architecture.md",
    "docs/quick_start.md",
    "docs/user_guide.md",
    "docs/developer_guide.md",
    "docs/cli.md",
    "docs/api.md",
    "docs/web_admin.md",
    "docs/config.md",
    "docs/storage.md",
    "docs/examples.md",
    "docs/testing.md",
    "docs/test_matrix.md",
    "docs/final_acceptance.md",
    "docs/delivery_checklist.md",
    "docs/troubleshooting.md",
    "docs/codex_workflow.md",
    "docs/feature_status.md",
]


def _read(path):
    return Path(path).read_text(encoding="utf-8")


def _markdown_files():
    return [Path("README.md"), *sorted(Path("docs").glob("*.md"))]


def _command_lines():
    for path in _markdown_files():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(("python ", "pytest ", "uvicorn ", "rg ")):
                yield path, stripped


def test_required_documentation_files_exist():
    for path in REQUIRED_DOCS:
        assert Path(path).exists(), path
    assert quality_gate.check_required_docs()["status"] == "passed"


def test_readme_has_core_feature20_sections():
    readme = _read("README.md")
    required_sections = [
        "## Quick Start",
        "## Run The Web Admin",
        "## Quality Gates",
        "## Architecture",
        "## Feature Status",
        "## Directory Structure",
        "## Spider Config",
        "## CLI",
        "## API",
        "## Examples And Templates",
        "## Optional Playwright",
        "## No-Database Boundary",
        "## Documentation Index",
        "## Troubleshooting",
    ]
    for section in required_sections:
        assert section in readme
    for feature_number in range(1, 21):
        assert f"Feature {feature_number:02d}" in readme


def test_docs_links_point_to_existing_files():
    text = "\n".join(path.read_text(encoding="utf-8") for path in _markdown_files())
    links = set(
        re.findall(r"\[[^\]]+\]\((docs/[A-Za-z0-9_./-]+\.(?:md|json|sql))\)", text)
    )
    for link in links:
        assert Path(link).exists(), link


def test_documented_local_cli_commands_parse_and_smoke(workspace_tmp_path, capsys):
    readme = _read("README.md")
    expected = [
        "python -m crawler_platform.cli doctor --data-dir ./data-demo",
        "python -m crawler_platform.cli validate examples/local_api_json.json",
        "python -m crawler_platform.cli run examples/local_api_json.json --task-id quickstart-demo --data-dir ./data-demo",
        "python -m crawler_platform.cli task show quickstart-demo --paths --data-dir ./data-demo",
        "python -m crawler_platform.cli export task quickstart-demo --format json --data-dir ./data-demo",
        "python -m crawler_platform.cli examples list",
        "python -m crawler_platform.cli examples list --quickstart",
        "python -m crawler_platform.cli examples validate",
        "python -m crawler_platform.cli init spider --type api --output ./data-demo/my-api-spider.json",
    ]
    for command in expected:
        assert command in readme
        parts = shlex.split(command, posix=False)
        assert parts[:3] == ["python", "-m", "crawler_platform.cli"]

    data_dir = workspace_tmp_path / "docs-demo"
    assert main(["doctor", "--data-dir", str(data_dir), "--json"]) == 0
    assert main(["validate", "examples/local_api_json.json"]) == 0
    assert main(["examples", "list", "--json"]) == 0
    assert main(["examples", "list", "--quickstart", "--json"]) == 0
    assert main(["examples", "validate", "--json"]) == 0
    assert main(["run", "examples/local_api_json.json", "--task-id", "quickstart-demo", "--data-dir", str(data_dir)]) == 0
    assert main(["task", "show", "quickstart-demo", "--paths", "--data-dir", str(data_dir)]) == 0
    assert main(["export", "task", "quickstart-demo", "--format", "json", "--data-dir", str(data_dir)]) == 0
    target = data_dir / "my-api-spider.json"
    assert main(["init", "spider", "--type", "api", "--output", str(target)]) == 0
    assert main(["validate", str(target)]) == 0
    capsys.readouterr()


def test_documented_commands_do_not_default_to_real_external_network():
    for path, command in _command_lines():
        lowered = command.lower()
        if "http://" in lowered or "https://" in lowered:
            assert "127.0.0.1" in lowered or "localhost" in lowered or "example.test" in lowered, (path, command)


def test_docs_declare_v1_pass_without_database_runtime_install():
    text = "\n".join(path.read_text(encoding="utf-8") for path in [Path("README.md"), *Path("docs").glob("*.md")])
    forbidden_positive_claims = [
        "entire project pass",
        "whole project pass",
        "final project pass",
        "project is finally accepted",
    ]
    lowered = text.lower()
    for phrase in forbidden_positive_claims:
        assert phrase not in lowered
    assert "feature 21 final acceptance evidence is complete" in lowered
    assert "chatgpt review loop" in lowered
    assert "has returned" in lowered
    assert "no-database filestore v1" in lowered
    assert not re.search(r"pip install .*?(sqlalchemy|psycopg|postgres|mysql|sqlite)", lowered)
    assert quality_gate.scan_database_dependencies()["status"] == "passed"


def test_docs_do_not_leak_cleartext_sensitive_values():
    allowed_markers = ("fake", "local", "example", "placeholder", "redacted", "test")
    pattern = re.compile(
        r"(?i)(password|passwd|secret|token|authorization|cookie|api_key|access_key|private_key)[A-Za-z0-9_-]*\s*[:=]\s*[\"']([^\"']+)[\"']"
    )
    findings = []
    for path in _markdown_files():
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            value = match.group(2).lower()
            if not any(marker in value for marker in allowed_markers):
                findings.append((str(path), match.group(0)))
    assert findings == []


def test_feature_status_lists_feature_01_to_21():
    status = _read("docs/feature_status.md")
    for feature_number in range(1, 22):
        assert f"Feature {feature_number:02d}" in status
    assert "Feature 20 Documentation System" in status
    assert "Feature 21 Final Acceptance" in status
    assert "ChatGPT" in status
    assert "no-database FileStore v1 platform PASS" in status
