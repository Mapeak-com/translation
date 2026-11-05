# translation

The translation service behind Mapeak. It turns the free text description on a point of interest into the language the reader is using, so a Spanish description is useful to someone reading the map in Russian.

It is a small [LibreTranslate](https://libretranslate.com)-compatible API backed by [Google Cloud Translation](https://cloud.google.com/translate), with a caching nginx in front of it, in one container.

## Why Google, and not a local LLM

This used to run [LTEngine](https://github.com/LibreTranslate/LTEngine) with `gemma3` on a CPU. The translations were good, but the speed wasn't: time grows with output length, so a one-line description took ~4s and a paragraph ~40s. LTEngine also serves one request at a time, so a burst of different descriptions queued up past the app's 10s timeout.

Google's standard translation model returns the same paragraph in about 0.3s, with equal or better quality (it gets *cistern* right where gemma said *well*). Pricing is per character of source text; check [current prices](https://cloud.google.com/translate/pricing). Because of the cache, each text is paid for only once per target language.

## The cache is the interesting part

A translation never changes for a given source text, so nginx caches the response **keyed by the request body**:

```
proxy_cache_key "google|$request_uri|$request_body";
```

Content addressing is what makes this work against OSM data, which anyone can edit at any moment. An edit changes the text, which changes the key, so the next reader gets a fresh translation. There is no invalidation to get wrong and no change feed to subscribe to. Identical descriptions on different points - common in OSM boilerplate - collapse onto one entry for free. The `google|` prefix keeps answers cached by the old LLM backend from being served.

- `proxy_cache_lock on` collapses concurrent requests for the same new text into a single Google call.
- `proxy_ignore_client_abort on` finishes and caches a translation even when the caller has given up on it, so a call that was paid for is not thrown away.
- Only `200` responses are cached, so a Google error or quota problem is retried on the next request.

```
cold  ~0.3 - 0.5s  MISS
warm    0.002s     HIT
```

## Running it

```bash
docker run -d --name translation -p 5050:5050 \
  -e GOOGLE_API_KEY=... \
  -v translation-cache:/var/cache/nginx \
  ghcr.io/mapeak-com/translation:latest
```

**Mount the cache volume.** Without a named volume, recreating the container (every deploy of `:latest`) starts with an empty cache and pays Google again for everything.

The API is LibreTranslate's, so anything written against LibreTranslate works unchanged:

```bash
curl -X POST http://localhost:5050/translate \
  -H 'Content-Type: application/json' \
  -d '{"q":"Un manantial escondido entre las rocas","source":"es","target":"ru","format":"text"}'
```

Supported: `POST /translate` (JSON or form, `q` as a string or an array, `source: "auto"` with `detectedLanguage` in the response, `format` `text` or `html`) and `GET /languages`. `GET /health` is a liveness check that never calls Google.

Responses carry `X-Translation-Cache: HIT` or `MISS`, and the container logs one line per request with the cache status and timings - the quickest way to tell whether the cache is doing its job.

### The API key

Create the key in the Google Cloud console under **APIs & Services → Credentials**, and restrict it to the **Cloud Translation API**. Pass it only as an environment variable, never in the image or the repository.

| Variable | Default | |
| --- | --- | --- |
| `GOOGLE_API_KEY` | - | Required. The container exits at start without it. |
| `GOOGLE_TIMEOUT` | `20` | Seconds to wait for Google before answering `502`. |

## What's in this repository

- `adapter.py` translates between LibreTranslate's API and Google's Cloud Translation v2 API. Python standard library only, listening on `127.0.0.1:5051`. It has no state; caching is entirely nginx's job.
- `nginx.conf` is the caching front described above.
- `entrypoint.sh` runs the adapter and `nginx` on `5050`, the only port exposed. It exits if either dies, so a restart policy sees the failure.
- `Dockerfile` is Debian slim with nginx and python3, and builds in seconds.
- `.github/workflows/test.yml` is a smoke test that needs no real key: it builds the image, checks the container starts and nginx reaches the adapter, and that a failed translation comes back as a LibreTranslate-style error that is not cached.
- `.github/workflows/docker-publish.yml` publishes `ghcr.io/mapeak-com/translation:latest` for `linux/amd64` on every push to `main`.

## Switching to Google's Translation LLM

Google also offers a Gemini-based *Translation LLM* model through the v3 API. It needs a project ID and a service account rather than an API key, and is billed on input and output characters. The standard model was good enough on the descriptions tested, but switching would only touch `adapter.py`. Change the `google|` prefix in the cache key at the same time, or old answers keep being served.
