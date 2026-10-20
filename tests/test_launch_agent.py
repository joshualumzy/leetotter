"""The installer contract, exercised without changing the user's LaunchAgents."""

import os
import plistlib
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
APP_FILES = {
    *(str(path.relative_to(ROOT)) for path in (ROOT / "companion").rglob("*.py")),
    "companion/data/catalog.json",
    "roles.toml",
    "pyproject.toml",
    "uv.lock",
    "scripts/leetotter-companion",
}


def run_script(name, home, *args):
    env = os.environ.copy()
    env["LEETOTTER_HOME"] = str(home)
    return subprocess.run(
        ["bash", str(SCRIPTS / name), "--dry-run", *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )


def test_install_creates_launch_agent_without_secret(tmp_path):
    result = run_script("install.sh", tmp_path, "--base-url", "https://provider.invalid/v1")
    assert result.returncode == 0, result.stderr

    plist_path = tmp_path / "Library/LaunchAgents/com.leetotter.companion.plist"
    assert subprocess.run(["plutil", "-lint", str(plist_path)], capture_output=True).returncode == 0
    plist = plistlib.loads(plist_path.read_bytes())
    assert plist["Label"] == "com.leetotter.companion"
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    assert plist["ThrottleInterval"] > 0
    app = tmp_path / "Library/Application Support/Leetotter/app"
    assert plist["WorkingDirectory"] == str(app)
    assert plist["ProgramArguments"] == [str(app / "scripts/leetotter-companion")]
    assert str(ROOT) not in plist_path.read_text()
    assert {str(path.relative_to(app)) for path in app.rglob("*") if path.is_file()} == APP_FILES
    companion_log = str(tmp_path / "Library/Logs/Leetotter/companion.log")
    launch_log = str(tmp_path / "Library/Logs/Leetotter/launch.log")
    assert plist["StandardOutPath"] == launch_log
    assert plist["StandardErrorPath"] == launch_log
    assert plist["EnvironmentVariables"]["LEETOTTER_LOG_PATH"] == companion_log
    assert "NUS_SOCLAAS_API_KEY" not in plist_path.read_text()
    assert "provider.invalid" not in plist_path.read_text()

    provider_env = tmp_path / "Library/Application Support/Leetotter/provider.env"
    assert provider_env.read_text() == "NUS_SOCLAAS_BASE_URL=https://provider.invalid/v1\n"
    assert provider_env.stat().st_mode & 0o777 == 0o600


def test_install_twice_preserves_base_url_when_argument_omitted(tmp_path):
    first = run_script("install.sh", tmp_path, "--base-url", "https://provider.invalid/v1")
    assert first.returncode == 0, first.stderr
    provider_env = tmp_path / "Library/Application Support/Leetotter/provider.env"
    app = tmp_path / "Library/Application Support/Leetotter/app"
    before = provider_env.read_bytes()
    (app / "obsolete.txt").write_text("remove on deploy")
    (app / ".venv").mkdir()
    (app / ".venv/installed.txt").write_text("keep installed dependencies")
    second = run_script("install.sh", tmp_path)
    assert second.returncode == 0, second.stderr
    assert provider_env.read_bytes() == before
    assert not (app / "obsolete.txt").exists()
    assert (app / ".venv/installed.txt").read_text() == "keep installed dependencies"
    assert {str(path.relative_to(app)) for path in app.rglob("*") if path.is_file()} == APP_FILES | {".venv/installed.txt"}


def test_uninstall_removes_app_and_launch_agent(tmp_path):
    assert run_script("install.sh", tmp_path, "--base-url", "https://provider.invalid/v1").returncode == 0
    data = tmp_path / "Library/Application Support/Leetotter/companion.sqlite3"
    data.write_text("keep")
    log = tmp_path / "Library/Logs/Leetotter/companion.log"
    log.write_text("keep log")
    result = run_script("uninstall.sh", tmp_path)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "Library/LaunchAgents/com.leetotter.companion.plist").exists()
    assert not (tmp_path / "Library/Application Support/Leetotter/app").exists()
    assert data.read_text() == "keep"
    assert log.read_text() == "keep log"
    assert (tmp_path / "Library/Application Support/Leetotter/provider.env").exists()
    assert run_script("uninstall.sh", tmp_path).returncode == 0


def test_initial_install_requires_base_url(tmp_path):
    result = run_script("install.sh", tmp_path)
    assert result.returncode != 0
    assert "--base-url" in result.stderr
    assert not (tmp_path / "Library/LaunchAgents/com.leetotter.companion.plist").exists()


def test_launch_script_reads_only_documented_keychain_item():
    script = (SCRIPTS / "leetotter-companion").read_text()
    assert 'security find-generic-password -a "$USER" -s NUS_SOCLAAS_API_KEY -w' in script
    assert script.count("find-generic-password") == 1
    assert "exec uv run python -m companion" in script
    assert "not in the Keychain" in script
    assert "fake-test-key" not in script
    assert str(ROOT) not in script


def test_launch_continues_without_key_using_fake_commands(tmp_path):
    assert run_script("install.sh", tmp_path, "--base-url", "https://provider.invalid/v1").returncode == 0
    app = tmp_path / "Library/Application Support/Leetotter/app"
    provider_dir = tmp_path / "Library/Application Support/Leetotter"
    provider_dir.mkdir(parents=True, exist_ok=True)
    (provider_dir / "provider.env").write_text("NUS_SOCLAAS_BASE_URL=https://provider.invalid/v1\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "security").write_text("#!/bin/sh\nexit 44\n")
    (bin_dir / "uv").write_text(
        "#!/bin/sh\n"
        'test "$NUS_SOCLAAS_BASE_URL" = https://provider.invalid/v1 || exit 41\n'
        'test -z "${NUS_SOCLAAS_API_KEY:-}" || exit 42\n'
        'test "$*" = "run python -m companion" || exit 43\n'
        f'test "$PWD" = "{app}" || exit 45\n'
    )
    for name in ("security", "uv"):
        (bin_dir / name).chmod(0o755)
    env = os.environ.copy()
    env.update(HOME=str(tmp_path), USER="test-user", PATH=f"{bin_dir}:{env['PATH']}")
    result = subprocess.run(
        ["bash", str(app / "scripts/leetotter-companion")],
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "not in the Keychain" in result.stderr
    assert "provider.invalid" not in result.stderr


def test_launch_script_trims_oversized_launch_log_in_place(tmp_path):
    assert run_script("install.sh", tmp_path, "--base-url", "https://provider.invalid/v1").returncode == 0
    app = tmp_path / "Library/Application Support/Leetotter/app"
    log = tmp_path / "Library/Logs/Leetotter/launch.log"
    log.write_bytes(b"x" * (1024 * 1024 + 1))
    inode = log.stat().st_ino
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "security").write_text("#!/bin/sh\nexit 44\n")
    (bin_dir / "uv").write_text("#!/bin/sh\nexit 0\n")
    for name in ("security", "uv"):
        (bin_dir / name).chmod(0o755)
    env = os.environ.copy()
    env.update(HOME=str(tmp_path), USER="test-user", PATH=f"{bin_dir}:{env['PATH']}")
    result = subprocess.run(
        ["bash", str(app / "scripts/leetotter-companion")],
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert log.stat().st_ino == inode
    assert log.read_bytes() == b"x" * (256 * 1024)


def test_status_says_in_plain_words_whether_the_service_is_installed(tmp_path):
    before = run_script("status.sh", tmp_path)
    assert before.returncode == 0, before.stderr
    assert "Leetotter companion service" in before.stdout
    assert "Installed: no" in before.stdout

    assert run_script("install.sh", tmp_path, "--base-url", "https://provider.invalid/v1").returncode == 0
    after = run_script("status.sh", tmp_path)
    assert after.returncode == 0, after.stderr
    assert "Installed: yes" in after.stdout
    assert str(tmp_path / "Library/LaunchAgents/com.leetotter.companion.plist") in after.stdout
    assert str(tmp_path / "Library/Logs/Leetotter/companion.log") in after.stdout
    assert "provider.invalid" not in after.stdout

    launch_log = tmp_path / "Library/Logs/Leetotter/launch.log"
    launch_log.write_text("crash before logging\n")
    with_launch = run_script("status.sh", tmp_path)
    assert with_launch.returncode == 0, with_launch.stderr
    assert "Launch log:" in with_launch.stdout
    assert "crash before logging" in with_launch.stdout
