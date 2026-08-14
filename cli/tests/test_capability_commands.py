from knowledge_studio import capability_commands


def _command_check(name: str, available: bool) -> dict:
    return {
        "type": "command",
        "name": name,
        "available": available,
        "path": f"/usr/bin/{name}" if available else None,
        "suggestion": None if available else f"Install {name}",
    }


def test_optional_command_failure_does_not_mark_doctor_unhealthy(
    tmp_path, monkeypatch
):
    provider = {
        "id": "yt-dlp",
        "label": "yt-dlp",
        "execution": "managed",
        "requirements": {
            "command": "yt-dlp",
            "optional_commands": ["ffmpeg"],
        },
    }
    availability = {"yt-dlp": True, "ffmpeg": False}
    monkeypatch.setattr(
        capability_commands, "_scan_providers", lambda _root: [provider]
    )
    monkeypatch.setattr(
        capability_commands,
        "_check_command",
        lambda name: _command_check(name, availability[name]),
    )

    result = capability_commands.capability_doctor(tmp_path)

    provider_result = result["providers"][0]
    optional_check = next(
        check for check in provider_result["checks"] if check["name"] == "ffmpeg"
    )
    assert provider_result["status"] == "ready"
    assert provider_result["healthy"] is True
    assert result["overall"] == "healthy"
    assert optional_check["available"] is False
    assert optional_check["required"] is False


def test_required_command_failure_still_marks_doctor_unhealthy(
    tmp_path, monkeypatch
):
    provider = {
        "id": "local-tool",
        "label": "Local Tool",
        "execution": "managed",
        "requirements": {"command": "required-tool"},
    }
    monkeypatch.setattr(
        capability_commands, "_scan_providers", lambda _root: [provider]
    )
    monkeypatch.setattr(
        capability_commands,
        "_check_command",
        lambda name: _command_check(name, False),
    )

    result = capability_commands.capability_doctor(tmp_path)

    provider_result = result["providers"][0]
    assert provider_result["status"] == "unavailable"
    assert provider_result["healthy"] is False
    assert result["overall"] == "issues_found"
