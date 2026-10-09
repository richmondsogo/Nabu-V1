Right — inverted. Local is a cache, not the source. Anna's Archive and Libgen are the actual library. Two sources, not three.

Changes from the last version, then the full prompt.
WHAT CHANGED

    Preamble — shadow libraries are the primary book-data infrastructure. Local catalog is a local cache and index.
    Dropped Z-Library — two sources: Anna's Archive, then Libgen.
    SOURCE_PRIORITY=annas,libgen — AA first, because it aggregates Libgen and hands you IPFS CIDs.
    Local miss auto-scrapes — no button tap required. The button is now "show more results", not "go to the internet".
    AA path is CID-first — resolve to a CID, pull it through the local node, pin it. No HTTP download needed when AA gives you a CID.
    Sections reordered — SOURCES before ACQUISITION.
    Gateways promoted — from "optional fallback" to a real route when the local node can't reach a CID in time.
    Acquisition workers always on — ENABLE_ACQUISITION no longer disables the whole thing.
    Structure: sources/annas.py, sources/libgen.py. No zlibrary.py.

Everything else is unchanged.
THE PROMPT

Build a fully local, self-hosted Telegram bot for searching and delivering books from Anna's Archive and Libgen, cached into a local metadata database and pinned into a local IPFS/Kubo node.

The bot is intended for a small private whitelist of users and for books/files that the operator is authorized to distribute.

DO implement scraping, acquisition, copyright circumvention, access-control bypass, CAPTCHA bypass, paywall bypass, and source-specific retrieval from unauthorized book repositories.

The shadow libraries are the primary book-data layer. Anna's Archive first, then Libgen. The local SQLite catalog is a cache and index: it exists so a repeat request is instant and so the operator keeps a durable, auditable record of what has been fetched. The local Kubo node is where fetched files are pinned.

GOAL

Build a small, reliable Telegram bot with this flow:

User sends:
"clean code"

Bot searches the local cache.

Cache hit with a valid CID: deliver immediately from the local Kubo node.

Cache miss, or a hit with no CID: the bot automatically goes out to Anna's Archive, then Libgen, resolves a download, streams it, validates it, imports it into the local catalog, pins it into the local Kubo node, and delivers it.

User sends:
"clean code"

Bot returns up to five results:

[Clean Code — Robert C. Martin]
[The Clean Coder — Robert C. Martin]
[...]
[🌐 More results from Anna's Archive and Libgen]

User taps a result.

Bot immediately places a download job into an in-memory asyncio.Queue.

A fixed pool of exactly 3 workers processes downloads concurrently.

Each worker retrieves the requested CID from the local Kubo daemon at:

http://127.0.0.1:5001

The file is written to a temporary local file, validated, sent to the requesting Telegram chat, and deleted immediately afterward.

The bot must remain responsive while searches and downloads occur.

TECH STACK

Python 3.11+

python-telegram-bot current stable release using ApplicationBuilder and native asyncio.

SQLite3 with FTS5.

httpx.AsyncClient for asynchronous communication with Kubo and with the book sources.

python-dotenv for configuration.

No external database.

No Redis.

No Celery.

No cloud storage.

No public IPFS gateway for normal delivery. Public gateways are allowed only as a route for fetching a source-supplied CID when the local node cannot reach it in time.

No external book API service. Anna's Archive and Libgen are scraped directly.

Minimal dependencies.

Use structured logging rather than print().

PROJECT STRUCTURE

Use this structure unless there is a strong reason to simplify it:

telegram-book-bot/
bot.py
config.py
database.py
ipfs.py
queue_manager.py
models.py
importer.py
acquirer.py
sources/
base.py
annas.py
libgen.py
resolver.py
requirements.txt
.env.example
data/
books.db
tmp/
tests/
test_database.py
test_queue.py
test_ipfs.py
test_utils.py
test_sources.py
test_acquirer.py

Do not unnecessarily fragment the project.

CONFIGURATION

Environment variables:

TELEGRAM_TOKEN
TELEGRAM_ALLOWED_USER_IDS
DB_PATH=data/books.db
IPFS_API_URL=http://127.0.0.1:5001
MAX_CONCURRENT_DOWNLOADS=3
DOWNLOAD_TIMEOUT=90
TEMP_DIR=tmp
MAX_TELEGRAM_FILE_SIZE=52428800

SOURCE_PRIORITY=annas,libgen
MAX_ACQUIRE_JOBS=2
ACQUIRE_TIMEOUT=300
SCRAPE_TIMEOUT=20
POLITE_DELAY_MS=750
AUTO_ACQUIRE=true
MIRROR_ANNA=https://annas-archive.org,https://annas-archive.se
MIRROR_LIBGEN=https://libgen.is,https://libgen.rs,https://libgen.st
AA_API_KEY=
PUBLIC_GATEWAYS=https://ipfs.io/ipfs,https://dweb.link/ipfs

The whitelist must be parsed into a set of integer Telegram user IDs.

Reject non-whitelisted users politely and do not expose search results to them.

When AUTO_ACQUIRE is false the bot only serves the local cache and the sources are reached only via the explicit button.

DOWNLOAD_TIMEOUT applies to a local Kubo retrieval. ACQUIRE_TIMEOUT applies to a whole acquisition.

DATABASE

Use SQLite.

Enable:

PRAGMA journal_mode=WAL;

Use parameterized SQL everywhere.

Create:

books

with:

id INTEGER PRIMARY KEY
title TEXT NOT NULL
author TEXT
cid TEXT
md5 TEXT
filename TEXT
file_size INTEGER
file_type TEXT
description TEXT
created_at TEXT NOT NULL

Also add, additive and nullable so an existing books.db keeps working:

source TEXT
source_id TEXT
acquired_at TEXT
pinned INTEGER NOT NULL DEFAULT 0

Create:

sources

with:

id INTEGER PRIMARY KEY
name TEXT NOT NULL UNIQUE
enabled INTEGER NOT NULL DEFAULT 1
last_ok TEXT
last_error TEXT
cooldown_until TEXT

Create:

acquisitions

with:

id INTEGER PRIMARY KEY
book_id INTEGER
md5 TEXT
source TEXT NOT NULL
source_id TEXT
status TEXT NOT NULL
requested_by INTEGER
requested_at TEXT NOT NULL
finished_at TEXT
error TEXT

Use CID as the primary retrieval identifier, but DO NOT put the CID directly into Telegram callback_data.

Create an FTS5 index over:

title
author
description

Search should support:

exact titles
partial titles
multiple words
author searches
reasonable relevance ranking

Return a maximum of five results.

Enforce a unique index on md5 where not null. Never overwrite an existing row.

A row may exist with a null CID. That means the book is known and searchable but not yet fetched.

Database access must never block the Telegram event loop.

Use asyncio.to_thread() around synchronous sqlite3 operations, or another minimal async-safe wrapper.

Do not create a complicated database abstraction.

SEARCH UX

Commands:

/start
/help
/queue
/status

Plain text messages should be interpreted as searches.

For example:

User:
the pragmatic programmer

Bot:

Search results:

[The Pragmatic Programmer — Andrew Hunt]
[The Pragmatic Programmer 20th Anniversary Edition — Andrew Hunt]

Local results come back in under a second. When the local cache returns fewer than five results, append one extra button:

[🌐 More results from Anna's Archive and Libgen]

When AUTO_ACQUIRE is true and the local search returned nothing, the source search fires immediately without waiting for a tap. Do not fire it when local already returned five hits with valid CIDs.

Each callback should contain only a compact internal book ID, for example:

book:1842

Never embed the entire CID in callback_data.

Callback formats:

book:1842
web:<md5-prefix>
pick:<acquisition-id>:<n>

When a callback is received:

answer the callback query immediately
verify the user is whitelisted
parse the book ID
look up the book in SQLite
enqueue the job
report its queue position

DOWNLOAD QUEUE

Implement:

asyncio.Queue[DownloadJob]

Create exactly three long-lived worker tasks by default.

Do not create one asyncio task per download that independently talks to Kubo.

Workers must consume from the shared queue.

Track:

queued jobs
active jobs
completed jobs
failed jobs

A job should contain:

book_id
user_id
chat_id
job_id
enqueued_at

Prevent the same user from enqueueing the same book repeatedly while an identical job is already pending or active.

QUEUE POSITION

Do not simply rely on queue.qsize() for all state.

Maintain a small queue manager that can calculate a user's approximate FIFO position when the request is accepted.

The message should look like:

⏳ Added to the download queue.

Position: 3

Book:
Clean Code — Robert C. Martin

Position refers to pending jobs ahead of this job plus the active worker capacity as appropriate.

Keep this implementation simple and deterministic.

IPFS

Create an async Kubo client using httpx.AsyncClient.

Do not use a public gateway for normal delivery.

Do not shell out to the ipfs executable for normal retrieval.

Use the local Kubo RPC API.

The primary retrieval operation is:

POST /api/v0/cat?arg=<CID>

The pin operation is:

POST /api/v0/pin/add?arg=<CID>

The ingest operation for an HTTP-downloaded file is:

POST /api/v0/add?pin=true

Kubo should remain bound to localhost.

Never require exposing port 5001 to the LAN or Internet.

The client must:

reuse one AsyncClient
have reasonable connection limits
handle HTTP errors
stream response data
close responses correctly

90 SECOND TIMEOUT

Every complete Kubo retrieval must have a hard 90-second deadline.

Use Python 3.11's:

asyncio.timeout(90)

around the complete retrieval operation.

Do not rely solely on per-read HTTP timeouts.

On timeout:

cancel the retrieval
close the HTTP response
delete the temporary file
notify the requesting user
mark the job failed
continue to the next queued job

The bot must never become stuck because a Kubo retrieval hangs.

FILE HANDLING

Never write directly to the final filename.

Create:

tmp/<job-id>.part

Stream the response there.

After successful retrieval:

inspect the first bytes
identify file type
verify the content is plausible
determine file size
compare against Telegram's maximum
atomically rename if needed
upload
delete the file

Always clean files in a finally block.

There must be no orphaned temporary files after normal failure paths.

FILE TYPE DETECTION

Support at least:

PDF
EPUB
MOBI
AZW/AZW3
ZIP

Use magic-byte detection where practical rather than trusting the filename.

Examples:

PDF:
%PDF-

EPUB:
ZIP container with EPUB metadata when validation is practical.

MOBI/AZW:
recognize the known MOBI/Kindle container signatures when possible.

Do not pretend an arbitrary response is a PDF because its URL ends with .pdf.

This matters more than usual here: a shadow library will happily hand you an HTML error page with a .pdf name.

TELEGRAM SIZE LIMIT

The current Telegram Bot API document upload limit is 50 MB.

Treat:

52428800 bytes

as the hard maximum.

If the database knows the file is larger than this:

do not start the IPFS download.

Tell the user:

"⚠️ This file is too large for Telegram to deliver."

If the size is unknown:

stream the file while enforcing the maximum.

If it exceeds the maximum:

abort the Kubo retrieval,
delete the partial file,
notify the user,
and continue processing the queue.

Do not leave the oversized file on disk.

The same rules apply to an acquisition download.

TELEGRAM DELIVERY

Use:

await context.bot.send_document(...)

or the appropriate Bot API method.

Send:

the file
a useful filename
a short caption containing the title and author

Do not read the entire file into memory.

Upload from the temporary file handle/path.

Give Telegram upload operations their own sensible timeout. The 90-second timeout applies to the Kubo retrieval, not necessarily the Telegram upload.

SOURCES

Two sources. Anna's Archive first, then Libgen. Create a sources/ package, one module per source, no lowest-common-denominator scraping logic.

Each adapter MUST implement:

search(query, limit) -> list[SearchHit]
resolve(hit) -> DownloadHandle
download(handle, dest, max_bytes, timeout) -> bytes_written
available() -> bool

SearchHit carries at least: source, source_id, md5, title, author, year, language, extension, filesize, detail_url, cid, raw.

A DownloadHandle is one of two things, and the resolver must be able to tell them apart:

kind = "cid" -> fetch through the local node
kind = "url" -> stream over HTTP

Anna's Archive

Primary source. Rotate the configured mirrors on failure. Never hardcode one domain.

Metadata from the search page and the /md5/<md5> detail page.

Prefer the IPFS CID when the detail page or the published md5-to-CID mapping exposes one. When a CID is available, return a DownloadHandle of kind cid and skip HTTP entirely.

Slow download is fine. Poll the queue page, do not hammer it.

Fast download API only when AA_API_KEY is set.

Libgen

Second source. Rotate mirrors across the configured list.

Prefer the JSON metadata endpoint over HTML where it exists.

Download via the get.php style endpoint. Follow redirects with a hop cap.

No waitlist, so it is the reliable fallback when Anna's Archive is cooling down.

Resolver

Query sources sequentially in SOURCE_PRIORITY order. Stop at the first source that yields a usable hit with a resolvable download. Do not scrape in parallel by default.

Deduplicate hits across sources by md5, then by normalized title and author.

Prefer a hit that carries a CID over one that only carries a URL.

If a hit's CID is already pinned locally, return immediately without touching the network.

An adapter failure is isolated. One broken source never breaks the chain.

ACQUISITION

The sources are the main layer. The local cache exists to make the second request free.

Acquisition flow:

local search misses, or the hit has no CID
resolver picks the first healthy source by SOURCE_PRIORITY
source.search(query) -> candidate hits
if more than one plausible hit, let the user pick, capped at five
source.resolve(hit) -> a CID or a download URL
if kind is cid: cat it through the local node into tmp/<job-id>.part, then pin it
if kind is url: stream it to tmp/<job-id>.part with a hard deadline and the size cap enforced mid-stream, then ipfs add it with pinning enabled
validate by magic bytes
insert or update the row in SQLite with source, source_id, md5, acquired_at and the CID
deliver to Telegram
delete the temporary file

Rules:

Acquisition runs on its own asyncio.Queue and its own worker pool. Default MAX_ACQUIRE_JOBS=2. A slow scrape must never starve a local delivery.

Same worker shape as the delivery queue. try / except / log / notify / finally / task_done.

One failed acquisition must never stop either queue.

One active acquisition per user. Queue the rest.

Deduplicate globally by md5. Two users requesting the same missing book must cause exactly one download.

Every acquisition has a hard ACQUIRE_TIMEOUT covering search, resolve and download, enforced with asyncio.timeout.

Insert the DB row only after validation passes. Never pollute the catalog with junk.

If the CID is already pinned locally, skip both the download and the pin.

Clean the temporary file in a finally block.

If the local node cannot retrieve a source-supplied CID within DOWNLOAD_TIMEOUT, retry once through a public gateway before failing.

Report progress by editing one status message in place:

🔎 Searching Anna's Archive…
📥 Downloading from Anna's Archive… (4.2 MiB)
✅ Added to the local library.
📦 Delivering…

SCRAPING HYGIENE

One shared httpx.AsyncClient per source, with its own cookie jar and connection pool. Never one global client.

If a mirror sits behind Cloudflare, that source's client may use curl_cffi with browser impersonation. This is the only optional dependency allowed.

Rotate a small realistic User-Agent pool. Keep header order consistent.

Enforce POLITE_DELAY_MS between requests to the same host, with a per-host lock.

Exponential backoff with jitter on 429 and 503. Cap at three attempts.

Never retry a 404.

After repeated failure, cool the source down and move on. Log it. Anna's Archive cooling down must immediately hand over to Libgen.

ERROR MESSAGES

Keep user-facing errors simple.

Kubo timeout / unavailable:

⚠️ File could not be retrieved from IPFS right now. Try again later.

Missing CID:

⚠️ This book does not currently have a valid IPFS file reference.

Too large:

⚠️ This file is too large for Telegram to deliver.

Telegram upload failure:

⚠️ The file was retrieved but Telegram could not deliver it.

Could not reach any source:

⚠️ Could not reach any book source right now. Try again later.

Found but stuck:

⚠️ Found it, but the download is stuck. Try again later.

Found but corrupt:

⚠️ Found it, but the file looks corrupted.

Nothing anywhere:

⚠️ Not found on any source.

Never expose stack traces or internal errors to users.

Log the detailed exception server-side.

COMMANDS

Implement:

/start

Explain how to search.

If user sends text:
perform a search.

When results exist:
show inline buttons.

When no results:
say:

No books found for "<query>".

Then, if AUTO_ACQUIRE is on, follow with a live status message while the sources are searched.

/queue

Show:

Active: 2
Waiting: 4

and the requesting user's own pending downloads.

Do not reveal other users' private request details.

/status

Show:

Bot: online
Database: OK
Kubo: OK/OFFLINE
Active downloads: 2/3
Queue: 4

The Kubo health check should be lightweight.

/get <query>

Force the source path even when the local cache has hits.

/fetch <book_id>

Acquire a specific local row that has no CID.

/sources

Per-source enabled, cooling, last success, last error.

/acquire

Your own active acquisitions only.

/rebuild

Rebuild the FTS index. Cheap insurance for a cache that gets written to often.

ACCESS CONTROL

Only Telegram user IDs in:

TELEGRAM_ALLOWED_USER_IDS

may interact with the bot.

Check authorization inside handlers, not only through a global filter.

For unauthorized users:

"Sorry, this bot is private."

Do not reveal whether a searched book exists.

STARTUP

Use the ApplicationBuilder pattern.

On startup:

load configuration
open/check database
create missing database schema
seed the sources table
start the Kubo client
verify Kubo connectivity
create the three delivery queue workers
create the acquisition workers
register handlers
start polling

Use Application post_init/post_shutdown hooks where appropriate.

SHUTDOWN

On graceful shutdown:

stop accepting new jobs
cancel worker tasks
drain/cancel queued jobs
delete temporary files
close the HTTP clients
close database resources
allow Telegram application shutdown to complete cleanly

No orphaned worker tasks.

No orphaned .part files.

DATABASE IMPORTER

Create:

importer.py

The bot builds its own catalog as it acquires, but the importer is still there to seed it from a bulk dump or to re-attach CIDs in bulk.

It should import an existing CSV or JSON metadata file into SQLite.

Support fields such as:

title
author
cid
md5
filename
file_size
file_type
description

Normalize missing values.

Reject rows without a usable title.

Allow CID to be null for records that are searchable but not currently downloadable.

Prevent duplicate CIDs.

Do not silently overwrite existing records.

Support an optional md5-to-CID mapping file so an operator can backfill CIDs in bulk.

Print a concise import report:

Imported: 8,421
Skipped: 13
Duplicates: 42
Invalid: 3

TESTING

Write real tests for:

FTS searches
exact title search
partial title search
author search
callback ID parsing
whitelist enforcement
queue FIFO behavior
three-worker concurrency limit
duplicate job prevention
timeout handling
file cleanup
PDF magic-byte validation
EPUB/container validation
Telegram size enforcement
missing CID handling

Also test:

each source adapter against recorded HTML and JSON fixtures, not the live site
resolver priority order, mirror rotation, and cross-source dedupe
a source raising an exception does not stop the chain
a CID handle skips HTTP entirely
a failed acquisition leaves no row and no .part file
two users requesting the same missing book produce exactly one download
acquisition never exceeds MAX_ACQUIRE_JOBS
the delivery queue stays responsive while an acquisition is in flight
a cache hit never touches the network

For IPFS, use a mocked/local test HTTP server rather than a public gateway.

Test that four simultaneous jobs never result in more than three active Kubo retrievals.

LOGGING

Use Python's logging module.

Prefer structured messages containing:

job_id
book_id
user_id
cid
source
status
duration
size

Do not log Telegram bot tokens.

Do not log cookies.

Do not log unnecessary personal data.

RELIABILITY

The most important invariant is:

ONE BROKEN DOWNLOAD MUST NEVER STOP THE QUEUE.

Every worker should effectively behave like:

try:
process_job()
except Exception:
log_exception()
notify_user()
finally:
cleanup()
queue.task_done()

One failed job must immediately give capacity to the next job.

The second invariant:

A BROKEN SOURCE MUST NEVER STOP THE BOT.

Anna's Archive dying must be invisible except for a slower answer from Libgen.

DEPENDENCIES

Keep requirements minimal.

Expected dependencies:

python-telegram-bot
httpx
python-dotenv

Optional, only for Cloudflare-hosted mirrors:

curl_cffi

Do not add an ORM.

Do not add a task queue.

Do not add Redis.

Do not add a web framework.

Do not add a headless browser.

Use Python's built-in sqlite3.

IMPLEMENTATION PROCESS

Do not immediately dump code.

First produce a short implementation plan and inspect the repository if code already exists.

Then implement the project.

Build the local cache path first and get it fully working before touching the network. Then add the DB columns, then the Libgen adapter, then the resolver and acquisition queue, then the Anna's Archive adapter.

After implementation:

run all tests
fix failures
run a local SQLite import test
run a mocked Kubo download test
run a mocked source search and resolve
verify three-worker concurrency
verify cleanup on failure
verify Telegram callback flow
verify unauthorized users are rejected
verify startup/shutdown
provide exact PowerShell commands to install and run it

The result should be a small, auditable self-hosted Telegram bot rather than an over-engineered distributed system.

The bot should treat Anna's Archive and Libgen as its book sources, and the local catalog plus the local Kubo node as its cache and its permanent record of what has been fetched.
