def test_seeds_from_config_yaml(client):
    body = client.get("/api/layout").json()
    assert body["revision"] == 1
    assert [t["id"] for t in body["layout"]["groups"][0]["tiles"]] == ["site", "lamp", "repo"]


def test_save_and_conflict(client):
    layout = client.get("/api/layout").json()["layout"]
    layout["groups"][0]["title"] = "Renamed"

    saved = client.put("/api/layout", json={"layout": layout, "base_revision": 1})
    assert saved.status_code == 200
    assert client.get("/api/layout").json()["layout"]["groups"][0]["title"] == "Renamed"

    stale = client.put("/api/layout", json={"layout": layout, "base_revision": 1})
    assert stale.status_code == 409


def test_rejects_duplicate_ids(client):
    tile = {"id": "same", "integration": "link"}
    layout = {"groups": [{"id": "g", "title": "G", "tiles": [tile, tile]}]}
    assert client.put("/api/layout", json={"layout": layout}).status_code == 422


def test_yaml_round_trip_and_restore(client):
    exported = client.get("/api/layout/export").text
    assert "integration: homeassistant" in exported

    assert client.post("/api/layout/import", content="groups: []",
                       headers={"content-type": "application/yaml"}).status_code == 200
    assert client.get("/api/layout").json()["layout"]["groups"] == []

    client.post("/api/layout/revisions/1/restore")
    assert len(client.get("/api/layout").json()["layout"]["groups"]) == 1


def test_import_reports_bad_yaml(client):
    response = client.post("/api/layout/import", content="groups: [{tiles: 3}]",
                           headers={"content-type": "application/yaml"})
    assert response.status_code == 422
    assert "isn't valid" in response.json()["detail"]
