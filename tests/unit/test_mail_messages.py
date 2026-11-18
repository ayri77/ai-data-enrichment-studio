"""Reading .eml files: multipart, HTML-only, encodings, encoded headers, broken files."""

import base64
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from ai_data_enrichment.mail.messages import (
    MailMessage,
    read_eml_file,
    read_eml_folder,
    read_eml_folder_report,
)

HEAD = (
    "From: {sender}\n"
    "To: Sam Example <sam.example@example.org>\n"
    "Subject: {subject}\n"
    "Date: {date}\n"
    "Message-ID: <{mid}@example.com>\n"
)


def write(folder: Path, name: str, body: bytes | str, **fields: str) -> Path:
    head = HEAD.format(
        **{
            "sender": "Cloudbox Billing <billing@cloudbox.example.com>",
            "subject": "Receipt",
            "date": "Sun, 01 Nov 2026 06:12:44 +0100",
            "mid": name,
            **fields,
        }
    )
    data = head.encode() + (body.encode() if isinstance(body, str) else body)
    path = folder / name
    path.write_bytes(data)
    return path


def test_plain_mail_and_header_facts(tmp_path: Path) -> None:
    write(
        tmp_path,
        "a.eml",
        "MIME-Version: 1.0\nContent-Type: text/plain; charset=utf-8\n\nHello Sam.\n",
    )
    [message] = read_eml_folder(tmp_path)
    assert message == MailMessage(
        message_id="a.eml@example.com",
        received=datetime(2026, 11, 1, 6, 12, 44, tzinfo=timezone(timedelta(hours=1))),
        sender_name="Cloudbox Billing",
        sender_address="billing@cloudbox.example.com",
        recipient_address="sam.example@example.org",
        subject="Receipt",
        text="Hello Sam.",
        truncated=False,
        source="a.eml",
    )


def test_multipart_alternative_prefers_a_plain_part_with_content(tmp_path: Path) -> None:
    body = (
        "MIME-Version: 1.0\nContent-Type: multipart/alternative; boundary=XX\n\n"
        "--XX\nContent-Type: text/plain; charset=utf-8\n\n"
        "Plain version of the receipt for the monthly plan, paid in full.\n"
        "--XX\nContent-Type: text/html; charset=utf-8\n\n<p>HTML version, different.</p>\n"
        "--XX--\n"
    )
    write(tmp_path, "m.eml", body)
    assert read_eml_folder(tmp_path)[0].text.startswith("Plain version")


def test_html_only_mail_and_quoted_printable(tmp_path: Path) -> None:
    body = (
        "MIME-Version: 1.0\nContent-Type: text/html; charset=utf-8\n"
        "Content-Transfer-Encoding: quoted-printable\n\n"
        '<p>Total: =E2=82=AC9,99</p><p><a href=3D"https://t.example.com/c/abc">Pay=\n now</a></p>\n'
    )
    write(tmp_path, "h.eml", body)
    assert read_eml_folder(tmp_path)[0].text == "Total: €9,99\n\nPay now"


def test_base64_and_a_declared_charset(tmp_path: Path) -> None:
    russian = base64.encodebytes("Стоимость: 399,00 ₽ в месяц".encode()).decode()
    write(
        tmp_path,
        "r.eml",
        "MIME-Version: 1.0\nContent-Type: text/plain; charset=utf-8\n"
        f"Content-Transfer-Encoding: base64\n\n{russian}",
    )
    latin = "Verlängerung am 01.12.2026".encode("iso-8859-1")
    write(
        tmp_path,
        "l.eml",
        b"MIME-Version: 1.0\nContent-Type: text/plain; charset=iso-8859-1\n"
        b"Content-Transfer-Encoding: 8bit\n\n" + latin,
    )
    texts = {m.source: m.text for m in read_eml_folder(tmp_path)}
    assert texts["r.eml"] == "Стоимость: 399,00 ₽ в месяц"
    assert texts["l.eml"] == "Verlängerung am 01.12.2026"


def test_wrong_or_unknown_charset_falls_back(tmp_path: Path) -> None:
    # Declared us-ascii but holds UTF-8; a charset that does not exist; bytes that are
    # not UTF-8 at all (read as cp1252).
    write(
        tmp_path,
        "a.eml",
        b"MIME-Version: 1.0\nContent-Type: text/plain; charset=us-ascii\n"
        b"Content-Transfer-Encoding: 8bit\n\nZahlung f\xc3\xbcr Mai",
    )
    write(
        tmp_path,
        "b.eml",
        b"MIME-Version: 1.0\nContent-Type: text/plain; charset=no-such-charset\n"
        b"Content-Transfer-Encoding: 8bit\n\nZahlung f\xc3\xbcr Juni",
    )
    write(
        tmp_path,
        "c.eml",
        b"MIME-Version: 1.0\nContent-Type: text/plain; charset=utf-8\n"
        b"Content-Transfer-Encoding: 8bit\n\nPreis 5 \x80 im Monat",
    )
    texts = {m.source: m.text for m in read_eml_folder(tmp_path)}
    assert texts["a.eml"] == "Zahlung für Mai"
    assert texts["b.eml"] == "Zahlung für Juni"
    assert texts["c.eml"] == "Preis 5 € im Monat"  # cp1252 after utf-8 fails


def test_encoded_headers(tmp_path: Path) -> None:
    write(
        tmp_path,
        "e.eml",
        "\nBody text of the mail.\n",
        subject="=?UTF-8?B?0KfQtdC6IFN0cmVhbWx5?=",
        sender="=?utf-8?q?Cloudbox_B=C3=BCro?= <Billing@Cloudbox.Example.com>",
    )
    [message] = read_eml_folder(tmp_path)
    assert message.subject == "Чек Streamly"
    assert message.sender_name == "Cloudbox Büro"
    assert message.sender_address == "billing@cloudbox.example.com"


def test_attachments_are_ignored(tmp_path: Path) -> None:
    body = (
        "MIME-Version: 1.0\nContent-Type: multipart/mixed; boundary=YY\n\n"
        "--YY\nContent-Type: text/plain\n\nThe receipt text is in the body of this mail.\n"
        "--YY\nContent-Type: text/plain; name=notes.txt\n"
        "Content-Disposition: attachment; filename=notes.txt\n\nSECRET ATTACHMENT TEXT\n"
        "--YY\nContent-Type: application/pdf\nContent-Transfer-Encoding: base64\n"
        "Content-Disposition: attachment; filename=a.pdf\n\nJVBERi0xLjQK\n--YY--\n"
    )
    write(tmp_path, "x.eml", body)
    text = read_eml_folder(tmp_path)[0].text
    assert text == "The receipt text is in the body of this mail."


def test_a_broken_file_is_skipped_and_counted(tmp_path: Path) -> None:
    write(tmp_path, "good.eml", "\nA readable mail with some words in it.\n")
    (tmp_path / "empty.eml").write_bytes(b"")
    (tmp_path / "garbage.eml").write_bytes(bytes(range(256)) * 4)
    (tmp_path / "notes.txt").write_text("not a mail")
    (tmp_path / "folder.eml").mkdir()
    messages, skipped = read_eml_folder_report(tmp_path)
    assert [m.source for m in messages] == ["good.eml"]
    assert sorted(skipped) == ["empty.eml", "garbage.eml"]
    assert [m.source for m in read_eml_folder(tmp_path)] == ["good.eml"]


def test_sorted_by_date_then_name_and_undated_last(tmp_path: Path) -> None:
    write(tmp_path, "b.eml", "\nbody b\n", date="Mon, 02 Nov 2026 10:00:00 +0000")
    write(tmp_path, "a.eml", "\nbody a\n", date="Mon, 02 Nov 2026 10:00:00 +0000")
    write(tmp_path, "c.eml", "\nbody c\n", date="Sun, 01 Nov 2026 23:00:00 -0500")
    write(tmp_path, "z.eml", "\nbody z\n", date="not a date")
    write(tmp_path, "d.eml", "\nbody d\n", date="Mon, 02 Nov 2026 09:00:00 +0000")
    messages = read_eml_folder(tmp_path)
    # c.eml is 2026-11-02 04:00 UTC, d.eml 09:00, a.eml and b.eml 10:00 (name decides).
    assert [m.source for m in messages] == ["c.eml", "d.eml", "a.eml", "b.eml", "z.eml"]
    assert messages[-1].received is None
    assert all(m.received is None or m.received.tzinfo is not None for m in messages)


def test_a_date_without_a_zone_is_read_as_utc(tmp_path: Path) -> None:
    write(tmp_path, "n.eml", "\nbody\n", date="Mon, 02 Nov 2026 10:00:00 -0000")
    assert read_eml_folder(tmp_path)[0].received == datetime(2026, 11, 2, 10, tzinfo=UTC)


def test_recipient_falls_back_to_delivered_to(tmp_path: Path) -> None:
    path = tmp_path / "d.eml"
    path.write_text(
        "From: A <a@example.com>\nDelivered-To: Sam.Example@example.org\nSubject: s\n\nbody text\n"
    )
    assert read_eml_file(path).recipient_address == "sam.example@example.org"


def test_max_chars_is_passed_on(tmp_path: Path) -> None:
    write(tmp_path, "t.eml", "\n" + "\n".join(f"line {i} " + "x" * 30 for i in range(30)))
    [message] = read_eml_folder(tmp_path, max_chars=200)
    assert message.truncated and len(message.text) <= 200
