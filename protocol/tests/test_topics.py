from tk_protocol import topics


def test_usage_topic_roundtrip():
    assert topics.parse_usage_topic(topics.usage("pc1", "kid-a")) == ("pc1", "kid-a")


def test_topic_shapes():
    assert topics.usage("pc1", "kid-a") == "timekpr/pc1/kid-a/usage"
    assert topics.remaining("kid-a") == "timekpr/kid-a/remaining"
    assert topics.settings("kid-a") == "timekpr/kid-a/settings"
    assert topics.status("pc1") == "timekpr/pc1/status"
    assert topics.event("pc1", "kid-a") == "timekpr/pc1/kid-a/event"
    assert topics.cmd("kid-a") == "timekpr/kid-a/cmd"


def test_parse_usage_topic_rejects_other_topics():
    assert topics.parse_usage_topic("timekpr/kid-a/remaining") is None
    assert topics.parse_usage_topic("timekpr/pc1/kid-a/event") is None
    assert topics.parse_usage_topic("other/pc1/kid-a/usage") is None
