"""The installer contract, exercised without changing the user's LaunchAgents."""

import os
import plistlib
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


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
    assert plist["WorkingDirectory"] == str(ROOT)
    assert plist["ProgramArguments"] == [str(SCRIPTS / "launch-companion.sh")]
    log = str(tmp_path / "Library/Logs/Leetotter/companion.log")
    assert plist["StandardOutPath"] == log
    assert plist["StandardErrorPath"] == log
    assert "NUS_SOCLAAS_API_KEY" not in plist_path.read_text()
    assert "provider.invalid" not in plist_path.read_text()

    provider_env = tmp_path / "Library/Application Support/Leetotter/provider.env"
    assert provider_env.read_text() == "NUS_SOCLAAS_BASE_URL=https://provider.invalid/v1\n"
    assert provider_env.stat().st_mode & 0o777 == 0o600


def test_install_twice_preserves_base_url_when_argument_omitted(tmp_path):
    first = run_script("install.sh", tmp_path, "--base-url", "https://provider.invalid/v1")
    assert first.returncode == 0, first.stderr
    provider_env = tmp_path / "Library/Application Support/Leetotter/provider.env"
    before = provider_env.read_bytes()
    second = run_script("install.sh", tmp_path)
    assert second.returncode == 0, second.stderr
    assert provider_env.read_bytes() == before


def test_uninstall_removes_only_launch_agent(tmp_path):
    assert run_script("install.sh", tmp_path, "--base-url", "https://provider.invalid/v1").returncode == 0
    data = tmp_path / "Library/Application Support/Leetotter/companion.sqlite3"
    data.write_text("keep")
    result = run_script("uninstall.sh", tmp_path)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "Library/LaunchAgents/com.leetotter.companion.plist").exists()
    assert data.read_text() == "keep"
    assert (tmp_path / "Library/Application Support/Leetotter/provider.env").exists()
    assert run_script("uninstall.sh", tmp_path).returncode == 0


def test_initial_install_requires_base_url(tmp_path):
    result = run_script("install.sh", tmp_path)
    assert result.returncode != 0
    assert "--base-url" in result.stderr
    assert not (tmp_path / "Library/LaunchAgents/com.leetotter.companion.plist").exists()


def test_launch_script_reads_only_documented_keychain_item():
    script = (SCRIPTS / "launch-companion.sh").read_text()
    assert 'security find-generic-password -a "$USER" -s NUS_SOCLAAS_API_KEY -w' in script
    assert script.count("find-generic-password") == 1
    assert "exec uv run python -m companion" in script
    assert "not in the Keychain" in script
    assert "fake-test-key" not in script


def test_launch_continues_without_key_using_fake_commands(tmp_path):
    provider_dir = tmp_path / "Library/Application Support/Leetotter"
    provider_dir.mkdir(parents=True)
    (provider_dir / "provider.env").write_text("NUS_SOCLAAS_BASE_URL=https://provider.invalid/v1\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "security").write_text("#!/bin/sh\nexit 44\n")
    (bin_dir / "uv").write_text(
        "#!/bin/sh\n"
        'test "$NUS_SOCLAAS_BASE_URL" = https://provider.invalid/v1 || exit 41\n'
        'test -z "${NUS_SOCLAAS_API_KEY:-}" || exit 42\n'
        'test "$*" = "run python -m companion" || exit 43\n'
    )
    for name in ("security", "uv"):
        (bin_dir / name).chmod(0o755)
    env = os.environ.copy()
    env.update(HOME=str(tmp_path), USER="test-user", PATH=f"{bin_dir}:{env['PATH']}")
    result = subprocess.run(
        ["bash", str(SCRIPTS / "launch-companion.sh")],
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "not in the Keychain" in result.stderr
    assert "provider.invalid" not in result.stderr
