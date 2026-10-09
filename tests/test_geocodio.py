import httpx

from conftest import GEOCODIO, require_port


def test_geocodio_forward_and_reverse():
    require_port(8100, "fake-geocodio")
    with httpx.Client(timeout=10.0) as c:
        r = c.get(f"{GEOCODIO}/v1.7/geocode", params={"q": "123 Main St, Laguna Beach CA", "api_key": "x", "limit": 5})
        assert r.status_code == 200
        res = r.json()["results"][0]
        assert res["address_components"]["city"] == "Laguna Beach"
        assert abs(res["location"]["lat"] - 33.54) < 0.1
        again = c.get(f"{GEOCODIO}/v1.7/geocode", params={"q": "123 Main St, Laguna Beach CA"}).json()
        assert again["results"][0]["location"] == res["location"]  # deterministic
        rev = c.get(f"{GEOCODIO}/v1.7/reverse", params={"q": "33.54,-117.78", "limit": 1}).json()
        assert rev["results"][0]["address_components"]["state"] == "CA"
