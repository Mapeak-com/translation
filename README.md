# translation

The translation service behind Mapeak. It turns the free text description on a point of interest into the language the reader is using, so a Spanish description is useful to someone reading the map in Russian.

It is [LTEngine](https://github.com/LibreTranslate/LTEngine) - a LibreTranslate-compatible API backed by a local LLM - with a caching nginx in front of it, in one container.

## The cache is the interesting part

An LLM translation costs seconds, and LTEngine serves one request at a time behind a mutex. Neither matters much here, because a translation never changes for a given source text, so nginx caches the response **keyed by the request body**:

```
proxy_cache_key "$request_uri|$request_body";
```

Content addressing is what makes this work against OSM data, which anyone can edit at any moment. An edit changes the text, which changes the key, so the next reader gets a fresh translation. There is no invalidation to get wrong and no change feed to subscribe to. Identical descriptions on different points - common in OSM boilerplate - collapse onto one entry for free.

Two directives carry most of the weight:

- `proxy_cache_lock on` collapses concurrent requests for the same new text into a single call, so a burst of readers opening the same point does not queue up behind LTEngine's mutex.
- `proxy_ignore_client_abort on` finishes and caches a translation even when the caller has given up on it. The app times out after 10s and falls back to showing the untranslated text, but the answer is warm for the next reader - so the model never has to be fast enough to fit inside the request someone is waiting on. This is what makes a large, slow, accurate model usable without a GPU.

The practical effect, measured on real point of interest descriptions:

```
cold  2.5 - 3.0s   MISS
warm      0.005s   HIT
```

## Running it

```bash
docker run -d --name translation -p 5050:5050 -v translation-models:/models \
  ghcr.io/mapeak-com/translation:latest
```

The API is LibreTranslate's, so anything written against LibreTranslate works unchanged:

```bash
curl -X POST http://localhost:5050/translate \
  -H 'Content-Type: application/json' \
  -d '{"q":"Un manantial escondido entre las rocas","source":"es","target":"ru","format":"text"}'
```

Responses carry `X-Translation-Cache: HIT` or `MISS`, which is the quickest way to tell whether the cache is doing its job.

## Choosing a model

`LTENGINE_MODEL` picks the model, which is downloaded into `/models` on first start. Keep that as a volume or every restart re-downloads it.

| Model | RAM | Notes |
| --- | --- | --- |
| `gemma3-1b` | ~1 GB | Unusable. It mistranslates common outdoor terms. |
| `gemma3-4b` | ~4 GB | Good English, but invents place names when translating into other languages. |
| `gemma3-12b` | ~8 GB | **Default.** Correct proper nouns in every target. ~8s per translation on a CPU. |
| `gemma3-27b` | ~16 GB | Better still, but slow enough that it is hard to justify over 12b. |

Translation speed is bound by memory bandwidth rather than cores, so 12b is about 3x slower than 4b - the ratio of their weights. With the cache in front, that only ever costs the first reader of a given description.

## What's in this repository

- `Dockerfile` builds LTEngine from source, CPU-only, so one image runs on both amd64 and arm64. `LTENGINE_REF` pins the upstream commit, so rebuilding the same tag cannot quietly change how things translate. Bump it deliberately.
- `nginx.conf` is the caching front described above.
- `entrypoint.sh` runs `ltengine` on `127.0.0.1:5051`, not reachable from outside the container, and `nginx` on `5050`, the only port exposed. It exits if either dies, so a restart policy sees the failure.
- `.github/workflows/test.yml` builds the image, translates a sentence with `gemma3-1b`, and checks that the same request comes back as a cache `HIT`.
- `.github/workflows/docker-publish.yml` publishes `ghcr.io/mapeak-com/translation:latest` for `linux/amd64` on every push to `main`.
