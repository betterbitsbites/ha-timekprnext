import pytest

from tk_agent.config import load

GOOD = """
[broker]
host = broker.lan
port = 8883
username = tk-agent-pc1
password = secret

[agent]
host_id = pc1

[kids]
kiduser = kid-a
"""


def write(tmp_path, text):
    p = tmp_path / "tk-agent.conf"
    p.write_text(text)
    return str(p)


def test_load_good_config(tmp_path):
    cfg = load(write(tmp_path, GOOD))
    assert (cfg.broker_host, cfg.broker_port, cfg.username, cfg.password) == ("broker.lan", 8883, "tk-agent-pc1", "secret")
    assert cfg.host_id == "pc1"
    assert cfg.interval_s == 45
    assert cfg.kids == {"kiduser": "kid-a"}
    assert cfg.state_dir == "/var/lib/tk-agent"


def test_interval_must_be_30_to_60(tmp_path):
    with pytest.raises(ValueError):
        load(write(tmp_path, GOOD.replace("host_id = pc1", "host_id = pc1\ninterval_s = 5")))


def test_host_id_must_differ_from_kid_ids(tmp_path):
    with pytest.raises(ValueError):
        load(write(tmp_path, GOOD.replace("host_id = pc1", "host_id = kid-a")))


def test_missing_kids_rejected(tmp_path):
    with pytest.raises(ValueError):
        load(write(tmp_path, GOOD.replace("kiduser = kid-a", "")))


def test_bad_ids_rejected(tmp_path):
    with pytest.raises(ValueError):
        load(write(tmp_path, GOOD.replace("kid-a", "kid/a")))
