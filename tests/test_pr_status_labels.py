#!/usr/bin/env python3

import importlib.util
import re
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (
    REPO_ROOT / ".github/workflows/pr-status-labels.yml"
).read_text(encoding="utf-8")
SCRIPT_PATH = REPO_ROOT / "scripts/pr_status_labels.py"
SPEC = importlib.util.spec_from_file_location("pr_status_labels", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
STATUS_LABELS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STATUS_LABELS)

LabelChange = STATUS_LABELS.LabelChange
NO_CHANGE = LabelChange()
FLIP = LabelChange(add=("needs-review",), remove=("changes-requested",))


def github_pull_request(labels=(), **overrides):
    pull_request = {
        "number": 42,
        "state": "open",
        "draft": False,
        "user": {"login": "contributor", "type": "User"},
        "labels": [{"name": label} for label in labels],
    }
    pull_request.update(overrides)
    return pull_request


def pull_request(labels=(), **overrides):
    return STATUS_LABELS.normalize_pull_request(
        github_pull_request(labels, **overrides),
        42,
    )


class Probe:
    """Stands in for an API check and records whether it was asked."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.answer


def plan(
    action,
    labels=(),
    sender="contributor",
    can_write=False,
    requested_before_event=True,
    **fields,
):
    """Plan a change and count the permission checks it needed."""
    permission = Probe(can_write)
    change = STATUS_LABELS.plan_label_change(
        action,
        pull_request(labels, **fields),
        sender,
        lambda: requested_before_event,
        permission,
    )
    return change, permission.calls


class PlanLabelChangeTest(unittest.TestCase):
    def test_new_pull_request_from_author_without_write_needs_review(self):
        for action in ("opened", "reopened", "ready_for_review"):
            with self.subTest(action=action):
                change, permission_calls = plan(action)

                self.assertEqual(change, LabelChange(add=("needs-review",)))
                self.assertEqual(permission_calls, 1)

    def test_new_pull_request_from_writer_is_unchanged(self):
        self.assertEqual(plan("opened", can_write=True)[0], NO_CHANGE)

    def test_existing_status_label_is_kept_without_permission_check(self):
        for label in (
            "needs-review",
            "changes-requested",
            "needs-rebase",
            "do-not-merge",
            "Ready-To-Merge",
        ):
            with self.subTest(label=label):
                self.assertEqual(plan("opened", [label]), (NO_CHANGE, 0))

    def test_unrelated_labels_do_not_count_as_status(self):
        change, _ = plan("opened", ["bug", "pkg:sdk"])

        self.assertEqual(change, LabelChange(add=("needs-review",)))

    def test_author_push_replaces_changes_requested(self):
        self.assertEqual(
            plan("synchronize", ["changes-requested"]),
            (FLIP, 0),
        )

    def test_flip_removes_the_label_as_spelled_on_the_pull_request(self):
        change, _ = plan("synchronize", ["Changes-Requested"])

        self.assertEqual(change.remove, ("Changes-Requested",))

    def test_flip_does_not_add_needs_review_twice(self):
        change, _ = plan(
            "synchronize",
            ["changes-requested", "needs-review"],
        )

        self.assertEqual(change, LabelChange(remove=("changes-requested",)))

    def test_author_is_matched_case_insensitively(self):
        change, _ = plan(
            "synchronize",
            ["changes-requested"],
            sender="Contributor",
        )

        self.assertEqual(change, FLIP)

    def test_push_by_someone_else_keeps_changes_requested(self):
        self.assertEqual(
            plan("synchronize", ["changes-requested"], sender="maintainer"),
            (NO_CHANGE, 0),
        )

    def test_marking_ready_replaces_changes_requested_for_any_sender(self):
        self.assertEqual(
            plan(
                "ready_for_review",
                ["changes-requested"],
                sender="maintainer",
                can_write=True,
            ),
            (FLIP, 0),
        )

    def test_request_made_after_the_event_is_kept(self):
        for action in ("synchronize", "ready_for_review"):
            with self.subTest(action=action):
                self.assertEqual(
                    plan(
                        action,
                        ["changes-requested"],
                        requested_before_event=False,
                    ),
                    (NO_CHANGE, 0),
                )

    def test_request_time_is_checked_only_when_a_flip_is_due(self):
        for action, labels, expected_calls in (
            ("opened", [], 0),
            ("synchronize", [], 0),
            ("reopened", ["changes-requested"], 0),
            ("synchronize", ["changes-requested"], 1),
            ("ready_for_review", ["changes-requested"], 1),
        ):
            with self.subTest(action=action, labels=labels):
                requested_before_event = Probe(True)
                STATUS_LABELS.plan_label_change(
                    action,
                    pull_request(labels),
                    "contributor",
                    requested_before_event,
                    Probe(True),
                )
                self.assertEqual(
                    requested_before_event.calls,
                    expected_calls,
                )

    def test_push_never_adds_needs_review_to_unlabeled_pull_request(self):
        self.assertEqual(plan("synchronize"), (NO_CHANGE, 0))

    def test_reopening_keeps_changes_requested(self):
        self.assertEqual(
            plan("reopened", ["changes-requested"]),
            (NO_CHANGE, 0),
        )

    def test_draft_closed_and_bot_pull_requests_are_unchanged(self):
        for fields in (
            {"draft": True},
            {"state": "closed"},
            {"user": {"login": "dependabot[bot]", "type": "Bot"}},
            {"user": {"login": "renovate[bot]", "type": "User"}},
        ):
            with self.subTest(fields=fields):
                for action in ("opened", "synchronize", "ready_for_review"):
                    self.assertEqual(
                        plan(action, ["changes-requested"], **fields),
                        (NO_CHANGE, 0),
                    )
                    self.assertEqual(plan(action, **fields), (NO_CHANGE, 0))

    def test_other_actions_are_unchanged(self):
        for action in ("labeled", "edited", "closed"):
            with self.subTest(action=action):
                self.assertEqual(plan(action), (NO_CHANGE, 0))
                self.assertEqual(
                    plan(action, ["changes-requested"]),
                    (NO_CHANGE, 0),
                )


def label_event(event, name, created_at):
    return {"event": event, "label": {"name": name}, "created_at": created_at}


def at(timestamp):
    return STATUS_LABELS.parse_timestamp(timestamp, "test time")


class EventOrderingTest(unittest.TestCase):
    def test_reads_the_event_time_from_the_payload(self):
        with patch.dict(
            "os.environ",
            {"EVENT_UPDATED_AT": "2026-11-18T23:44:30Z"},
            clear=True,
        ):
            self.assertEqual(
                STATUS_LABELS.event_time(),
                at("2026-11-18T23:44:30+00:00"),
            )

    def test_rejects_times_without_a_zone_or_format(self):
        for value in ("yesterday", "2026-11-18T23:44:30", None):
            with self.subTest(value=value):
                with self.assertRaises(STATUS_LABELS.StatusLabelError):
                    STATUS_LABELS.parse_timestamp(value, "event time")

    def test_finds_the_latest_application_across_pages(self):
        request = "changes-requested"
        first_page = [
            label_event("labeled", request, "2026-11-01T00:00:00Z")
        ] + [label_event("labeled", "bug", "2026-11-09T00:00:00Z")] * 99
        second_page = [
            label_event("unlabeled", request, "2026-11-02T00:00:00Z"),
            label_event("labeled", request.title(), "2026-11-03T00:00:00Z"),
            {
                "event": "review_requested",
                "created_at": "2026-11-04T00:00:00Z",
            },
            "unexpected",
        ]
        with patch.object(
            STATUS_LABELS,
            "run_gh_json",
            side_effect=[first_page, second_page],
        ) as run_gh:
            applied_at = STATUS_LABELS.latest_label_application(
                "aws/example",
                42,
                "changes-requested",
            )

        self.assertEqual(applied_at, at("2026-11-03T00:00:00Z"))
        self.assertEqual(
            [call.args[0] for call in run_gh.call_args_list],
            [
                ["repos/aws/example/issues/42/events?per_page=100&page=1"],
                ["repos/aws/example/issues/42/events?per_page=100&page=2"],
            ],
        )

    def test_request_counts_as_before_the_event_only_if_strictly_older(
        self,
    ):
        event_moment = at("2026-11-18T12:00:00Z")
        for applied_at, expected in (
            (at("2026-11-18T11:59:59Z"), True),
            (at("2026-11-18T12:00:00Z"), False),
            (at("2026-11-18T12:00:01Z"), False),
            (None, False),
        ):
            with self.subTest(applied_at=applied_at), patch.object(
                STATUS_LABELS,
                "latest_label_application",
                return_value=applied_at,
            ), redirect_stdout(StringIO()):
                self.assertEqual(
                    STATUS_LABELS.applied_before(
                        "aws/example",
                        42,
                        "changes-requested",
                        event_moment,
                    ),
                    expected,
                )


class NormalizePullRequestTest(unittest.TestCase):
    def test_maps_casefolded_label_names_to_their_spelling(self):
        self.assertEqual(
            pull_request(["Needs-Review", "bug"])["labels"],
            {"needs-review": "Needs-Review", "bug": "bug"},
        )

    def test_rejects_invalid_pull_requests(self):
        for value in (
            None,
            github_pull_request(number=7),
            github_pull_request(draft=None),
            github_pull_request(user=None),
            github_pull_request(user={"type": "User"}),
            {**github_pull_request(), "labels": [{"id": 1}]},
        ):
            with self.subTest(value=value):
                with self.assertRaises(STATUS_LABELS.StatusLabelError):
                    STATUS_LABELS.normalize_pull_request(value, 42)


class GitHubCallsTest(unittest.TestCase):
    def test_write_roles_count_as_write_access(self):
        for permission, expected in (
            ("admin", True),
            ("write", True),
            ("triage", False),
            ("read", False),
            ("none", False),
        ):
            with self.subTest(permission=permission):
                with patch.object(
                    STATUS_LABELS,
                    "run_gh_json",
                    return_value={"permission": permission},
                ) as run_gh:
                    self.assertEqual(
                        STATUS_LABELS.has_write_permission(
                            "aws/example",
                            "a user",
                        ),
                        expected,
                    )
                self.assertEqual(
                    run_gh.call_args.args[0],
                    ["repos/aws/example/collaborators/a%20user/permission"],
                )

    def test_not_found_is_returned_only_when_allowed(self):
        failure = Mock(
            returncode=1,
            stdout='{"message":"Not Found"}',
            stderr="gh: Label does not exist (HTTP 404)",
        )
        with patch.object(
            STATUS_LABELS.subprocess,
            "run",
            return_value=failure,
        ):
            self.assertIsNone(
                STATUS_LABELS.run_gh_json(["path"], allow_not_found=True)
            )
            with self.assertRaisesRegex(
                STATUS_LABELS.StatusLabelError,
                "HTTP 404",
            ):
                STATUS_LABELS.run_gh_json(["path"])

    def test_flip_adds_needs_review_before_removing_changes_requested(self):
        with patch.object(
            STATUS_LABELS,
            "run_gh_json",
            side_effect=[{"name": "needs-review"}, [], None],
        ) as run_gh, redirect_stdout(StringIO()):
            STATUS_LABELS.apply_label_change("aws/example", 42, FLIP)

        self.assertEqual(
            [call.args[0] for call in run_gh.call_args_list],
            [
                ["repos/aws/example/labels/needs-review"],
                [
                    "--method",
                    "POST",
                    "repos/aws/example/issues/42/labels",
                    "--input",
                    "-",
                ],
                [
                    "--method",
                    "DELETE",
                    "repos/aws/example/issues/42/labels/changes-requested",
                ],
            ],
        )
        self.assertEqual(
            run_gh.call_args_list[1].kwargs["input_value"],
            {"labels": ["needs-review"]},
        )
        self.assertTrue(run_gh.call_args_list[2].kwargs["allow_not_found"])

    def test_missing_needs_review_label_is_created(self):
        with patch.object(
            STATUS_LABELS,
            "run_gh_json",
            side_effect=[None, {"name": "needs-review"}, []],
        ) as run_gh, redirect_stdout(StringIO()):
            STATUS_LABELS.apply_label_change(
                "aws/example",
                42,
                LabelChange(add=("needs-review",)),
            )

        self.assertEqual(
            run_gh.call_args_list[1].args[0][2],
            "repos/aws/example/labels",
        )
        self.assertEqual(
            run_gh.call_args_list[1].kwargs["input_value"],
            {
                "name": "needs-review",
                "color": "d4c5f9",
                "description": "Awaiting maintainer review",
            },
        )

    def test_no_change_makes_no_requests(self):
        with patch.object(
            STATUS_LABELS,
            "run_gh_json",
        ) as run_gh, redirect_stdout(StringIO()):
            STATUS_LABELS.apply_label_change("aws/example", 42, NO_CHANGE)

        run_gh.assert_not_called()

    def test_main_reads_pull_request_and_checks_author_permission(self):
        with patch.dict(
            "os.environ",
            {
                "GITHUB_REPOSITORY": "aws/example",
                "PULL_REQUEST_NUMBER": "42",
                "EVENT_ACTION": "opened",
                "EVENT_SENDER": "contributor",
                "EVENT_UPDATED_AT": "2026-11-18T12:00:00Z",
            },
            clear=True,
        ), patch.object(
            STATUS_LABELS,
            "run_gh_json",
            side_effect=[
                github_pull_request(),
                {"permission": "read"},
                {"name": "needs-review"},
                [{"name": "needs-review"}],
            ],
        ) as run_gh, redirect_stdout(StringIO()):
            self.assertEqual(STATUS_LABELS.main(), 0)

        self.assertEqual(
            [call.args[0][-1] for call in run_gh.call_args_list[:3]],
            [
                "repos/aws/example/pulls/42",
                "repos/aws/example/collaborators/contributor/permission",
                "repos/aws/example/labels/needs-review",
            ],
        )
        self.assertEqual(
            run_gh.call_args_list[3].kwargs["input_value"],
            {"labels": ["needs-review"]},
        )

    def test_main_flips_a_request_made_before_the_author_pushed(self):
        with patch.dict(
            "os.environ",
            {
                "GITHUB_REPOSITORY": "aws/example",
                "PULL_REQUEST_NUMBER": "42",
                "EVENT_ACTION": "synchronize",
                "EVENT_SENDER": "contributor",
                "EVENT_UPDATED_AT": "2026-11-18T12:00:00Z",
            },
            clear=True,
        ), patch.object(
            STATUS_LABELS,
            "run_gh_json",
            side_effect=[
                github_pull_request(["changes-requested"]),
                [
                    label_event(
                        "labeled",
                        "changes-requested",
                        "2026-11-17T09:00:00Z",
                    )
                ],
                {"name": "needs-review"},
                [{"name": "needs-review"}],
                [],
            ],
        ) as run_gh, redirect_stdout(StringIO()):
            self.assertEqual(STATUS_LABELS.main(), 0)

        self.assertEqual(
            run_gh.call_args_list[1].args[0],
            ["repos/aws/example/issues/42/events?per_page=100&page=1"],
        )
        self.assertEqual(
            run_gh.call_args_list[4].args[0][-1],
            "repos/aws/example/issues/42/labels/changes-requested",
        )
        self.assertEqual(len(run_gh.call_args_list), 5)

    def test_main_keeps_a_request_reapplied_after_the_event_on_rerun(self):
        # The first run removed the request; a maintainer applied it again
        # after reviewing; then the first run was re-run with its payload.
        with patch.dict(
            "os.environ",
            {
                "GITHUB_REPOSITORY": "aws/example",
                "PULL_REQUEST_NUMBER": "42",
                "EVENT_ACTION": "synchronize",
                "EVENT_SENDER": "contributor",
                "EVENT_UPDATED_AT": "2026-11-18T12:00:00Z",
            },
            clear=True,
        ), patch.object(
            STATUS_LABELS,
            "run_gh_json",
            side_effect=[
                github_pull_request(["changes-requested"]),
                [
                    label_event(
                        "labeled",
                        "changes-requested",
                        "2026-11-17T09:00:00Z",
                    ),
                    label_event(
                        "unlabeled",
                        "changes-requested",
                        "2026-11-18T12:00:20Z",
                    ),
                    label_event(
                        "labeled",
                        "changes-requested",
                        "2026-11-18T15:30:00Z",
                    ),
                ],
            ],
        ) as run_gh, redirect_stdout(StringIO()) as stdout:
            self.assertEqual(STATUS_LABELS.main(), 0)

        self.assertEqual(len(run_gh.call_args_list), 2)
        self.assertIn("not applied before this event", stdout.getvalue())

    def test_main_reports_failure(self):
        with patch.dict(
            "os.environ",
            {"GITHUB_REPOSITORY": "aws/example"},
            clear=True,
        ), redirect_stdout(StringIO()), patch(
            "sys.stderr",
            new_callable=StringIO,
        ) as stderr:
            self.assertEqual(STATUS_LABELS.main(), 1)

        self.assertIn("PULL_REQUEST_NUMBER must be set", stderr.getvalue())


def job_condition(workflow: str) -> str:
    match = re.search(r"(?ms)^    if: >-\n(.*?)(?=^    [a-z-]+:)", workflow)
    if match is None:
        raise AssertionError("job has no folded if: >- block")
    return " ".join(match.group(1).split())


class WorkflowContractTest(unittest.TestCase):
    def test_runs_on_pull_request_target_and_as_reusable_workflow(self):
        self.assertRegex(
            WORKFLOW,
            r"(?m)^on:\n  pull_request_target:\n"
            r"    types: \[opened, reopened, ready_for_review, synchronize\]\n"
            r"  workflow_call:",
        )
        self.assertRegex(
            WORKFLOW,
            r"(?ms)^      runs-on:\n.*?default: ubuntu-latest",
        )
        self.assertIn(
            "runs-on: ${{ inputs['runs-on'] || 'ubuntu-latest' }}",
            WORKFLOW,
        )

    def test_token_can_only_read_contents_and_write_pull_requests(self):
        self.assertIn("\npermissions: {}\n", WORKFLOW)
        self.assertRegex(
            WORKFLOW,
            r"(?m)^    permissions:\n      contents: read\n"
            r"      pull-requests: write\n    steps:",
        )
        self.assertNotRegex(WORKFLOW, r"(?m)^concurrency:")
        for forbidden in ("issues: write", "id-token", "secrets."):
            self.assertNotIn(forbidden, WORKFLOW)

    def test_skips_drafts_bots_and_pushes_that_change_nothing(self):
        condition = job_condition(WORKFLOW)

        for expected in (
            "github.event_name == 'pull_request_target'",
            "github.event.pull_request.draft == false",
            "github.event.pull_request.user.type != 'Bot'",
            "github.event.action == 'opened'",
            "github.event.action == 'reopened'",
            "github.event.action == 'ready_for_review'",
            "github.event.action == 'synchronize' && "
            "github.event.sender.login == "
            "github.event.pull_request.user.login && "
            "contains(github.event.pull_request.labels.*.name, "
            "'changes-requested')",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, condition)

    def test_checks_out_only_the_trusted_script(self):
        self.assertIn("repository: ${{ job.workflow_repository }}", WORKFLOW)
        self.assertIn("ref: ${{ job.workflow_sha }}", WORKFLOW)
        self.assertIn("sparse-checkout: scripts/pr_status_labels.py", WORKFLOW)
        self.assertIn("persist-credentials: false", WORKFLOW)
        self.assertNotIn("pull_request.head", WORKFLOW)
        self.assertEqual(WORKFLOW.count("actions/checkout@"), 1)

    def test_event_values_reach_the_script_only_through_environment(self):
        self.assertIn(
            "PULL_REQUEST_NUMBER: ${{ github.event.pull_request.number }}",
            WORKFLOW,
        )
        self.assertIn("EVENT_ACTION: ${{ github.event.action }}", WORKFLOW)
        self.assertIn(
            "EVENT_SENDER: ${{ github.event.sender.login }}",
            WORKFLOW,
        )
        self.assertIn(
            "EVENT_UPDATED_AT: ${{ github.event.pull_request.updated_at }}",
            WORKFLOW,
        )
        self.assertIn(
            "        run: python3 scripts/pr_status_labels.py\n",
            WORKFLOW,
        )


if __name__ == "__main__":
    unittest.main()
