# fake-geocodio (profile `geo`)

Deterministic stand-in for the [Geocodio](https://www.geocod.io/docs/) API on **http://127.0.0.1:8100**. No network, any `api_key`.

| Route | Purpose |
|-------|---------|
| `GET /v1.7/geocode?q=…&limit=N` | Forward geocode (any `v1.x` path version works) |
| `GET /v1.7/reverse?q=lat,lng&limit=N` | Reverse geocode |
| `GET /health` | Health |

Response shape matches Geocodio: `results[].formatted_address`, `location.{lat,lng}`, `address_components.{number,formatted_street,city,county,state,zip,country}`, `accuracy`, `accuracy_type`, `source`.

```bash
./scripts/start.sh geo
curl -s 'http://127.0.0.1:8100/v1.7/geocode?q=123+Main+St,+Laguna+Beach+CA&api_key=x'
curl -s 'http://127.0.0.1:8100/v1.7/reverse?q=33.54,-117.78&api_key=x'
```

Consumer wiring: point the Geocodio base URL at `http://127.0.0.1:8100` (e.g. `GEOCODIO_API_BASE`), or from compose at `http://geocodio:8100`.

**Limits (intentional):** a ~20-city built-in gazetteer. Queries naming one of those cities resolve near it (street addresses get a small stable offset). Unknown addresses get a stable pseudo-location in the continental US under the synthetic city "Localville, CA 90000". No batch, fields appends (census, timezone…), or Canada. Good for UI and persistence flows, not for real distance math.
