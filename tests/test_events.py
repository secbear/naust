from naust.agent.events import Event, EventFactory


def test_cloudevent_envelope() -> None:
    factory = EventFactory.for_world("host-1", "midgard")

    ce = factory.cloudevent(Event("backend.ready", {"pid": 7}), sequence=3)

    assert ce["specversion"] == "1.0"
    assert ce["type"] == "io.naust.backend.ready"
    assert ce["source"] == "naust://host-1/worlds/midgard"
    assert ce["naustsequence"] == 3
    assert ce["datacontenttype"] == "application/json"
    assert ce["data"] == {"pid": 7}
    assert ce["time"].endswith("+00:00")
    assert len(ce["id"]) == 36


def test_ids_are_unique() -> None:
    factory = EventFactory(source="naust://h/worlds/w")
    a = factory.cloudevent(Event("x", {}), 1)
    b = factory.cloudevent(Event("x", {}), 2)
    assert a["id"] != b["id"]
