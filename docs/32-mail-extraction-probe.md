# Mail extraction probe

`scripts/experiment_mail_extraction.py` is a measurement script. It is **not part of the
application**: nothing in `src/` outside its own modules calls it, it has no mailbox
connection, no interface and no server route, and it adds no capability to the planner.
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

1. **Read.** Every `*.eml` file in a folder is read with the standard library (`email`).
   Multipart mails, HTML-only mails, quoted-printable and base64, the declared charset
   (with UTF-8 and Windows-1252 as fallbacks) and encoded headers are handled. Attachments
   are ignored. A file that cannot be read is skipped and counted. Messages are ordered
   by their `Date` header, then by file name. `--limit N` keeps the first N.
2. **Clean.** The plain-text part is used when it has real content, otherwise the HTML part
   is converted to text (`html.parser`). `script`, `style` and `head` are dropped, block
   elements and table cells become line breaks, and the visible text of a link is kept
   while its target is dropped. Bare `http(s)://` addresses and markdown links are removed
   in plain text too. Invisible padding characters that marketing mails use (zero-width
   characters, U+034F, soft hyphen, braille blank, non-breaking spaces) are removed or
   turned into normal spaces, spaces are collapsed and blank lines are limited to one. The
   body is cut at `--max-chars` (default 6000) on a line boundary, and the message is then
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
  whitespace (`quote_not_in_text`; the comparison is case-sensitive);
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

## Not verified

- **No live request has been sent.** The request shape (a strict JSON schema with
  `string | null`, `number | null` and enum types, no tools, `store: false`) has been
  checked only against the code that builds it and against fake clients. Whether the API
  accepts it with `gpt-6-luna`, and how the model answers, is not known.
- **Nothing was measured on real mail.** The ten fixtures are invented. The token counts,
  the time and the cost per message are not measured; the prices are the web lookup's.
- **The masking was tried only on invented text.** Its patterns come from the forms named
  above, not from a survey of real receipts.
- **No mailbox connection exists yet.** The probe reads `.eml` files from a folder. A
  reader for a mailbox is a separate, later step.
