# Optional Command Health Semantics — Design and Implementation Plan

> **For implementers:** Use `superpowers:test-driven-development` while changing the behavior and `superpowers:verification-before-completion` before claiming the fix is complete. The change is small enough to execute in one session; no parallel work is needed.

**Goal:** Make `oks capability doctor` report a provider and the overall environment as healthy when only an explicitly optional command is missing, while preserving the failed optional check as diagnostic information.

**Architecture:** Keep Provider parsing and the public doctor result unchanged. The fix belongs only in `capability_doctor()`'s aggregation rule: a failed check affects `healthy` and `overall` only when it is not a note and is not marked `required: false`. `_provider_status()` already follows this rule, so aligning the aggregation removes the current contradiction without adding a new abstraction.

**Tech stack:** Python 3.12+, pytest, the existing `knowledge_studio.capability_commands` module.

## 1. Problem and evidence

`_check_provider_health()` records every command check. For entries under a Provider's `requirements.optional_commands`, it adds `required: false` to the check.

`_provider_status()` respects that marker: it excludes a failed check when `required is False`. However, `capability_doctor()` currently treats every failed non-note check as a health failure. A Provider can therefore return contradictory fields such as:

```json
{
  "healthy": false,
  "status": "ready",
  "checks": [
    {
      "type": "command",
      "name": "ffmpeg",
      "available": false,
      "required": false
    }
  ]
}
```

This also changes the top-level result from `healthy` to `issues_found`, even though the missing dependency was explicitly declared optional.

The existing `yt-dlp` Provider is the motivating case: `yt-dlp` is required and `ffmpeg` is optional. The tests should model that contract directly without depending on which binaries happen to be installed on the development machine.

## 2. Desired behavior

For each health check, use the following rule:

| Check result | Provider `healthy` | Affects top-level `overall` | Still included in `checks` |
|---|---:|---:|---:|
| Required check succeeds | Yes | No failure | Yes |
| Required check fails | No | Yes | Yes |
| Optional check succeeds | Yes | No failure | Yes |
| Optional check fails | Yes | No failure | Yes |
| Informational note | No change | No failure | Yes |

Concretely:

- A failed check is blocking only when `available is False`, its type is not `note`, and `required is not False`.
- An optional failure remains visible with its original `available: false` and `required: false` values. It is diagnostic degradation, not a failed health state.
- `capability_doctor()` keeps its current return schema and status vocabulary.
- Required command failures continue to produce `healthy: false`, Provider status `unavailable`, and top-level `overall: issues_found`.

## 3. Scope and non-goals

### In scope

- Align `capability_doctor()`'s `healthy` and `overall` aggregation with the optional-check semantics already used by `_provider_status()`.
- Add regression tests for an optional command failure and a required command failure.
- Preserve failed optional checks in the returned diagnostics.

### Out of scope

- Changing Provider YAML files or reclassifying any dependency.
- Changing `_provider_status()` or its special-case Provider statuses.
- Adding warning/degraded states or changing the JSON/text output schema.
- Refactoring health checks into new classes or helpers.
- Fixing the existing `oks capability probe` wording in the external-provider note.
- Changing Python package/import-name mapping, metrics, installation behavior, or capability catalogs.

## 4. Compatibility and risks

This is a behavioral correction with no interface migration. Consumers receive the same keys and the same individual checks. The only intentional differences are:

- `providers[*].healthy` changes from `false` to `true` when all failures are explicitly optional.
- `overall` changes from `issues_found` to `healthy` when no Provider has a required failure.

The primary regression risk is accidentally ignoring required failures. A paired negative-control test prevents that. Tests must mock command discovery so results do not vary according to the developer or CI machine.

## 5. Acceptance criteria

The change is complete when all of the following are true:

- A Provider with an available required command and an unavailable optional command has `status == "ready"` and `healthy is True`.
- Its unavailable optional check remains present with `available is False` and `required is False`.
- When it is the only Provider, the doctor result has `overall == "healthy"`.
- A Provider with an unavailable required command has `status == "unavailable"`, `healthy is False`, and top-level `overall == "issues_found"`.
- The targeted regression tests and the full pytest suite pass.
- Wheel/sdist content validation passes and the build does not alter tracked files.

## 6. File map

- Create `cli/tests/test_capability_commands.py`: deterministic regression coverage for doctor aggregation.
- Modify `cli/knowledge_studio/capability_commands.py`: exclude explicitly optional failed checks from the health failure predicate in `capability_doctor()`.

No other production files should change.

## 7. Implementation plan

### Task 1: Add regression coverage and align doctor aggregation

**Files:**

- Create: `cli/tests/test_capability_commands.py`
- Modify: `cli/knowledge_studio/capability_commands.py:338-354`
- Test: `cli/tests/test_capability_commands.py`

#### Step 1: Prepare an isolated development environment if needed

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ./cli pytest requests build
```

Expected result: installation exits with status 0 and imports the package from this checkout. `.venv/` remains ignored by Git.

#### Step 2: Write the failing tests

Create `cli/tests/test_capability_commands.py` with tests that exercise the real `_check_provider_health()` and `capability_doctor()` aggregation while replacing only Provider discovery and machine-dependent command lookup:

```python
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
```

This setup deliberately avoids invoking real executables. It also verifies that `_check_provider_health()` adds `required: false` rather than merely testing a fabricated final check list.

#### Step 3: Run the tests and confirm the regression is reproduced

```bash
.venv/bin/python -m pytest cli/tests/test_capability_commands.py -q
```

Expected result before the production change: the optional-command test fails at `assert provider_result["healthy"] is True` because the actual value is `False`. The required-command control test passes.

If the optional-command test passes before modifying production code, stop and inspect the checkout instead of continuing; the assumed defect is no longer present.

#### Step 4: Implement the smallest behavior change

In `capability_doctor()`, replace the broad `has_failure` predicate with a required-failure predicate:

```python
has_required_failure = any(
    c.get("available") is False
    and c.get("type") != "note"
    and c.get("required") is not False
    for c in checks
)
if has_required_failure:
    all_healthy = False
```

Use `not has_required_failure` for the Provider's `healthy` field. Do not modify the check list or `_provider_status()`.

#### Step 5: Run the targeted tests

```bash
.venv/bin/python -m pytest cli/tests/test_capability_commands.py -q
```

Expected result: `2 passed` and exit status 0.

#### Step 6: Run the full behavioral test suite

```bash
.venv/bin/python -m pytest -q
```

Expected result: exit status 0 with no failed tests.

#### Step 7: Validate package artifacts and repository cleanliness

```bash
git status --porcelain
.venv/bin/python -m build --outdir dist ./cli
git status --porcelain
.venv/bin/python cli/scripts/check_dist.py dist
git diff --check
```

Expected results:

- Both status snapshots list the same intended source and test files plus this planning document; the build itself adds no tracked change.
- The build exits with status 0.
- `check_dist.py` exits with status 0.
- `git diff --check` prints nothing and exits with status 0.

If any additional tracked file changes, stop and inspect it rather than including it in this fix.

#### Step 8: Review and commit the implementation

Review the final diff, then stage only the implementation and regression test:

```bash
git diff -- cli/knowledge_studio/capability_commands.py cli/tests/test_capability_commands.py
git add cli/knowledge_studio/capability_commands.py cli/tests/test_capability_commands.py
git commit -m "fix(capability): ignore missing optional commands in health status"
```

Expected result: one focused commit containing one production-code change and the two regression tests. Do not push or create a Pull Request without the user's separate, explicit authorization.

## 8. Pull Request handoff

Suggested PR title:

```text
fix(capability): ignore missing optional commands in health status
```

The PR description should state the before/after semantic difference, mention that optional failures remain visible in `checks`, and list the targeted pytest, full pytest, and package validation results. It should not claim changes to installation, Provider definitions, or capability probing.
