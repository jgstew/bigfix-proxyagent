from bigfix_proxyagent.util import major_minor


def test_major_minor_ok():
    assert major_minor("3.2.3") == (3, 2)
    assert major_minor("10.0") == (10, 0)


def test_major_minor_none_and_empty():
    assert major_minor(None) is None
    assert major_minor("") is None


def test_major_minor_too_few_parts():
    assert major_minor("3") is None


def test_major_minor_non_numeric():
    assert major_minor("1.2rc1") is None
    assert major_minor("x.y") is None
