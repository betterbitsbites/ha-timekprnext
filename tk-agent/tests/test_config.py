import logging

import pytest

from tk_agent.config import host_key, load

GOOD = """
[broker]
host = broker.lan
port = 8883
username = tk-agent
password = secret

[agent]
interval_s = 45
"""


def write(tmp_path, text):
    p = tmp_path / "tk-agent.conf"
    p.write_text(text)
    return str(p)


def test_load_good_config(tmp_path):
    cfg = load(write(tmp_path, GOOD), hostname=lambda: "Kid-PC.fritz.box")
    assert (cfg.broker_host, cfg.broker_port, cfg.username, cfg.password) == ("broker.lan", 8883, "tk-agent", "secret")
    assert cfg.host_id == "kid-pc"
    assert cfg.interval_s == 45
    assert cfg.state_dir == "/var/lib/tk-agent"


def test_host_id_override(tmp_path):
    cfg = load(write(tmp_path, GOOD + "host_id = pc1\n"), hostname=lambda: "other")
    assert cfg.host_id == "pc1"


def test_bad_host_id_override_rejected(tmp_path):
    with pytest.raises(ValueError):
        load(write(tmp_path, GOOD + "host_id = pc/1\n"), hostname=lambda: "other")


@pytest.mark.parametrize("name,key", [("Kid-PC.fritz.box", "kid-pc"), ("a_b c", "a_b-c"), ("", "pc"), (".x", "pc")])
def test_host_key(name, key):
    assert host_key(name) == key


def test_interval_must_be_30_to_60(tmp_path):
    with pytest.raises(ValueError):
        load(write(tmp_path, GOOD.replace("interval_s = 45", "interval_s = 5")), hostname=lambda: "pc1")


def test_leftover_kids_section_tolerated(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        cfg = load(write(tmp_path, GOOD + "\n[kids]\nkid-a = kid-a\n"), hostname=lambda: "pc1")
    assert cfg.host_id == "pc1"
    assert "[kids]" in caplog.text


def test_agent_section_optional(tmp_path):
    cfg = load(write(tmp_path, GOOD.replace("[agent]\ninterval_s = 45\n", "")), hostname=lambda: "pc1")
    assert cfg.interval_s == 45


def test_remote_defaults_from_hostname(tmp_path):
    cfg = load(write(tmp_path, GOOD), hostname=lambda: "Kid-PC.fritz.box")
    assert (cfg.remote_host, cfg.rdp_port, cfg.vnc_base_port) == ("Kid-PC", 3389, 5900)


def test_remote_section_overrides(tmp_path):
    text = GOOD + "\n[remote]\nhost = 192.168.1.20\nrdp_port = 3390\nvnc_base_port = 5910\n"
    cfg = load(write(tmp_path, text), hostname=lambda: "pc1")
    assert (cfg.remote_host, cfg.rdp_port, cfg.vnc_base_port) == ("192.168.1.20", 3390, 5910)


@pytest.mark.parametrize("line", ["host = pc 1", "host = pc/1", "host = rdp://pc1", "rdp_port = 0",
                                  "rdp_port = 65536", "vnc_base_port = -1", "vnc_base_port = x"])
def test_bad_remote_rejected(tmp_path, line):
    with pytest.raises(ValueError):
        load(write(tmp_path, GOOD + "\n[remote]\n" + line + "\n"), hostname=lambda: "pc1")
