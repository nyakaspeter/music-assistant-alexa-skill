# Music Assistant Alexa Skill

A self-hosted Alexa bridge for Music Assistant, with spoken song, artist, album and playlist selection. Run it in Docker on a Linux server or NAS. AWS Lambda is not required.

Fork of [timlaing/music-assistant-alexa-skill](https://github.com/timlaing/music-assistant-alexa-skill). This fork adds voice selection, recommendation commands and skill restrictions. Music Assistant and its music providers supply the music and recommendations.

## How it works

Alexa sends signed skill requests to the bridge over HTTPS. The bridge searches and controls Music Assistant through its API. Music Assistant's Alexa player provider connects back to the bridge to start audio playback on an Echo. The Echo retrieves audio through Music Assistant's stream endpoint.

The application runs on your server; Amazon still processes voice commands and operates the Alexa service. A development skill can be enabled for its owner without publishing it to the Alexa Skills Store.

## Voice commands

The extended voice model is available in English (US), with invocation name **music assistant**:

| Command | Behavior |
| --- | --- |
| Alexa, ask Music Assistant to play Teardrop | Find a song and start a similar-song mix |
| Alexa, ask Music Assistant to play One by Metallica | Match the song title and artist |
| Alexa, ask Music Assistant to play songs by Linkin Park | Queue the artist's tracks |
| Alexa, ask Music Assistant to play the album Hybrid Theory | Queue an album in normal order |
| Alexa, ask Music Assistant to play the album One More Light by Linkin Park | Match the album title and artist |
| Alexa, ask Music Assistant to shuffle this album | Randomize the remaining unbuffered album tracks once |
| Alexa, ask Music Assistant to shuffle this playlist | Randomize the remaining unbuffered playlist tracks once |
| Alexa, ask Music Assistant what is playing | Announce the current song and artist |
| Alexa, ask Music Assistant what is next | Announce the next queued song and artist |
| Alexa, ask Music Assistant what played before this | Announce the preceding queue entry |
| Alexa, ask Music Assistant to play my playlist Favorites | Play an imported library playlist |
| Alexa, ask Music Assistant to play similar songs | Replace upcoming selections with a mix based on the current song |
| Alexa, ask Music Assistant to play music | Pick a random Music Assistant recommendation, with a library fallback |

Pause, resume, stop, next and previous use the bridge's playback controls. Similar-song requests preserve the current and already buffered audio, so a prepared next track may still play before the new mix.

Artist is optional for both songs and albums. A final “by artist” qualifier is parsed from the free-form request, and candidates must match that artist and title; a different artist is not silently substituted. Unqualified searches prefer exact titles, otherwise the first available result. Playlist requests prefer an exact library name and reject ambiguous matches.

Generic “play music” shuffles Music Assistant recommendation rows and randomly chooses an available track from the first row containing tracks. There is no YouTube Music preference or dependency. If no recommendation row provides tracks, it uses randomly ordered favorites, then library tracks. Similar-track radio is attempted when an available provider advertises support; otherwise, or if generating the mix fails, playback uses a normal selection from the library. Recommendations that contain only albums, artists or playlists are skipped by the track selector.

Album and playlist requests explicitly disable inherited shuffle when starting. “Shuffle this album” and “shuffle this playlist” reorder only the upcoming unbuffered tracks of a single finite album or playlist queue, preserving played tracks, queue item identities and its album or playlist source. It never enables persistent shuffle. Already buffered audio cannot be moved; very short remaining queues and mismatched or dynamic queues are rejected. Queue announcements describe the current queue, not a separate listening-history database. The next selection can change as a dynamic mix refreshes. Read-only announcements and one-time shuffle use a short response timeout; if playback advances while rearranging tracks, the operation stops and asks you to try again.

Radio-station selection, queue insertion and repeat voice commands are not added by this fork. The selection, announcements and shuffle commands use provider-independent Music Assistant APIs, subject to the music provider's supported features.

## Requirements

- Docker Compose on a Linux amd64 or arm64 host.
- Music Assistant with a working music provider and an API token.
- An Amazon developer account associated with the Echo devices.
- HTTPS access to the bridge's skill endpoint and Music Assistant's audio stream server, with certificates accepted by Alexa.
- An Alexa player provider configured in Music Assistant.

Use the **music assistant** invocation to match the standard Music Assistant Alexa provider; no Music Assistant server modifications are required. Updating an existing skill preserves its invocation name. If it differs, change and build its model in Amazon's developer console.

## Docker installation

Image:

```text
ghcr.io/nyakaspeter/music-assistant-skill:latest
```

Use [compose.nas.yaml](compose.nas.yaml) as a starting point. The image supports Linux amd64 and arm64. `latest` follows main; `sha-<full-commit-SHA>` identifies a specific build.

Set `APP_USERNAME` and `APP_PASSWORD` directly in Compose before starting. Choose a strong password instead of the example placeholder. The username defaults to `admin` in the sample.

```sh
mkdir -p data
docker compose -f compose.nas.yaml up -d
```

Open the setup wizard to configure the Music Assistant API token, public hostnames and Login with Amazon credentials. These are saved through the GUI, so they do not need environment variables.

The wizard automatically saves the allowed skill ID after verifying the deployment with Amazon. No skill ID or owner ID needs to be entered in Compose, and no credential `.txt` files or secrets mount are required.

Keep credentials and runtime data out of source control. `data/` stores settings, Amazon authorization state and device mappings. The GUI setup does not require a persistent ASK CLI directory. The application defaults to `/data` for settings, deployment state and device mappings, so no storage-path environment variables are needed. Back up this directory securely. Saved web settings override environment defaults.

The example publishes port **5050** on the host. Enter a Music Assistant control URL reachable from the bridge container in the setup wizard, such as `http://192.168.1.100:8095` or a reachable Docker service hostname. The Compose file does not assume that Music Assistant runs on the same host.

### Setup

1. Open `http://SERVER-IP:5050/setup` and sign in with `APP_USERNAME` and `APP_PASSWORD`.
2. Set the Music Assistant control URL and enter its API token.
3. Configure public skill and audio hostnames. For example, reverse-proxy `alexa-api.example.com` to bridge port 5050 and `alexa-stream.example.com` to Music Assistant port 8097. Select the certificate type matching the public endpoint.
4. Follow the wizard to register a Login with Amazon security profile, authorize developer access, and create or update a development skill. Keep invocation name `music assistant` and enable development testing.
5. Complete deployment in the wizard. It verifies the skill and saves its ID automatically.
6. In Music Assistant, add the Alexa player provider, supply the bridge API address and credentials, and complete its Amazon authentication.
7. Say “Alexa, open Music Assistant” on an Echo. Open `/devices` on the bridge, refresh, and map that device to its Music Assistant player. Repeat one Echo at a time.
8. Test a song request and check the bridge logs if playback does not start.

The setup wizard displays the OAuth callback URL that must be registered with Amazon. Skill requests are blocked until a verified skill has been configured through the wizard. An accepted voice command may acknowledge before playback preparation finishes; background errors appear in the logs.

The public skill endpoint must allow Alexa requests without an interactive login or browser challenge. Administrator pages use the bridge's own password protection. Cloudflare proxying can be used provided it forwards Alexa's requests and audio without blocking or challenging them.

### Updating

```sh
docker compose -f compose.nas.yaml pull
docker compose -f compose.nas.yaml up -d
```

An image update alone does not update the voice model hosted by Amazon. Use the setup wizard to review and deploy model changes.

## Access controls

The Alexa SDK validates Amazon signatures and request timestamps. This fork additionally requires the skill ID to match the wizard's verified deployment and disables simulator signature bypasses. It does not impose a separate owner-user allowlist: access to the skill is managed through Amazon's development/testing or distribution settings. Device mappings route authorized skill requests to the correct Music Assistant player.

The wizard persists the restriction in `/data/skill-access.json`. Updates recover the skill ID automatically from a previously completed wizard deployment when that file is absent. Before setup completes, skill requests fail closed while the management UI remains available. Credentials and deployment settings are supplied or saved at runtime, not included in the image. A public repository and image do not publish the Alexa skill.

## Development and builds

```sh
docker build -t music-assistant-skill:dev .
docker run --rm -v "$PWD:/project:ro" \
  --entrypoint /bin/sh music-assistant-skill:dev -c \
  '/app/venv/bin/pip -q install pytest && SKILL_APP_PATH=/app/src /app/venv/bin/python -m pytest /project/tests -q -p no:cacheprovider'
```

CI builds once on each native architecture, runs tests against that image, then publishes the tested image. Once both architectures succeed, it publishes the combined `latest` and commit tags. Pull requests build and test without publishing.

In addition to voice controls, this fork includes a Debian/Python 3.12 container, OpenSSL compatibility handling, device onboarding, skill restrictions, and normalization of Amazon package exports while retaining upstream deployment diagnostics. Dependency compatibility patches do not disable request verification.

## Related projects

- [Original bridge](https://github.com/timlaing/music-assistant-alexa-skill)
- [Music Assistant server](https://github.com/music-assistant/server)
- [Music Assistant Alexa provider documentation](https://www.music-assistant.io/player-support/alexa/)
- [Upstream Home Assistant add-on](https://github.com/timlaing/music-assistant-alexa-api)

The Home Assistant add-on is a separate packaging option and is not needed for this Docker deployment.
