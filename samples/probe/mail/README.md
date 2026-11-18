# Invented mails for the mail extraction probe

Everything in this folder is invented. The ten `.eml` files were written for the probe
(`scripts/experiment_mail_extraction.py`, see `docs/32-mail-extraction-probe.md`) and are
not copies of real mail.

- The services (Cloudbox, Streamly, AppMarket, PayRelay, HostHill) do not exist.
- Senders are at `example.com` or `example.org`; the recipient is the invented
  `sam.example@example.org`.
- The card tails, the phone number (a number from the range reserved for fiction), the
  street, the order numbers and the amounts do not belong to anyone.

What the files cover:

| File | What it is |
| --- | --- |
| `01_cloudbox_rechnung_de.eml` | German plain-text monthly receipt (ISO-8859-1, quoted-printable) with a renewal date and VAT |
| `02_streamly_chek_ru.eml` | Russian receipt (base64) with a price per month in another currency |
| `03_payrelay_receipt_html.eml` | English HTML-only receipt from a payment processor; the body is mostly long tracking links |
| `04_streamly_trial_ends.eml` | "Trial ends on ..." notice; a stub plain part, an HTML part with invisible padding characters |
| `05_hosthill_payment_failed.eml` | "Payment failed" notice, plain and HTML parts and an attachment that is ignored |
| `06_streamly_ended_uk.eml` | Ukrainian "your subscription has ended" notice |
| `07_hosthill_price_change.eml` | Price-change notice with another number and date format |
| `08_streamly_newsletter_ad.eml` | Advertisement without prices |
| `09_appmarket_promo_prices.eml` | Advertisement that mentions prices |
| `10_payrelay_card_phone_me.eml` | Receipt with card tails, a phone number, the recipient's address and a postal address in the body |

The dates are written in different forms on purpose (`01.11.2026`, `3 Nov 2026`,
`12 ноября 2026`, `2026-11-22`, `01/01/2027`), and so are the numbers (`8,40`, `29.99`,
`399,00`, `1,234.56`).
