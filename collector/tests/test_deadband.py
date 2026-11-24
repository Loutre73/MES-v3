from mes_collector.deadband import Deadband


def test_none_stores_every_change_but_not_repeats():
    d = Deadband()
    assert d.passes(1.0, 1.5)
    assert not d.passes(1.0, 1.0)


def test_absolute():
    d = Deadband("absolute", 0.5)
    assert not d.passes(10.0, 10.4)
    assert d.passes(10.0, 10.6)
    assert d.passes(10.0, 9.4)


def test_percent_of_span():
    d = Deadband("percent", 1.0, 0.0, 200.0)  # 1 % de 200 = 2
    assert not d.passes(50.0, 51.5)
    assert d.passes(50.0, 52.5)


def test_percent_of_last_value_without_span():
    d = Deadband("percent", 10.0)  # 10 % de 50 = 5
    assert not d.passes(50.0, 54.0)
    assert d.passes(50.0, 56.0)


def test_first_value_and_none():
    d = Deadband("absolute", 5)
    assert d.passes(None, 3.0)
    assert d.passes(3.0, None)
    assert not d.passes(None, None)
