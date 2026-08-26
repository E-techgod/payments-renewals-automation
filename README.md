# Birthday Email Automation

## Project Purpose

This project is a single-run daily job that reads client birthdays from a spreadsheet, finds the clients whose birthday is today, routes each match to either the standard birthday email path or a BP call-reminder path, and uses a durable state store to prevent duplicate sends for the same client/date/year.

## Architecture Overview

High-level pipeline:

1. Load and validate configuration from environment variables.
2. Resolve "today" in `APP_TIMEZONE` or use `TEST_DATE`.
3. Read spreadsheet rows from either Google Sheets or a Drive-hosted `.xlsx` file.
4. Parse each row into a client record and skip invalid rows with warnings.
5. Match birthdays for today, including the Feb. 29-on-Feb. 28 rule in non-leap years.
6. Atomically claim each delivery in the configured state backend before attempting it.
7. If `Línea de servicio` contains `BP`, send an internal reminder to call the client instead of emailing the client directly.
8. Otherwise render the personalized birthday email, send it through Gmail, and record the final result.

`PLAN.md` section 1 is the authoritative detailed design if you want the full implementation flow.

## Setup

Requirements:

- Python `3.12+`
- [`uv`](https://docs.astral.sh/uv/)

Local setup:

```bash
uv sync
cp .env.example .env
```

Then fill in `.env` with your real values.

## Email Editing Workflow

Need to change the birthday email or BP reminder copy?

```text
edit app/email_content.py
    ↓
uv run pytest
    ↓
build + deploy
```

`[app/email_content.py](/Users/eliasarellanocampos/EAC/Quiron/happybd-automatization/app/email_content.py)` is the single default source of truth for the birthday email subject/body, BP reminder subject/body, greetings, signature wording, reminder recipient defaults, and default image settings. Use environment variables only when you intentionally want a deployment-specific override.

## Google Cloud Configuration

This project authenticates to Google APIs in one of two modes, selected by `GOOGLE_AUTH_MODE`:

- `service_account` (default) — a Google Cloud service account JSON key file referenced by `GOOGLE_CREDENTIALS_FILE`. This is the mode used for every deployment target described below.
- `oauth` — an interactive user consent flow, intended for local testing only (it opens a browser and a local server, so it does not run in Docker/cron/cloud deployments). See [Local testing with OAuth](#local-testing-with-oauth) below.

Application Default Credentials / keyless auth are not implemented.

### 1. Create a service account and JSON key

1. In Google Cloud, create or choose a project.
2. Enable the APIs you need:
   - Google Sheets API for `google_sheet` mode
   - Google Drive API for `xlsx_drive` mode
   - Gmail API for sending
3. Create a service account.
4. Create and download a JSON key for that service account.
5. Store the key on disk and set `GOOGLE_CREDENTIALS_FILE` to that path.

Example only:

```env
GOOGLE_CREDENTIALS_FILE=secrets/your-service-account.json
```

### 2. Share the spreadsheet file with the service account

This is required in both spreadsheet modes, and it is separate from Gmail delegation.

Share the target file directly with the service account's `client_email` from the JSON key:

- `google_sheet` mode: share the Google Sheet with the service account
- `xlsx_drive` mode: share the Drive-hosted `.xlsx` file with the service account

Spreadsheet access always uses the bare service account identity. It does not use impersonation.

### 3. Configure spreadsheet mode

`google_sheet` mode:

- Set `SPREADSHEET_MODE=google_sheet`
- Set `GOOGLE_SHEET_ID` from the sheet URL
- Optionally set `GOOGLE_SHEET_TAB`

Example sheet URL:

```text
https://docs.google.com/spreadsheets/d/your-google-sheet-id-here/edit
```

`xlsx_drive` mode:

- Set `SPREADSHEET_MODE=xlsx_drive`
- Set `GOOGLE_DRIVE_FILE_ID` from the Drive file URL

Example Drive URL:

```text
https://drive.google.com/file/d/your-google-drive-file-id-here/view
```

### 4. Spreadsheet columns and birthday formats

The app resolves these logical columns from the header row:

- `NAME_COLUMN` required
- `EMAIL_COLUMN` required
- `BIRTHDAY_COLUMN` required
- `LAST_SENT_YEAR_COLUMN` optional, informational only
- `GENDER_COLUMN` optional, used only to pick a Spanish salutation
- `SERVICE_LINE_COLUMN` optional, used to detect `BP` and override the standard email path
- `MOBILE_PHONE_COLUMN` optional, used only in the BP reminder content
- `BP_REMINDER_TO_ADDRESS_DEFAULT` optional, overrides the default BP primary recipient
- `BP_REMINDER_CC` optional, used only for BP reminder email CC recipients

`LAST_SENT_YEAR_COLUMN` is never the duplicate-send source of truth. The configured state backend is.

Routing rules:

- If `SERVICE_LINE_COLUMN` contains `BP`, after splitting on common delimiters such as commas, semicolons, or slashes and normalizing case/whitespace, the client goes to the BP override path.
- The BP override path does not email the client. It sends an internal reminder to `jorge.arellano@quirongroup.com` with the full name, birthday date, BP status, and a normalized mobile phone number from `MOBILE_PHONE_COLUMN`.
- When `BP_REMINDER_TO_ADDRESS_DEFAULT` is set, it overrides the default BP primary recipient without changing the BP reminder name or the standard birthday path.
- When `BP_REMINDER_CC` is set, the BP reminder also CCs the configured comma-separated addresses after trimming whitespace, dropping blanks, validating email format, and removing duplicates. Standard birthday emails are unchanged.
- BP reminder phone handling removes every non-digit character from `Móvil` and keeps only `0-9`. If the normalized result has fewer than 7 digits, the reminder renders `Móvil: No disponible` and still sends the reminder.
- If `BP` is not present, the standard personalized birthday email path is used.

Accepted birthday values:

- Native Excel/Sheets date values
- Excel serial date numbers
- `MM/DD/YYYY`
- ISO `YYYY-MM-DD`
- `"Month DD, YYYY"` such as `May 27, 2003`

### Local testing with OAuth

As an alternative to a service account, you can authenticate as your own Google user for local testing:

1. In the same Google Cloud project, create an OAuth client ID of type "Desktop app" and download its JSON file.
2. Set the following instead of `GOOGLE_CREDENTIALS_FILE`:

   ```env
   GOOGLE_AUTH_MODE=oauth
   GOOGLE_OAUTH_CLIENT_SECRETS_FILE=secrets/your-oauth-client.json
   GOOGLE_OAUTH_TOKEN_FILE=data/google_oauth_token.json
   GOOGLE_OAUTH_TOKEN_PERSIST=true
   ```

3. Install the OAuth dev dependency (`uv sync` picks it up automatically since it's in the `dev` dependency group).
4. Run the app. The first run opens a browser for consent and caches the resulting token at `GOOGLE_OAUTH_TOKEN_FILE`; later runs reuse it and, by default, persist silently refreshed credentials back to that file.

Notes:

- With your own Google account, no sharing step is needed for files you already own — read access follows your normal Drive/Sheets permissions.
- Gmail sends as your own mailbox. `GOOGLE_IMPERSONATE_SUBJECT` and domain-wide delegation (below) do not apply in `oauth` mode.
- The cached token file contains a live credential; it is already covered by `.gitignore` (`*token*.json`) and must never be committed.
- This mode is not supported in the Docker image or any unattended/cron deployment — use `service_account` there.

## Gmail Authorization

Gmail sending requires a second setup step in Google Workspace Admin, separate from sharing the sheet or Drive file.

The service account must be granted domain-wide delegation so it can impersonate `GOOGLE_IMPERSONATE_SUBJECT` for the Gmail scope:

- Scope: `https://www.googleapis.com/auth/gmail.send`

Important details:

- Gmail impersonation is required for sending.
- Spreadsheet access never uses impersonation.
- If `GOOGLE_IMPERSONATE_SUBJECT` is blank, the app defaults it to `EMAIL_FROM_ADDRESS`.
- The impersonated mailbox must be allowed to send as `EMAIL_FROM_ADDRESS`.

## Environment Variables

Copy `.env.example` to `.env` and fill in every required value for your deployment. Relative paths are resolved from the project root.

| Variable | Default | Purpose | Required when | Validation / notes |
|---|---|---|---|---|
| `APP_TIMEZONE` | `America/Chicago` | Timezone used to compute "today" when `TEST_DATE` is unset | Always | Must be a valid IANA timezone or startup fails |
| `DRY_RUN` | `true` | Runs the job without sending email or writing state | Always | Must be `true` or `false` only |
| `TEST_DATE` | empty | Overrides today's date for testing | Optional | Must be `YYYY-MM-DD` or startup fails |
| `SPREADSHEET_MODE` | `google_sheet` | Selects spreadsheet provider | Always | Must be `google_sheet` or `xlsx_drive` |
| `GOOGLE_SHEET_ID` | empty | Google Sheet ID to read | Required when `SPREADSHEET_MODE=google_sheet` | Required in sheet mode or startup fails |
| `GOOGLE_SHEET_TAB` | empty | Optional plain sheet/tab name | Optional | Blank reads the default/first sheet; when set, the app always reads that tab's full `A:ZZ` range |
| `GOOGLE_DRIVE_FILE_ID` | empty | Drive file ID for a shared `.xlsx` workbook | Required when `SPREADSHEET_MODE=xlsx_drive` | Required in Drive mode or startup fails |
| `NAME_COLUMN` | `Name` | Logical column header for client name | Always | Header matching is normalized by trim + casefold + diacritic removal |
| `LAST_NAME_COLUMN` | `Last Name` | Logical column header for client last name | Always | Used to build `display_name` when present |
| `GENDER_COLUMN` | `Gender` | Optional column used to pick a gender-aware Spanish salutation | Always | Missing or unrecognized values fall back to `Estimado/a`; never invalidates a row |
| `SERVICE_LINE_COLUMN` | `Línea de servicio` | Logical column header used for BP override routing | Always | Values are split on commas, semicolons, and `/`, then trim + casefold normalized; header matching is also diacritic-insensitive, so `Linea de Servicio` works |
| `MOBILE_PHONE_COLUMN` | `Móvil` | Logical column header used in BP reminders | Always | Header matching is diacritic-insensitive, so `Movil` works; values are normalized to digits only, and fewer than 7 digits renders as `No disponible` |
| `EMAIL_COLUMN` | `Email` | Logical column header for recipient email | Always | Same header normalization rules |
| `BIRTHDAY_COLUMN` | `Birthday` | Logical column header for birthday values | Always | Same header normalization rules |
| `LAST_SENT_YEAR_COLUMN` | `Last Birthday Email Year` | Optional informational column from the spreadsheet | Always | Not used for idempotency gating or writes |
| `EMAIL_PROVIDER` | `gmail` | Email provider selector | Always | Must be `gmail` |
| `EMAIL_FROM_NAME` | empty | Display name in the `From:` header and template signature | Optional | Can be blank |
| `EMAIL_FROM_ADDRESS` | empty | Sender mailbox address | Always | Must look like an email address or startup fails |
| `EMAIL_SUBJECT_TEMPLATE` | `Feliz cumpleaños, {{ display_name }}! 🎉` from `app/email_content.py` | Optional subject override | Always | Leave unset to use the centralized default; rendered with `name`, `last_name`, and `display_name` |
| `GOOGLE_AUTH_MODE` | `service_account` | Selects the Google auth mode | Always | Must be `service_account` or `oauth` |
| `GOOGLE_CREDENTIALS_FILE` | empty | Service account JSON key file path | Required when `GOOGLE_AUTH_MODE=service_account` | File must exist at startup |
| `GOOGLE_IMPERSONATE_SUBJECT` | empty | Gmail impersonation subject (service account mode only) | Optional | Must be an email if set; defaults to `EMAIL_FROM_ADDRESS` if blank |
| `GOOGLE_OAUTH_CLIENT_SECRETS_FILE` | empty | OAuth "Desktop app" client JSON path | Required when `GOOGLE_AUTH_MODE=oauth` | File must exist at startup |
| `GOOGLE_OAUTH_TOKEN_FILE` | `data/google_oauth_token.json` | Cached OAuth user token path | Used only when `GOOGLE_AUTH_MODE=oauth` | Relative to project root; created on first interactive consent |
| `GOOGLE_OAUTH_TOKEN_PERSIST` | `true` | Controls whether refreshed OAuth user credentials are written back to `GOOGLE_OAUTH_TOKEN_FILE` | Used only when `GOOGLE_AUTH_MODE=oauth` | Set to `false` for read-only token mounts such as Cloud Run Secret Manager volumes |
| `BIRTHDAY_IMAGE_MODE` | `local` from `app/email_content.py` | Optional image mode override | Always | Must be `none`, `local`, or `url` |
| `BIRTHDAY_IMAGE_PATH` | `app/assets/birthday_banner.jpg` from `app/email_content.py` | Optional local image path override | Required when `BIRTHDAY_IMAGE_MODE=local` | File must exist at startup |
| `BIRTHDAY_IMAGE_URL` | empty from `app/email_content.py` | Optional remote image URL override | Required when `BIRTHDAY_IMAGE_MODE=url` | Must be an `https://` URL |
| `BIRTHDAY_IMAGE_ALT` | `Happy Birthday` from `app/email_content.py` | Optional HTML alt text override | Always | Leave unset to use the centralized default |
| `BIRTHDAY_IMAGE_WIDTH` | `600` from `app/email_content.py` | Optional HTML width override | Always | Must be a positive integer |
| `STATE_BACKEND` | `sqlite` | State backend selector | Always | Must be `sqlite` or `firestore` |
| `STATE_DB_PATH` | `data/birthday_state.db` | SQLite state database path | Only when `STATE_BACKEND=sqlite` | Relative to project root; directory is created automatically |
| `FIRESTORE_DATABASE` | `birthday-automation` | Firestore database name | Only when `STATE_BACKEND=firestore` | Passed explicitly to the Firestore client |
| `STALE_CLAIM_TIMEOUT_MINUTES` | `30` | Reclaim window for stale `pending` claims | Always | Must be a positive integer |
| `RETRY_MAX_ATTEMPTS` | `3` | Retry attempts for transient Sheets/Drive API failures | Always | Must be a positive integer |
| `RETRY_BASE_DELAY_SECONDS` | `1.0` | Base exponential backoff delay for transient Sheets/Drive failures | Always | Must be a positive number |
| `LOG_LEVEL` | `INFO` | Root log level | Always | Passed through to Python logging |

### Shipped `.env.example`

```env
APP_TIMEZONE=America/Chicago
DRY_RUN=true
TEST_DATE=

SPREADSHEET_MODE=google_sheet

GOOGLE_SHEET_ID=
GOOGLE_SHEET_TAB=

GOOGLE_DRIVE_FILE_ID=

NAME_COLUMN=Name
LAST_NAME_COLUMN=Last Name
GENDER_COLUMN=Gender
SERVICE_LINE_COLUMN=Línea de servicio
MOBILE_PHONE_COLUMN=Móvil
EMAIL_COLUMN=Email
BIRTHDAY_COLUMN=Birthday
LAST_SENT_YEAR_COLUMN=Last Birthday Email Year

EMAIL_PROVIDER=gmail
EMAIL_FROM_NAME=
EMAIL_FROM_ADDRESS=
# Optional override. Edit app/email_content.py to change the default email subject everywhere.
EMAIL_SUBJECT_TEMPLATE=

GOOGLE_AUTH_MODE=service_account

GOOGLE_CREDENTIALS_FILE=
GOOGLE_IMPERSONATE_SUBJECT=

GOOGLE_OAUTH_CLIENT_SECRETS_FILE=
GOOGLE_OAUTH_TOKEN_FILE=data/google_oauth_token.json
GOOGLE_OAUTH_TOKEN_PERSIST=true

# Optional overrides. Edit app/email_content.py to change the default image settings everywhere.
BIRTHDAY_IMAGE_MODE=
BIRTHDAY_IMAGE_PATH=
BIRTHDAY_IMAGE_URL=
BIRTHDAY_IMAGE_ALT=
BIRTHDAY_IMAGE_WIDTH=

STATE_BACKEND=sqlite
STATE_DB_PATH=data/birthday_state.db
FIRESTORE_DATABASE=birthday-automation
STALE_CLAIM_TIMEOUT_MINUTES=30

RETRY_MAX_ATTEMPTS=3
RETRY_BASE_DELAY_SECONDS=1.0

LOG_LEVEL=INFO
```

## Local Execution

Once configured:

```bash
uv run run.py
```

If you are already inside the virtualenv, `python run.py` is also supported.

## Dry Run

Set:

```env
DRY_RUN=true
```

Dry run executes config loading, spreadsheet read, row parsing, and birthday matching, but it stops before claim, render, and send. It does not:

- send the client birthday email
- send the BP internal reminder
- claim or update send state in SQLite or Firestore

Use it when validating configuration, spreadsheet parsing, and birthday matching without affecting real recipients or duplicate-send protection state.

Known limitation (accepted): `load_config()` always calls `_validate_image_settings()`, so when `BIRTHDAY_IMAGE_MODE=local` the configured `BIRTHDAY_IMAGE_PATH` is checked eagerly at startup with `is_file()` on every run, even for `DRY_RUN=true` and zero-birthday-today days. What remains lazy lives in [app/birthday_service.py](/Users/eliasarellanocampos/EAC/Quiron/happybd-automatization/app/birthday_service.py): `_process_match()` returns early for `DRY_RUN=true` before claim, template/subject rendering, or send, and `_build_email_provider_accessor`, `_build_state_store_accessor`, and `_build_inline_image_accessor` defer Gmail provider construction/auth, state store construction and real backend usability, and the actual inline-image byte read (`read_bytes()`) until an actual send attempt. That means a clean no-op run still does not prove Gmail auth works, that the configured state backend is usable, that templates or `EMAIL_SUBJECT_TEMPLATE` render successfully, or that the image file's bytes are readable and valid for sending; those failures can stay hidden until the first real birthday send. Operators who want continuous readiness signal should periodically exercise the real send path deliberately, such as a scheduled `DRY_RUN=false` run against a synthetic test recipient/date via `TEST_DATE` outside the normal daily schedule, rather than treating an ordinary day's clean exit as proof the send path works.

## TEST_DATE Testing

To test birthday matching for a specific day, set:

```env
TEST_DATE=2026-05-27
```

This overrides the runtime date and lets you test matching behavior without waiting for a real birthday.

## Image Modes

- `none`: no image block is rendered
- `local`: attaches a local image inline and references it with `cid:`
- `url`: embeds a remote `https://` image URL in the HTML template

The shipped default is `local`, with a real placeholder banner at `app/assets/birthday_banner.jpg`.
Use Cloudinary to store the image as an url

## Automated Tests

Run the test suite with:

```bash
uv run pytest -q
```

All Google API interactions are mocked in tests. The suite does not perform real network calls and does not send real email.

## Quality Checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app
```

## Scheduling

Example cron entry for a daily `08:00` run:

```cron
0 8 * * * cd /opt/birthday-automation && /usr/bin/env -S uv run run.py >> /var/log/birthday-automation.log 2>&1
```

The process is intentionally single-run only. Use cron, systemd, or another external scheduler to start a fresh process each day.

## Docker Usage

Build the image:

```bash
docker build -t birthday-automation .
```

### Critical persistence warning

The image declares `VOLUME /app/data`, but that does not create durable storage by itself. Every `docker run` gets a fresh empty filesystem for that path unless you explicitly pass a bind mount or named volume. If you do not mount persistent storage for `/app/data`, duplicate-send protection resets on every run.

### Bind mount example

Before the first run, create a host directory and make it writable by uid:gid `1000:1000` because the image runs as non-root `appuser`:

```bash
mkdir -p /opt/birthday-automation/data
chown 1000:1000 /opt/birthday-automation/data
```

Run the container:

```bash
docker run --rm \
  --env-file .env \
  -v /opt/birthday-automation/data:/app/data \
  -v /opt/birthday-automation/secrets/service-account.json:/run/secrets/service-account.json:ro \
  birthday-automation
```

Your `.env` must point `GOOGLE_CREDENTIALS_FILE` at the mounted file path, for example:

```env
GOOGLE_CREDENTIALS_FILE=/run/secrets/service-account.json
```

### Named volume example

```bash
docker volume create birthday-automation-data

docker run --rm \
  --env-file .env \
  -v birthday-automation-data:/app/data \
  -v /opt/birthday-automation/secrets/service-account.json:/run/secrets/service-account.json:ro \
  birthday-automation
```

### Secrets handling

- Never bake secrets or a real `.env` into the image.
- Pass environment variables with `--env-file` or your orchestrator's secret mechanism.
- Mount the service-account JSON as a file and reference it through `GOOGLE_CREDENTIALS_FILE`.

### Cloud deployment guidance

Primary supported cloud target:

- A Compute Engine VM, or any similar VM, running this container from cron or systemd with a normal persistent disk mounted into `/app/data`

This is the recommended target because the application depends on a local SQLite database using WAL mode and real POSIX file locking for duplicate-send protection.

Do not use Cloud Run Jobs native volume mounts for `STATE_DB_PATH`. Those mounts use Cloud Storage FUSE, which is unsafe for this app's SQLite/WAL state store and can break locking guarantees or corrupt state. That makes duplicate-send protection unreliable. Cloud Run Jobs with GCS FUSE must not be used for persistent state for this project.

Cloud Run Jobs is supported when you switch to Firestore-backed state:

- Set `STATE_BACKEND=firestore`
- Do not set `STATE_DB_PATH`
- Set `FIRESTORE_DATABASE=birthday-automation` unless you intentionally use a different Firestore database
- If `GOOGLE_AUTH_MODE=oauth` and `GOOGLE_OAUTH_TOKEN_FILE` comes from a read-only Secret Manager mount, set `GOOGLE_OAUTH_TOKEN_PERSIST=false`
- Ensure the Cloud Run Job service account has Firestore access in the target Google Cloud project
- Firestore authentication uses Google Application Default Credentials automatically; do not mount or hardcode a Firestore credential file just for state

With `STATE_BACKEND=firestore`, the app keeps the same claim/lease/deduplication semantics while moving state coordination to Firestore transactions instead of local SQLite locking.

GKE can work if you use a real block-storage-backed PersistentVolumeClaim instead of object or network-backed storage, but it also requires Kubernetes-specific writable-volume setup for uid `1000` such as `securityContext` / `fsGroup` or an init container. That setup is beyond this README.

Every deployment target in this project requires a real service-account key file via `GOOGLE_CREDENTIALS_FILE`. Example cloud pattern: mount a Secret Manager secret as a file and point `GOOGLE_CREDENTIALS_FILE` at that mounted path.

## Troubleshooting

`ConfigError` at startup:

- Compare your `.env` against the environment variable reference above.
- Common causes are a missing `GOOGLE_CREDENTIALS_FILE`, invalid `TEST_DATE`, invalid email address, or an image mode/path mismatch.

Spreadsheet access errors:

- Confirm the target Google Sheet or Drive `.xlsx` file was shared directly with the service account `client_email`.
- Confirm the correct ID was set for the selected `SPREADSHEET_MODE`.

Gmail send errors:

- Confirm Gmail API access is enabled.
- Confirm domain-wide delegation was configured in Google Workspace Admin for the service account.
- Confirm the impersonation subject is correct and can send as `EMAIL_FROM_ADDRESS`.

Critical ambiguous-send log:

- If you see the `CRITICAL` log stating the send outcome is unknown, do not assume the message failed and do not assume it duplicated.
- Check the mailbox manually before the stale-claim window elapses.
- The claim is intentionally left `pending` so it is not immediately reclaimable; after `STALE_CLAIM_TIMEOUT_MINUTES`, a future run may retry and could double-send if the original request actually succeeded.
- A rerun before `STALE_CLAIM_TIMEOUT_MINUTES` elapses can see the same claim as `IN_PROGRESS` instead of ambiguous, but that rerun still exits non-zero because `in_progress` also contributes to exit status 1. The exit code alone still does not distinguish a routine overlapping-run `IN_PROGRESS` from an unresolved ambiguous-send incident, so operators must read the actual log output and treat any earlier `CRITICAL` ambiguous-send event as authoritative until the mailbox is verified manually.
