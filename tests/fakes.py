"""Hand-built run documents for tests that don't need Docker. Labeled mock, like real mock runs."""


def make_run(run_id: str, results: dict[str, list[str]], started_at="2026-11-06T12:00:00+00:00"):
    samples = []
    for task_id, cats in results.items():
        for i, cat in enumerate(cats):
            samples.append(
                {
                    "task": {"id": task_id, "version": "1.0.0", "content_hash": "ab" * 32},
                    "sample_index": i,
                    "model_id": "mock/mixed@0.1.0",
                    "is_mock": True,
                    "prompt": f"prompt for {task_id}",
                    "completion": "[MOCK OUTPUT — test]",
                    "generated_code": "def f():\n    return 42\n",
                    "generation_latency_s": 0.0,
                    "result": {
                        "category": cat,
                        "detail": f"synthetic {cat}",
                        "tests_passed": 1 if cat == "passed" else 0,
                        "tests_total": 1,
                    },
                    "sandbox": {"duration_s": 0.5, "test_output": "1 passed"},
                    "finished_at": started_at,
                }
            )
    return {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": started_at,
        "is_mock": True,
        "notice": "MOCK MODEL — synthetic output, NOT a real model result",
        "harness": {"version": "0.1.0", "git_commit": "deadbeef", "prompt_template_version": "1"},
        "model": {"id": "mock/mixed@0.1.0", "is_mock": True, "params": {"seed": 0}},
        "sandbox_runtime": "runc",
        "samples_per_task": max((len(c) for c in results.values()), default=0),
        "validations": [{"task_id": t, "ok": True, "expected_tests": 1} for t in results],
        "samples": samples,
    }
