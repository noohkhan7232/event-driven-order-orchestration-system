def test_e2e_runner_reports_failures_without_hiding_them(capsys):
    from scripts.e2e_reliability import experiment

    assert experiment("control", lambda: "verified")
    assert not experiment("failure", lambda: (_ for _ in ()).throw(AssertionError("wrong stock")))
    output = capsys.readouterr().out
    assert "PASS control" in output and "FAIL failure" in output and "wrong stock" in output
