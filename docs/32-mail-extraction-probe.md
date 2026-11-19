# Mail extraction probe

`scripts/experiment_mail_extraction.py` is a measurement script. It is **not part of the
application**: nothing in `src/` outside its own modules calls it, it has no interface and
no server route of its own, and it adds no capability to the planner. (The first step of
bringing mail into the application, a table with one row per e-mail, is described in "In
the application: a table from e-mails (step 1)" at the end of this note.) Its messages come from `.eml`
files or, as an option, from the person's own Gmail mailbox, read-only (see "Reading from
Gmail").
It exists to answer one question before any application code is written around it.

## Why it exists

A table can be built from e-mails: one row per message, columns read from what the
message says. The first real task is a register of subscriptions and recurring payments,
built from receipt mails: what was paid, to whom, when, and when the next payment is due.
Such mails come in several languages and layouts, and they sit in the same mailbox as
advertisements. The probe tests whether a model can fill the columns from the text of a
message, with a quote from the message for every value, so that a person can check each
cell against the mail.

It follows the probe of the AI web search ([universal lookup probe](31-universal-lookup-probe.md)):
one request per item, a strict JSON schema, a strict reply reader, a rows file, a dry
run, a console summary and an estimated cost. The difference is that there is no web
search: the model may use only the text it is given.

## What the probe does, step by step

1. **Read.** Every `*.eml` file in a folder (or every message that Gmail returns, see
   "Reading from Gmail"; both go through the same parser) is read with the standard library
   (`email`).
   Multipart mails, HTML-only mails, quoted-printable and base64, the declared charset
   (with UTF-8 and Windows-1252 as fallbacks) and encoded headers are handled. Attachments
   are ignored. A file that cannot be read is skipped and counted. Messages are ordered
   by their `Date` header, then by file name. `--limit N` keeps the first N.
2. **Clean.** Of the plain-text part and the HTML part converted to text (`html.parser`),
   the one that says more after cleaning is used, the HTML part on a tie (the plain part of
   a receipt is often only a stub; see "First run on real mail"). `script`, `style` and `head` are dropped, block
   elements and table cells become line breaks, and the visible text of a link is kept
   while its target is dropped. Bare `http(s)://` addresses and markdown links are removed
   in plain text too. Invisible padding characters that marketing mails use (zero-width
   characters, U+034F, soft hyphen, braille blank, non-breaking spaces) are removed or
   turned into normal spaces, spaces are collapsed and blank lines are limited to one. The
   body is cut at `--max-chars` (default 6000), at the end of a line or a word when one is
   near the limit, and the message is then
   counted as truncated.
3. **Mask.** Card tails, phone numbers and the mailbox owner's own address are replaced by
   `[card]`, `[phone]` and `[me]` (see below). `--no-mask` skips this step.
4. **Ask.** One request per message to the Responses API (`gpt-6-luna`, effort `low`, the
   web lookup's defaults), with no tools and a strict JSON schema. The reply has
   `applies` (is this message what the task asks about, or for instance an advertisement)
   and, for every target column, a `value` and a `quote`, the shortest passage copied
   from the text that carries the value. `PROMPT_VERSION` is `1`; it is part of
   `configuration_id` (`openai-text-extraction-p1-<model>-<effort>`).
5. **Read the reply.** The reader is strict and **every mark is computed by code, never
   reported by the model** (see "The marks").
6. **Write.** `rows.json`, `summary.json` and `register.xlsx` in
   `artifacts/mail-extraction/<timestamp>/` (or `--out-dir`). `artifacts/` is ignored by
   git.

Exactly one of `--eml-dir PATH` or `--gmail-query QUERY` names the source.

The columns are given with `--target`, repeated: `"Header:kind"` with kind `text`,
`number`, `date` or `yes_no`, or `"Header:one_of:a|b|c"` for a choice. A `one_of` answer
can only be one of the listed options.

## What leaves the machine

For every message that is asked, **one request goes to OpenAI** (the Responses API) with:

- the subject, the sender (name and address) and the date of the message, as lines at the
  top;
- the cleaned and masked body of the message, up to `--max-chars` characters;
- the task sentence and the column headers and kinds.

Nothing else is sent: no attachments, no other headers, no recipient address, no links
from the body. Requests are made with `store: false`.

**What masking covers**

- Card tails, replaced by `[card]`: `•••• 0504`, `**** **** **** 0504`, `xxxx 0504`
  (the card brand word before it stays), `Visa-6464`, `ending in 1234`, `ending with
  1234`, `endet auf 1234`, and the same wording in Russian and Ukrainian.
- Phone numbers, replaced by `[phone]`: a `+` followed by 9 to 15 digits that may be
  separated by single spaces or dashes.
- The mailbox owner's address, replaced by `[me]`: the recipient of the message and the
  recipient that is most common in the folder, compared without regard to case.

**What masking does not cover.** It is a small filter made of patterns, not a privacy
guarantee. It does not try to find names, postal addresses, account or tax numbers, phone
numbers without a leading `+` or written with brackets or dots, card numbers written in
another way, or anything else personal that a mail may say. Amounts, dates and order
numbers stay on purpose: the table is built from them. The sender's address and name are
sent as they are. Read the output of `--dry-run` for a few of your own messages before a
real run, and do not point the script at mail that must not leave your machine.

Everything after masking works on the masked text: the request, the check of the quotes
and the output. The files in `artifacts/` contain quotes taken from the masked text, so
they hold the placeholders and not the masked parts, but they still hold what the quotes
say. Do not commit or share them unreviewed.

The key is read from `OPENAI_API_KEY` (an ignored `.env` or the shell) and is never
written anywhere. `--dry-run` needs no key.

## The marks

A mark says how a kept value relates to the passage the model quoted for it. It is
computed by code from the value, the quote and the text that was sent.

A value is **dropped**, with a reason, when:

- it has no quote, or the quote is not found in the text that was sent after collapsing
  whitespace (`quote_not_in_text`; letter case is not compared, another form of a word is
  not the passage);
- a number cannot be read from its quote (`number_not_in_quote`);
- a date has neither its day nor its year in its quote (`date_not_in_quote`);
- a date is not a real `YYYY-MM-DD` date, or a choice is not one of the options
  (`invalid_value`).

A value that is kept has one of two marks:

| Kind | `literal` | `derived` |
| --- | --- | --- |
| number | always, once it is kept: the number can be read from the quote (`9,99`, `9.99`, `1.234,56`, `1,234.56`, `909,99`, with or without a currency sign, tolerance 0.005) | never |
| date | the year, the month and the day are all in the quote (day and year as digits; month as digits or as a month name or abbreviation in English, German, Russian or Ukrainian) | at least the day or the year is in the quote, the rest was worked out by the model |
| text | the value, ignoring case and spacing, is contained in the quote | otherwise |
| yes/no, choice | never | always |

What a mark does **not** say:

- A `literal` number is in the quoted passage of the text. It does not say that the
  passage is the amount that was paid, and not an old price, a total or a discount; the
  model chose the passage.
- A `derived` date was converted or completed by the model (for instance `12 декабря`
  with the year taken from the header). It was not checked against anything.
- A `literal` text value is in its quote; it does not say that it is the right thing to
  put in that column.
- A `derived` yes/no or choice is the model's reading of the quote.
- Nothing here says that the mail itself is genuine. A forged receipt gives the same marks
  as a real one.

The outcome per message is `FOUND` (at least one value kept), `NOTHING_FOUND` (the message
applies but no value was kept), `NOT_APPLICABLE` (the model says the message is not what
the task asks about; its values are ignored), or the code of a failure: `INVALID_RESPONSE`
(also used when the model refuses), `RESPONSE_INCOMPLETE`, `PROVIDER_ERROR`,
`SOURCE_ACCESS_DENIED`, `REQUEST_REFUSED`. These are the codes of the web lookup. The run
stops at the first `SOURCE_ACCESS_DENIED` or `REQUEST_REFUSED`, as the lookup probe does;
the others only mark their message.

## How to run it

```
uv run --env-file .env python -m scripts.experiment_mail_extraction \
  --eml-dir path/to/folder \
  --task "Payments and subscriptions: what was paid, or what changes for a subscription" \
  --target "Service:text" --target "Item:text" --target "Amount:number" \
  --target "Currency:text" --target "Paid on:date" --target "Period:text" \
  --target "Next renewal:date" \
  --target "Event:one_of:payment|renewal notice|trial ending|price change|payment failed|ended"
```

`--dry-run` prints the exact request for the first message and how many card tails, phone
numbers and own addresses masking replaced in it and in all messages, and sends nothing.
Add it to look first. Other options: `--limit`, `--model`, `--reasoning-effort`,
`--max-chars`, `--no-mask`, `--out-dir`, `--timeout` and the two `--price-*` options (the
defaults are the web lookup's prices for `gpt-6-luna`).

The folder `samples/probe/mail/` holds ten invented mails for trying it out: a German
receipt, a Russian one, an HTML-only receipt full of tracking links, a trial notice, a
failed payment, an ended subscription, a price change, two advertisements, a receipt with
card tails, a phone number and the recipient's address, and a mail with invisible padding.
Its `README.md` lists them. Nothing in it is real.

## Reading from Gmail

**What it is.** A second source of messages for the same probe: your own Gmail mailbox,
**read-only**, on your own machine. Nothing is hosted and no server of this project is
involved: you create your own project in Google Cloud, the script signs in to it in your
browser and reads the messages that a Gmail search returns. The only permission it asks for
is `https://www.googleapis.com/auth/gmail.readonly`; the code cannot ask for another one and
makes no request that changes anything (two read calls to the Gmail API, plus one that
reads the address of the mailbox, which is used for masking). An app in the "Testing"
status needs no verification by Google for up to 100 listed test users. The price is that
the consent, and with it the stored sign-in, expires after 7 days; then the script notices,
deletes the old token and asks you to sign in again.

The sign-in uses Google's own libraries, which are an optional extra and not part of the
base install: `uv sync --extra mail`, or `--extra mail` on `uv run`. Without it the script
stops with one sentence saying so.

**Setup, once**, in the [Google Cloud console](https://console.cloud.google.com/). The area
is called "Google Auth Platform" with the pages Branding, Audience, Data Access and
Clients; the labels in the console change from time to time, so look for the page by its
meaning if a name differs.

1. Create a project, or choose one of your own.
2. APIs & Services, Library: find "Gmail API" and enable it.
3. Google Auth Platform, Branding: give the app a name and a support e-mail address.
4. Audience: user type "External", publishing status "Testing". Add every person who will
   use it under "Test users". Only listed people can sign in.
5. Data Access: add the scope `https://www.googleapis.com/auth/gmail.readonly`. The console
   lists it as a restricted scope; that is expected.
6. Clients: create an OAuth client of type "Desktop app" and download its JSON file.
7. Keep that file outside the repository, or in the folder `credentials/` at its top, which
   git ignores (so does the name `client_secret*.json` anywhere). Point `--gmail-client PATH`
   or the environment variable `ENRICHMENT_GMAIL_CLIENT_FILE` at it. The script never copies
   it. If the file is inside the repository in a place git does not ignore, the script prints
   a warning. The file is needed only when a sign-in is needed.

   The variable can stand in `.env`; then every command needs `--env-file .env`, the dry run
   too. Write the path with forward slashes (`credentials/client_secret_….json`, relative to
   the folder you run from, or `C:/Users/…`). `uv` cannot read a value with single
   backslashes, quoted with `"` or not quoted: it stops reading the file at that line, with a
   warning, and every variable below it is lost too. Single quotes or doubled backslashes
   work as well.

**First run.** The browser opens (the address is printed too, in case it does not). Google
warns that the app is not verified. That is expected for an app in testing; continue only
because the project is your own. The consent screen shows read-only access to your mail.
After you agree, the script keeps the token in a file in your state folder, the same
folder as the usage file: `$XDG_STATE_HOME`, else `%LOCALAPPDATA%` on Windows, else
`~/.local/state`, then `ai-data-enrichment-studio/mail/gmail-token-<label>.json`. The file
is written for the owner only (mode 0600 where the system has it) and never inside the
working directory. `<label>` is `--gmail-account LABEL`, default `default`. The token file
also holds the client id and secret of your project, as Google's library writes it: treat it
as a secret.

If Gmail answers 401, or the stored sign-in cannot be renewed, the script says that the
sign-in is no longer accepted, deletes the token and exits; run it again to sign in. If
Gmail answers 403 for another reason than the rate, a new sign-in would not help: the
script keeps the token and says to check that the Gmail API is enabled in the project and
that the account is listed as a test user. A rate limit (429 or a 403 that says so) and
server errors are retried three times with growing pauses; then the script stops with a
short message that holds no message text. A message that is gone between listing and
reading (404) is skipped and counted.

**A second person**, for instance on the same machine: add them as a test user (step 4) and
let them run with another label, `--gmail-account second`, so that each keeps their own
token.

**What leaves the machine.** Towards OpenAI nothing changes: the cleaned and masked text
described under "What leaves the machine" above. Google gets only the search query and the
read requests, from your own machine. The address that Gmail reports for the mailbox is
added to the addresses that masking replaces by `[me]`; it is not written to any output.
`rows.json` and `summary.json` hold no message text; the summary records the source kind,
the query and how many messages were listed, read and skipped, and not the account.

`--save-eml DIR` also writes every message that was read as `<n>_<id>.eml` into `DIR`
(default: nothing is saved). **That stores real mail, unmasked, on your disk.** `DIR` is
refused inside the repository unless it is under `artifacts/` (ignored by git), and the
files are written for the owner only. Deleting them is your responsibility. Its purpose: a
later run with `--eml-dir DIR` repeats the measurement on exactly the same mails, without
Gmail and without a sign-in.

`--dry-run` with `--gmail-query` **does read from Gmail** (it has to, to show a message)
and sends nothing to OpenAI; the script says so.

**Taking access back.** `--gmail-sign-out` (with `--gmail-account` if you used a label)
deletes the local token and exits. It does not revoke anything at Google. To revoke, open
your Google Account, Security, the connections to third-party apps (the name of the page
changes from time to time), choose the app and remove its access. Deleting the Google Cloud
project ends it for good.

**Commands.**

```
# look first: reads 40 messages from Gmail, sends nothing to OpenAI
uv run --extra mail python -m scripts.experiment_mail_extraction \
  --gmail-query "category:purchases newer_than:180d" --limit 40 --dry-run \
  --gmail-client ~/secrets/client_secret.json \
  --task "Payments and subscriptions: what was paid, or what changes for a subscription" \
  --target "Service:text" --target "Amount:number" --target "Paid on:date"

# the subscriptions task, and keep the mails for a repeat
uv run --extra mail --env-file .env python -m scripts.experiment_mail_extraction \
  --gmail-query "category:purchases newer_than:180d" --limit 40 \
  --save-eml artifacts/mail-extraction/mails \
  --task "Payments and subscriptions: what was paid, or what changes for a subscription" \
  --target "Service:text" --target "Item:text" --target "Amount:number" \
  --target "Currency:text" --target "Paid on:date" --target "Period:text" \
  --target "Next renewal:date" \
  --target "Event:one_of:payment|renewal notice|trial ending|price change|payment failed|ended"

# the same mails again, without Gmail
uv run --env-file .env python -m scripts.experiment_mail_extraction \
  --eml-dir artifacts/mail-extraction/mails --task ... --target ...
```

With Gmail, `--limit` defaults to 40 and cannot be above 200. Gmail returns the newest
messages first, so the limit keeps the newest N that match; the messages are then ordered
by their `Date` header as with files.

## What the output holds

- `rows.json`: one entry per asked message: the file name, header facts (message id, date,
  sender name and address, subject), the outcome, the kept values with mark and quote,
  the dropped values with their reason (and what the model claimed), the masking counts,
  whether the message was cut, tokens and seconds. It does **not** hold the text of the
  message, and not the recipient's address.
- `summary.json`: the configuration, the task, the counts below and the prices used.
- `register.xlsx`, with three sheets:
  - `Register`: one row per message with outcome `FOUND`: `Received` (the date of the
    `Date` header, as written there), `From` (the sender's name, or the domain of the
    address when there is no name), `Subject`, then one column per target; numbers are
    numbers and dates are dates. Every filled cell has a note with its mark and its quote.
    A value that was not kept leaves its cell empty.
  - `Other`: the messages that are `NOT_APPLICABLE`, `NOTHING_FOUND` or failed: received,
    from, subject, outcome.
  - `Checks`: one line per kept or dropped value: source file, column, value, mark or
    reason, quote.

  Text from a mail never becomes a formula: a cell that starts with `=`, `+`, `-` or `@`
  is written as text.

The console summary lists: messages read, skipped and asked; outcomes by kind; values kept
by mark; values dropped by reason; masking counts; messages cut; tokens in and out;
seconds; and the estimated cost. The cost is an estimate from the price options; it was
not compared with a bill.

## What it cannot show

- Whether a kept value is **right**. The checks are about the quote, not about the truth:
  a value can be traceable and still be the wrong figure of the mail.
- Whether the model's choice of `applies` is right. Nothing compares it with a person's
  judgement.
- Whether a register built this way is complete. A mail the model calls not applicable is
  only listed on `Other`.
- Anything about mail that is not in the languages and layouts of the fixtures.

## First run on real mail (2026-11-18)

One run by the owner on his own mailbox: `--gmail-query "category:purchases newer_than:180d"`,
40 messages, the eight columns of the example command, `gpt-6-luna`, effort `low`, masking
on. A single run of one mailbox, not a measurement; no value was compared with a bank
statement.

What happened (evidence):

- **The Gmail flow worked at the first try**: consent in the browser, the token, the list,
  40 of 40 messages read and parsed.
- **The API accepted the request shape** in 40 of 40 requests. About 3 s per message,
  35,556 input and 9,188 output tokens in all, about 900 input tokens per message; the
  estimated cost of the whole run is under one cent at the web lookup's prices (not
  compared with the bill).
- **Outcomes**: 29 `FOUND`, 11 `NOT_APPLICABLE`. Ten of the eleven are advertisements,
  delivery notices and a request for a review. The eleventh is an order confirmation of a
  marketplace, which says itself that it is an acknowledgement and not yet a purchase;
  another shop's order confirmation was read as a payment. The task text does not say
  whether an order counts.
- **Values**: 146 kept (112 `literal`, 34 `derived`), 2 dropped. All 18 amounts are
  `literal`. Masking replaced 7 card tails, 8 own addresses and 1 phone number.
- **Five receipts of one sender had no amount and no item**, although the mail shows them.
  Its plain-text part only says to visit an address for the details; the receipt is in the
  HTML part, and the plain part was preferred because it "had real content". Changed after
  this run: the part that says more is used.
- **One purchase confirmation has no amount in its text**: it is in an attached PDF.
  Attachments are not read.
- **Both dropped values were right**: the model's quote differed from the text in the case
  of one letter in one, in the ending of a word in the other. Changed after this run:
  letter case is no longer compared; the other form is still dropped.
- **Text columns are not uniform**: the currency came as `€`, `EUR`, `Euro`, `$`, `USD`,
  `грн`, `грн.` and `UAH`; the period as "Monatlich", "в месяц", "3 Monate", a date range,
  and once as hours of use. A header can ask for a form ("Currency (ISO code)"), and a
  `one_of` column fixes the choices; neither was tried in this run.
- **"Amount" is the amount the mail names**, not always an amount paid: the price of an
  ended plan, the price after a trial and a charge that failed are in the same column. The
  `Event` column tells them apart.
- **The same notice arrived twice** (a trial ending, five days apart) and gave two equal
  rows. Nothing removes duplicates.
- **The cleaned text of real mails holds what masking does not cover**: the owner's name,
  a job title in a footer, a billing address.

## Not verified

- **Whether the values are right.** The run above was read row by row for consistency, not
  compared with a reference. A `literal` amount is in the mail; whether it was charged is
  another question.
- **The two changes made after the first run** (which part of a mail is used; letter case
  of a quote) were tested on invented text only; the 40 mails have not been run again.
- **Nothing was measured**: one mailbox, one run, 40 messages. The prices are the web
  lookup's and were not compared with the bill.
- **The masking** was not surveyed on real receipts beyond the counts above.
- **The Gmail flow beyond the first sign-in**: the refresh of the token, the 7-day expiry
  showing up as a refresh failure, a second account with another label, and the rate limit
  have not been seen. Tests use a fake session, a fake sign-in and stand-in modules.

## In the application: a table from e-mails (step 1)

What exists after this step: e-mails can become an **ordinary dataset**, one row per mail,
through the API. Nothing is extracted from them yet and there is no interface for it.

**The route.** `POST /api/mail-datasets?filename=<name>`, the raw bytes of the upload as
the body (no multipart), as for `POST /api/datasets`. It accepts one `.zip` of `.eml`
files or one `.eml` file, and answers 201 with what the workbook upload answers for a
dataset (`dataset_id`, `worksheets`) plus `"mail": {"read", "skipped", "truncated",
"masked": {"card", "phone", "me"}}`. `filename` in the answer is always `mail.xlsx`; the
name of the upload is only used to tell a `.zip` from an `.eml` and is not kept. The
dataset is registered exactly like an uploaded workbook (same lock, same limit of eight
datasets with the oldest dropped, same `preview` and `DELETE` routes). The preview answer
of such a dataset also has `"mail": true`.

**Switched on, and local only.** Off unless `ENRICHMENT_ALLOW_MAIL=1`
(`PlanRunSettings.allow_mail`), and then only on a local installation: the application must
have been made as the local launcher makes it (`local_browser_configuration`). The hosted
entry point never offers it, whatever the variable says. Otherwise the route answers 403
`mail_not_allowed` before it reads the body. `GET /api/limits` has `"mail": true|false`.

**The table.** One sheet, `Mail`, with the columns `Received`, `From`, `Subject`, `Text`
(`Text` last, so that a later step can clear that column in an exported result), one row
per mail in the order of the probe (date, then entry name; undated mails last).

- `Received` is a real date-time cell (UTC, no time zone, `yyyy-mm-dd hh:mm`); empty when the
  mail has no usable date.
- `From` is the sender's name, or the domain of the address when there is none; `Subject`
  is the subject.
- `Text` is exactly the masked document of the probe: `Subject:`, `From:` and `Date:` lines,
  then the cleaned body (at most 6,000 characters; `truncated` counts the mails cut).
  A later check of a quote runs against this cell and nothing else.
- Every text cell is a string cell, also when it starts with `=`, `+`, `-` or `@`;
  characters Excel cannot hold are removed and a cell is cut at Excel's limit.
- Masking is the probe's (`mail/masking.py`): card tails, phone numbers and the owner's own
  address (each mail's recipient and the most common recipient of the upload) are replaced
  in all three text columns. It finds nothing else: names, postal addresses and account
  numbers stay in the text.

**Bounds**, each with its own fixed error message (no name, address or text of a mail is
ever in one): an upload up to 32 MiB (`mail_upload_too_large`, 413); at most 500 `.eml`
entries in a zip (`mail_too_many`, 422; other entries are counted as skipped); at most 10 MiB
per mail (`mail_entry_too_large`, 413) and 64 MiB of mail in all (`mail_total_too_large`,
413), both checked from the sizes the archive declares before anything is read and again
while reading; a broken zip `invalid_mail_archive`; nothing readable `no_mail_found`;
another file type `invalid_mail_file_type`. An unreadable mail, or an entry that cannot be
opened, is only counted in `skipped`.

**The mail files are not written to disk; the table made of them is.** The upload is read
into memory, parsed there and dropped; nothing is extracted from the archive, so no entry
name can become a path. The generated workbook, which holds the cleaned and masked text of
every mail, is stored in the workspace's temporary folder like an uploaded one, and is
removed with the workspace. The
usage event `mail_import` holds numbers only (`read`, `skipped`, `truncated`, `masked`).

**Why the planner refuses such a table.** `POST /api/plans`, `POST /api/task-ideas` and
`POST /api/fallback-plans` answer 409 `mail_dataset_no_planner` ("A table built from
e-mails is not planned by the AI.") for it, before any other check and before any model is
asked. The first two send sample rows of the table to the planning model and the third the
values of its context columns; for this table those are pieces of mail text. The server
remembers which datasets are mail tables until they are dropped or deleted. Preview cells
are cut to 200 characters by the profiling limits (`ProfilingLimits`), so the preview shows
the start of a `Text` cell only; the whole cell is in the stored workbook.

**What does not exist yet:** extracting values from the mails, an interface for any of
this, Gmail in the application (the probe's Gmail source is still the probe's), and
attachments.

**Not verified:** nothing was run on real mail in the application; the fixtures are the
invented mails of `samples/probe/mail/`. The upload bounds were tested with small archives
that declare large sizes, not with an upload of 32 MiB of real mail. The server's own
access log, which a person starting the local launcher sees, shows the request line with
`filename=`, as it does for a workbook upload.

## In the application: the extraction capability (step 2)

**What exists.** The capability layer of the probe's extraction, built by the pattern of the AI
web search and tested offline with a fake model client.

- `capabilities/extraction.py`: `ExtractionRequest` (the task sentence, the name of the text
  column, up to 20 targets of the kinds `text`, `number`, `date`, `yes_no`, `one_of`, a choice
  with 2 to 20 distinct options) and `build_extraction_plan`: a plan of one step, the
  capability `text_extraction`, with the task as a literal text and the text column as the input
  `text`. The table must hold the text column as a text column; targets follow the rules the web
  search's targets follow (an existing column must fit the kind, a new name must not differ from
  an existing one only in letter case). The plan is checked with the application's own
  validator. A number is written as a number, a date as `YYYY-MM-DD` text.
- `extraction_registry(plan, columns)`: the registry for that plan, holding only the
  `text_extraction` contract derived from the plan's own task. A second step, a web lookup step
  or a planner capability next to it is refused (`ExtractionPlanError`); there is no mixed plan
  with extraction. The planner is never told about the capability: it is not offered, not in
  `BINDINGS`, not in `GET /api/capabilities` (neither as an entry nor as the category
  `from_text`).
- `TextExtractionHandler.execute`: the probe's call as a step of a plan. A found text gives a
  record with a value, a quote and a mark (`literal` or `derived`, computed by code) per slot.
  "The text is about the task but no value could be traced" is the answer `NOTHING_FOUND`,
  reported and kept like the web search's. "The text is not about the task" is a failed step
  with the code `NOT_APPLICABLE`: never a record and never kept, so such a text is asked again by
  a later run. A row without text fails with `MISSING_VALUE`. The probe is unchanged.
- `build_live_executor(..., text_extraction=ExtractionOptions(...))` binds the handler under the
  source `openai_text_extraction`, with the same budget of one request per row and the same
  keeping of answers; a registry that holds the capability is refused at assembly without the
  options. The assembly binds nothing else for such a registry.
- `registry_for_plan(plan, columns, offered)` picks the registry from the shape of a plan
  (`plan_shape` now also says `extraction`).

**What is kept on disk when a run reuses answers.** The plan's value cache (`values.sqlite`)
holds, per text, a SHA-256 digest of the operation (task and text together, so the text itself
is not stored), the extracted values and their quotes in clear (a quote is a passage of up to
300 characters of the mail), and the capability and source ids. A text that is not about the
task is not stored. The result workbook, when sources are asked for, repeats the quote in the
note or on the `Sources` sheet of each written cell.

**Update (step 3).** The route and the run now exist; see the next section.

**Not decided here.** The text of a cell is sent as it is (a cell holds at most 32,767
characters; the body of a mail made by the application at most 6,000).

## In the application: planning and running it through the API (step 3)

**What exists.** `POST /api/extraction-plans` builds the one-step plan; validate, approve and
run are the ordinary routes, through the same worker and executor path, with the model reading
`ENRICHMENT_TEXT_EXTRACTION_MODEL`. Offered only on a local installation with
`ENRICHMENT_ALLOW_MAIL=1`, an OpenAI key and (live) the network gate; the hosted app answers 403.
Rows whose text is not about the task are settled (`not_applicable`) and do not stop a run from
being `completed`; they are asked again on a later run. The text is replaced in the report by a
marker with its length, is absent from logs, usage events, errors and the cache, and is emptied
from the result workbook of a mail table. Quotes stay in the report and the workbook notes, by
design. Contract and errors: [the plan/run API](22-m5-plan-run-api.md), "Values read out of mail
texts".

**What does not exist.** No interface. A plan of this shape that is supplied or returned by the
planner, or mixed with other steps, is refused.

**Not verified.** Everything above is tested offline with fake clients and invented mails. Nothing
has been run on real mail or a real model through the application, so the real reply quality, cost
and the time of a 40 to 100 row run are unmeasured.

## Acceptance run through the application

`scripts/mail_register_via_app.py` drives an **already running** local application through its
public API, so that the extraction can be accepted on real mail before there is an interface. It
is the first time real mail goes through the application: the probe above talks to OpenAI itself,
this script has the application do it (upload, plan, approve, run, poll, download). It is a
script, not part of the application, and uses the standard library for HTTP.

**Cost.** One request to OpenAI per mail, as in the probe (about 3 s per mail; the probe's first
run on 40 mails cost under a cent; nothing was measured through the application). Every run of the
script is a new upload and a new plan, so a second run asks every mail again, the "not about the
task" mails included (those are never kept, not even by a later run of the same plan).

**What it sends.** To the application, on the loopback address only (the script refuses another
`--url`): the mails as one zip with neutral entry names (`0001.eml`, ...); no file name, no path.
The application then sends the cleaned and masked text of each mail to OpenAI, as the probe does
(see "What leaves the machine"; the masking is the same and just as limited). `--allow-network` is
the run's consent and is required; without it nothing is sent anywhere.

**What it prints.** Counts, codes, statuses, the folder it wrote and fixed sentences; never a
subject, sender, file name, text, quote or value. A refusal of the application is printed as its
code and fixed message, only the code where the message may name the columns you typed
(`extraction_plan_invalid`). It writes into `--out` (default
`artifacts/mail-extraction-app/<UTC timestamp>/`, ignored by git; a folder inside the repository
that git does not ignore is refused):

- `register.xlsx`: the result workbook of the application (the table, with the text column
  emptied and the target columns filled, notes with the quotes);
- `report.json`: the application's audit report (rows, values with quote and mark, counts);
- `summary.json`: numbers only (mails read, skipped and cut, masked counts, rows by outcome,
  values by mark, values dropped by reason, requests, tokens, job status, seconds).

`register.xlsx` and `report.json` hold quotes from your mails: do not commit or share them.

**Steps**, from the repository root, in two terminals.

1. **`.env`** (ignored by git; names only, put your own values):

   ```
   OPENAI_API_KEY=<your-key>
   ENRICHMENT_ALLOW_MAIL=1
   ENRICHMENT_MAX_RUN_ROWS=<at least the number of mails; the default is 20>
   ENRICHMENT_MAX_TIMEOUT_SECONDS=<seconds; the default 300 is about 100 mails at 3 s>
   ```

   The network gate is the launcher's `--allow-network` below (or `ENRICHMENT_ALLOW_NETWORK=1`).
   The two limits are read when the application starts. If you add a path to `.env`, write it
   with forward slashes: `uv` stops reading the file at a value with single backslashes (see
   "Reading from Gmail"). No path is needed for this run.

2. **Mails as files**, if you have none yet. The probe saves them; `--dry-run` reads from Gmail
   and saves, and sends nothing to OpenAI (it prints the request for the first mail on your
   terminal, text included; the probe's own options are described above):

   POSIX shell:

   ```
   uv run --locked --extra mail python -m scripts.experiment_mail_extraction \
     --gmail-query "category:purchases newer_than:180d" --limit 40 --dry-run \
     --save-eml artifacts/mail-extraction/mails \
     --task "x" --target "Amount:number"
   ```

   PowerShell (the same with a backtick at the end of a line):

   ```
   uv run --locked --extra mail python -m scripts.experiment_mail_extraction `
     --gmail-query "category:purchases newer_than:180d" --limit 40 --dry-run `
     --save-eml artifacts/mail-extraction/mails `
     --task "x" --target "Amount:number"
   ```

   Or point `--eml-dir` at a folder of `.eml` files you already have.

3. **Terminal 1: the application** (the repository's documented command, on 127.0.0.1:8010):

   ```
   uv run --locked --env-file .env python -m ai_data_enrichment.local_server --allow-network
   ```

   It is the same on Windows and POSIX. The application keeps the table made of your mails, with
   their cleaned and masked text, in its temporary workspace until you stop it; stop it (Ctrl+C)
   when you are done.

4. **Terminal 2: the script**, with an invented task and columns (replace them with your own):

   POSIX shell:

   ```
   uv run --locked python -m scripts.mail_register_via_app \
     --eml-dir artifacts/mail-extraction/mails --allow-network \
     --task "Receipts: what was paid, to whom and when" \
     --target "Service:text" --target "Amount:number" --target "Currency:text" \
     --target "Paid on:date" \
     --target "Kind:one_of:payment|renewal notice|trial ending|price change|payment failed"
   ```

   PowerShell:

   ```
   uv run --locked python -m scripts.mail_register_via_app `
     --eml-dir artifacts/mail-extraction/mails --allow-network `
     --task "Receipts: what was paid, to whom and when" `
     --target "Service:text" --target "Amount:number" --target "Currency:text" `
     --target "Paid on:date" `
     --target "Kind:one_of:payment|renewal notice|trial ending|price change|payment failed"
   ```

   The script does not read `.env` and holds no key: the key belongs to the application.

**Options.** `--url` (default `http://127.0.0.1:8010`, loopback only), `--out`, `--limit N`
(the first N files **in file name order**, which for files saved by the probe from Gmail are the N
newest; the probe orders by the `Date` header before it applies its `--limit`, the script does not
parse the mails), `--timeout SECONDS` for the whole wait
(default: the server's longest run, from `GET /api/limits`, plus 30 s; when it passes, the script
asks the run to cancel). `--target` has the probe's syntax.

**Before anything is uploaded** the script reads `GET /api/limits` and stops, with exit code 2 and
one sentence naming the setting, when mail tables are not offered (`ENRICHMENT_ALLOW_MAIL=1`),
when extraction is not offered (an OpenAI key, or the network gate: it reads the open
`GET /api/provider-settings` to say which), or when the server's row limit is below the number of
mails (`ENRICHMENT_MAX_RUN_ROWS`; or use `--limit`). Exit code 0 is a run that ended `completed`
or `needs_review`; 1 is a failed job or a refusal of the application; a server that is not this
application, a refused connection and an unexpected status each give one fixed sentence.

**Reading the result.** `needs_review` is the normal end of a run where some mail is about the
task but gave no traceable value (counted as a failed row in the progress line and as
`nothing_found` in the summary); mails that are not about the task are `not_applicable` and do
not stop a run from being `completed`.

**Verified and not.** The script is tested against the real application in the same process,
with a fake model client and the invented fixtures. It was also run once (2026-11-18) against the
application as a real server process on the loopback address, through its real HTTP transport,
with the same fake model client: ten fixture mails, three files written, the text column empty
in the workbook. For real mail and a real model see the next section.

## First run through the application (2026-11-19)

The owner ran the acceptance script on the 40 real mails of the probe (the saved `.eml` files),
with the task and the eight columns of the probe's second run. Numbers only; no mail is quoted.

**The run.** 40 of 40 mails read and asked, 169 s (about 4 s a mail), 39,635 input and 11,309
output tokens, job `completed`. 32 found, 8 not about the task, none failed. 175 values kept
(105 literal, 70 derived), 4 dropped (3 `quote_not_in_text`, 1 `number_not_in_quote`). The text
column of the result workbook is empty.

**Against the probe's second run, mail by mail.**

- Outcome: the same for 38 of 40. The two that differ were "found" in the probe and are "not
  about the task" here. By their senders and filled columns they look like two of the three rows
  the probe's second run should not have taken in (an inference; the mails were not reread).
  The boundary of the task is not stable from run to run.
- `Amount` and `Paid on`: no difference in any mail where both runs gave a value.
- The other columns move: `Billing cycle` is filled in only one of the two runs for 6 mails,
  the currency for 5; the wording of `Item` differs in 4.

**Is the text the same?** Checked offline, without a model: for all 40 mails the cell the
application reads is identical, character for character, to the document the probe sent. All 175
quotes kept by the application stand in that text, and all 186 quotes of the probe's run would
pass the application's check. The four dropped values are therefore the model's own (a quote it
altered), not an effect of the table or the workbook. **Which** values they were cannot be seen:
the report counts dropped values by reason and does not name the row or the column.

**Found by this run: the cut threw most of a long mail away.** Two mails were counted as cut,
yet no text was longer than 2,274 characters. The cut went back to the last line break before
the limit, and in both mails that was at character 290 of about 17,000, because the next line
alone was more than 8,000 characters long (a table that arrives as one line). The probe's second
run had the same loss. The cut now takes a line or word boundary only in the last fifth of the
allowance; on these two mails it keeps 5,977 and 5,997 characters. Neither run above was made
with the fix.

**Still not verified.** The values against real payments; a second run of the same plan (kept
answers); a run that reaches the time limit; more than 40 mails.
