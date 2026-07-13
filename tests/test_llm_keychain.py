from subprocess import CompletedProcess

import pytest

from orum.llm.keychain import KeychainSecretError, read_generic_password


def test_read_generic_password_uses_security_without_secret_in_arguments():
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return CompletedProcess(args, 0, stdout="secret-from-keychain\n", stderr="")

    value = read_generic_password(
        service="0rum-openrouter",
        account="cube",
        runner=runner,
    )

    assert value == "secret-from-keychain"
    assert calls[0][0] == [
        "/usr/bin/security",
        "find-generic-password",
        "-a",
        "cube",
        "-s",
        "0rum-openrouter",
        "-w",
    ]
    assert calls[0][1] == {
        "capture_output": True,
        "text": True,
        "check": False,
    }
    assert "secret-from-keychain" not in repr(calls)


@pytest.mark.parametrize("returncode,stdout", [(44, ""), (0, "\n")])
def test_failure_never_exposes_security_stderr(returncode, stdout):
    def runner(args, **kwargs):
        return CompletedProcess(
            args,
            returncode,
            stdout=stdout,
            stderr="sensitive-security-output",
        )

    with pytest.raises(
        KeychainSecretError,
        match="OpenRouter credential unavailable",
    ) as captured:
        read_generic_password(
            service="0rum-openrouter",
            account="cube",
            runner=runner,
        )

    assert "sensitive-security-output" not in str(captured.value)


def test_default_account_comes_from_current_macos_user(monkeypatch):
    seen = {}

    def runner(args, **kwargs):
        seen["args"] = args
        return CompletedProcess(args, 0, stdout="value\n", stderr="")

    monkeypatch.setattr("getpass.getuser", lambda: "runtime-user")

    assert read_generic_password(runner=runner) == "value"
    assert seen["args"][3] == "runtime-user"
